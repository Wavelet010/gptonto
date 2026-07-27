#!/bin/bash
# LightRAG WebUI 暗色主题修复脚本
# 在 ARM 服务器的 fixed-pkg 目录下执行

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
WEBUI_DIR="$DIR/lightrag/lightrag/api/webui/assets"

if [ ! -d "$WEBUI_DIR" ]; then
    echo "错误: 找不到 $WEBUI_DIR"
    echo "请在 fixed-pkg 目录下执行此脚本"
    exit 1
fi

if ! command -v docker &>/dev/null && ! command -v podman &>/dev/null; then
    echo "错误: 未找到 docker 或 podman"
    exit 1
fi

shopt -s nullglob
WEBUI_JS=$(grep -l "bg-primary/5" "$WEBUI_DIR"/*.js 2>/dev/null || true)
if [ -z "$WEBUI_JS" ]; then
    echo "未找到 bg-primary/5，可能已经修复过了"
    exit 0
fi

echo "找到文件: $(basename "$WEBUI_JS")"

# 1. bg-primary/5 → bg-card border
sed -i.bak 's/bg-primary\/5 max-h-96 overflow-auto rounded p-1/bg-card max-h-96 overflow-auto rounded p-1 border/g' "$WEBUI_JS"
if grep -q "bg-card max-h-96" "$WEBUI_JS"; then
    echo "[1/3] bg-primary/5 → bg-card border  ✓"
else
    echo "[1/3] 警告: 替换未生效，CSS 类名可能已变化"
fi

# 2. text-amber-700 加 dark 变体（编译后 JS 用反引号包裹，不是引号）
sed -i.bak 's/text-amber-700`/text-amber-700 dark:text-amber-400`/g' "$WEBUI_JS"
if grep -q "text-amber-700 dark:text-amber-400" "$WEBUI_JS"; then
    echo "[2/3] text-amber-700 → +dark variant  ✓"
else
    echo "[2/3] 警告: 替换未生效，可能已修复或格式不匹配"
fi

# 清理备份文件
rm -f "$WEBUI_JS.bak"

# 3. 重启容器
echo "[3/3] 重启 lightrag..."
if command -v podman &>/dev/null; then
    podman compose -f "$DIR/docker-compose.deploy.yml" restart lightrag
else
    docker compose -f "$DIR/docker-compose.deploy.yml" restart lightrag
fi

echo ""
echo "修复完成！等待 10 秒后访问 WebUI 验证"
