#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

# 自动检测 docker 还是 podman
if command -v podman &>/dev/null; then
  RUNTIME="podman"
  COMPOSE="podman-compose"
  command -v podman-compose &>/dev/null || COMPOSE="podman compose"
else
  RUNTIME="docker"
  COMPOSE="docker compose"
fi

echo "=== 检测到: $RUNTIME ==="
echo ""

# 加载镜像
echo "=== 1/3 加载镜像 ==="
for f in "$DIR/images"/*.tar; do
    echo "  $(basename "$f")..."
    $RUNTIME load -i "$f"
done

# 检查关键镜像是否已正确 tag（离线环境无法拉取）
echo ""
echo "=== 镜像检查 ==="
for _img in lightrag-base:latest milvusdb/milvus:v2.5.9 quay.io/coreos/etcd:v3.5.18 minio/minio:latest zilliz/attu:latest; do
    if $RUNTIME image inspect "$_img" >/dev/null 2>&1; then
        echo "  ✓ $_img"
    else
        echo "  ✗ $_img 未找到或未标记! 离线部署将失败"
        exit 1
    fi
done

# 创建目录
echo ""
echo "=== 2/3 创建数据目录 ==="
mkdir -p "$DIR/data/rag_storage" "$DIR/data/inputs" "$DIR/data/milvus" "$DIR/data/etcd" "$DIR/data/minio" "$DIR/data/tiktoken"

# 启动
echo ""
echo "=== 3/3 启动服务 ==="
cd "$DIR"
$COMPOSE -f docker-compose.deploy.yml up -d

echo ""
echo "========================================="
echo "  部署完成!"
echo "  Web UI: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<IP>'):9621/webui"
echo "  Attu:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<IP>'):3000"
echo ""
echo "  状态: $COMPOSE -f $DIR/docker-compose.deploy.yml ps"
echo "  日志: $COMPOSE -f $DIR/docker-compose.deploy.yml logs -f lightrag"
echo "========================================="
