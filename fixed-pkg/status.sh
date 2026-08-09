#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"

if docker compose version &>/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
  COMPOSE="docker-compose"
else
  echo "错误: 未找到 docker compose"
  exit 1
fi

$COMPOSE -f "$DIR/docker-compose.deploy.yml" ps
