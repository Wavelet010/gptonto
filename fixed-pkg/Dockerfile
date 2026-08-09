# LightRAG-EDP 多架构构建 (x86_64 / ARM64)
# 用法:
#   docker build -t lightrag-base:latest .
#   podman  build -t lightrag-base:latest .

# ---- 前端构建 (node + npm) ----
FROM docker.io/node:20-alpine AS frontend-builder
WORKDIR /app/lightrag_webui
COPY lightrag_webui/package.json lightrag_webui/package-lock.json ./
RUN npm install --legacy-peer-deps
COPY lightrag_webui/ ./
RUN npm run build

# ---- Python 环境 ----
FROM docker.io/python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl build-essential pkg-config gosu \
    && rm -rf /var/lib/apt/lists/*

# Rust (pymilvus 等包编译需要，自动适配当前架构)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# pip 安装（从本地源码安装所有依赖）
COPY pyproject.toml setup.py uv.lock ./
COPY lightrag/ ./lightrag/
RUN pip install --no-cache-dir ".[api,offline]"

# 前端静态文件
COPY --from=frontend-builder /app/lightrag/api/webui ./lightrag/api/webui

# 数据目录
RUN mkdir -p /app/data/rag_storage /app/data/inputs /app/data/prompts /app/data/tiktoken

# 非 root 用户
RUN groupadd -g 1000 lightrag 2>/dev/null || true \
    && useradd -u 1000 -g lightrag -m -d /home/lightrag -s /usr/sbin/nologin lightrag 2>/dev/null || true \
    && chown -R lightrag:lightrag /app /home/lightrag

ENV HOME=/home/lightrag \
    WORKING_DIR=/app/data/rag_storage \
    INPUT_DIR=/app/data/inputs \
    PROMPT_DIR=/app/data/prompts \
    TIKTOKEN_CACHE_DIR=/app/data/tiktoken

EXPOSE 9621

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "lightrag.api.lightrag_server"]
