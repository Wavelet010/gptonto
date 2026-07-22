#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v podman &>/dev/null; then
  RUNTIME="podman-compose"
  command -v podman-compose &>/dev/null || RUNTIME="podman compose"
else
  RUNTIME="docker compose"
fi
$RUNTIME -f "$DIR/docker-compose.deploy.yml" ps
