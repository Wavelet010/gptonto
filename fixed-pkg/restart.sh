#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v podman &>/dev/null; then
  RUNTIME="podman-compose"
  command -v podman-compose &>/dev/null || RUNTIME="podman compose"
else
  RUNTIME="docker compose"
fi
echo "重启 LightRAG..."
$RUNTIME -f "$DIR/docker-compose.deploy.yml" restart lightrag
echo "完成"
$RUNTIME -f "$DIR/docker-compose.deploy.yml" logs --tail=5 lightrag
