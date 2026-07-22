#!/bin/bash
# ================================================================
# LightRAG-EDP 部署打包 (在目标 ARM 服务器上运行)
# 需要: Docker + 网络
# ================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="$SCRIPT_DIR/offline-deploy"
IMGDIR="$OUTDIR/images"

echo "========================================="
echo " LightRAG-EDP 部署打包 (ARM64)"
echo "========================================="

rm -rf "$OUTDIR"
mkdir -p "$IMGDIR" "$OUTDIR/data/inputs"

# ---- 1. 构建 LightRAG 基础镜像 ----
echo ""
echo ">>> [1/5] 构建 LightRAG 基础镜像 (含 Python 环境 + 依赖 + 前端)..."
docker build \
  -f "$SCRIPT_DIR/Dockerfile.base" \
  -t lightrag-base:latest \
  "$SCRIPT_DIR"
docker save lightrag-base:latest -o "$IMGDIR/lightrag-base.tar"
echo "    lightrag-base.tar  $(du -h "$IMGDIR/lightrag-base.tar" | cut -f1)"

# ---- 2-5. 拉取依赖 ----
echo ""
echo ">>> [2/5] 拉取 Milvus..."
docker pull milvusdb/milvus:v2.5.9
docker save milvusdb/milvus:v2.5.9 -o "$IMGDIR/milvus.tar"
echo "    milvus.tar  $(du -h "$IMGDIR/milvus.tar" | cut -f1)"

echo ""
echo ">>> [3/5] 拉取 etcd..."
docker pull quay.io/coreos/etcd:v3.5.18
docker save quay.io/coreos/etcd:v3.5.18 -o "$IMGDIR/etcd.tar"
echo "    etcd.tar  $(du -h "$IMGDIR/etcd.tar" | cut -f1)"

echo ""
echo ">>> [4/5] 拉取 MinIO..."
docker pull minio/minio:latest
docker save minio/minio:latest -o "$IMGDIR/minio.tar"
echo "    minio.tar  $(du -h "$IMGDIR/minio.tar" | cut -f1)"

echo ""
echo ">>> [5/5] 拉取 Attu..."
docker pull zilliz/attu:latest
docker save zilliz/attu:latest -o "$IMGDIR/attu.tar"
echo "    attu.tar  $(du -h "$IMGDIR/attu.tar" | cut -f1)"

# ---- 复制配置文件 ----
echo ""
echo ">>> 复制配置..."
cp "$SCRIPT_DIR/.env" "$OUTDIR/"
cp "$SCRIPT_DIR/docker-compose.deploy.yml" "$OUTDIR/"

# ---- 创建部署脚本 ----
cat > "$OUTDIR/deploy-offline.sh" << 'DEPLOYEOF'
#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== 1/3 加载镜像 ==="
for f in "$DIR/images"/*.tar; do
    echo "  $(basename "$f")..."
    docker load -i "$f"
done
echo "=== 2/3 创建数据目录 ==="
mkdir -p "$DIR/data/rag_storage" "$DIR/data/inputs"
echo "=== 3/3 启动服务 ==="
cd "$DIR"
docker compose -f docker-compose.deploy.yml --profile full up -d
echo ""
echo "部署完成! Web UI: http://$(hostname -I | awk '{print $1}'):9621/webui"
echo "Attu: http://$(hostname -I | awk '{print $1}'):3000"
DEPLOYEOF
chmod +x "$OUTDIR/deploy-offline.sh"

# ---- 创建更新脚本 ----
cat > "$OUTDIR/update.sh" << 'UPDATEEOF'
#!/bin/bash
# 增量更新：同步源码后重启容器（不动镜像）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$(dirname "$DIR")/lightrag}"
echo "同步源码: $SRC → $DIR/lightrag/"
rsync -av --delete --exclude='__pycache__' --exclude='*.pyc' "$SRC/" "$DIR/lightrag/"
echo "重启..."
cd "$DIR"
docker compose -f docker-compose.deploy.yml restart lightrag
docker compose -f docker-compose.deploy.yml logs --tail=5 lightrag
echo "更新完成"
UPDATEEOF
chmod +x "$OUTDIR/update.sh"

echo ""
echo "========================================="
echo "  打包完成!"
echo "  输出目录: $OUTDIR"
echo "  总大小:   $(du -sh "$OUTDIR" | cut -f1)"
echo ""
echo "  部署: cd $OUTDIR && ./deploy-offline.sh"
echo "  更新: cd $OUTDIR && ./update.sh /path/to/new/lightrag"
echo "========================================="
