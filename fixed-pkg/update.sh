#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$(dirname "$DIR")/lightrag}"
echo "同步: $SRC → $DIR/lightrag/"
rsync -a --delete --exclude='__pycache__' "$SRC/" "$DIR/lightrag/" 2>/dev/null \
  || cp -r "$SRC/." "$DIR/lightrag/"

# Also update the frontend & profiles inside the Docker image (runtime uses site-packages copy)
echo "更新前端和配置..."
if [ -d "$DIR/lightrag/lightrag/api/webui" ]; then
  docker cp "$DIR/lightrag/lightrag/api/webui/." lightrag-edp:/usr/local/lib/python3.12/site-packages/lightrag/api/webui/ 2>/dev/null && \
    echo "  前端文件已同步到容器" || echo "  跳过前端更新 (容器未运行)"
fi
if [ -d "$DIR/lightrag/lightrag/edp/profiles" ]; then
  docker exec lightrag-edp mkdir -p /usr/local/lib/python3.12/site-packages/lightrag/edp/profiles 2>/dev/null || true
  docker cp "$DIR/lightrag/lightrag/edp/profiles/." lightrag-edp:/usr/local/lib/python3.12/site-packages/lightrag/edp/profiles/ 2>/dev/null && \
    echo "  Profile 文件已同步到容器" || echo "  跳过 Profile 更新 (容器未运行)"
fi

echo "重启..."
docker compose -f "$DIR/docker-compose.deploy.yml" restart lightrag
docker compose -f "$DIR/docker-compose.deploy.yml" logs --tail=5 lightrag
