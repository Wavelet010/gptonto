#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

# 自动检测 docker compose 的调用方式
# Docker Compose v2: "docker compose"
# Docker Compose v1: "docker-compose"
if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
else
  echo "错误: 未找到 docker compose 或 docker-compose 命令"
  exit 1
fi

echo "=== 使用: $COMPOSE ==="
echo ""

# 加载离线镜像（如果有 images 目录且非空）
if ls "$DIR/images"/*.tar &>/dev/null 2>&1; then
  echo "=== 1/3 加载离线镜像 ==="
  for f in "$DIR/images"/*.tar; do
    echo "  $(basename "$f")..."
    docker load -i "$f"
  done
  echo ""
  OFFLINE=1
else
  echo "=== 1/3 跳过镜像加载（在线模式） ==="
  echo ""
  OFFLINE=0
fi

# 创建数据目录
echo "=== 2/3 创建数据目录 ==="
mkdir -p "$DIR/data/rag_storage" "$DIR/data/inputs" "$DIR/data/milvus" "$DIR/data/etcd" "$DIR/data/minio" "$DIR/data/tiktoken" "$DIR/data/milvus-tmp"
echo "  完成"

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
