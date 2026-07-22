# LightRAG-EDP ARM 离线部署任务

## 总体流程

```
ARM 测试机 (8.153.13.79, 能上网)
  │
  ├─ 1. 构建 lightrag-base 镜像
  ├─ 2. 拉取依赖镜像 (milvus, etcd, minio, attu)  
  ├─ 3. 导出所有镜像为 tar
  └─ 4. scp 到模板机
        │
        ▼
模板机 (断网)
  │
  ├─ 5. docker load < *.tar
  ├─ 6. docker compose up -d
  └─ 7. 后续更新: rsync 源码 + docker compose restart
```

---

## 阶段一：在 ARM 测试机上操作 (8.153.13.79)

### 1. 确认环境

```bash
docker version && docker compose version && uname -m
```

### 2. 构建 LightRAG 基础镜像

```bash
cd /chenhs/lightrag
docker build -f Dockerfile.arm -t lightrag-base:latest .
```

### 3. 拉取依赖镜像

```bash
docker pull milvusdb/milvus:v2.5.9
docker pull quay.io/coreos/etcd:v3.5.18
docker pull minio/minio:latest
docker pull zilliz/attu:latest
```

### 4. 导出所有镜像

```bash
cd /chenhs/lightrag
mkdir -p deploy-package/images

docker save lightrag-base:latest  -o deploy-package/images/lightrag-base.tar
docker save milvusdb/milvus:v2.5.9 -o deploy-package/images/milvus.tar
docker save quay.io/coreos/etcd:v3.5.18 -o deploy-package/images/etcd.tar
docker save minio/minio:latest    -o deploy-package/images/minio.tar
docker save zilliz/attu:latest    -o deploy-package/images/attu.tar

ls -lh deploy-package/images/
```

### 5. 复制部署文件

```bash
cp docker-compose.deploy.yml deploy-package/
cp .env deploy-package/
mkdir -p deploy-package/data/inputs
```

### 6. 写入离线部署脚本

```bash
cat > deploy-package/deploy.sh << 'EOF'
#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=== 加载镜像 ==="
for f in "$DIR/images"/*.tar; do docker load -i "$f"; done
echo "=== 启动 ==="
mkdir -p "$DIR/data/rag_storage" "$DIR/data/inputs"
docker compose -f "$DIR/docker-compose.deploy.yml" --profile full up -d
echo "完成: http://$(hostname -I | awk '{print $1}'):9621/webui"
EOF
chmod +x deploy-package/deploy.sh
```

### 7. 写入更新脚本

```bash
cat > deploy-package/update.sh << 'EOF'
#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/../lightrag"
echo "同步源码: $SRC → $DIR/lightrag/"
rsync -a --delete --exclude='__pycache__' "$SRC/" "$DIR/lightrag/" 2>/dev/null \
  || cp -r "$SRC/." "$DIR/lightrag/"
echo "重启..."
docker compose -f "$DIR/docker-compose.deploy.yml" restart lightrag
docker compose -f "$DIR/docker-compose.deploy.yml" logs --tail=5 lightrag
EOF
chmod +x deploy-package/update.sh
```

### 8. 打包传输

```bash
tar czf lightrag-deploy.tar.gz deploy-package/
echo "总大小: $(du -h lightrag-deploy.tar.gz | cut -f1)"
# scp 到模板机
scp lightrag-deploy.tar.gz root@<模板机IP>:/opt/
```

---

## 阶段二：在模板机上操作 (断网)

### 9. 解压

```bash
cd /opt
tar xzf lightrag-deploy.tar.gz
cd deploy-package
```

### 10. 部署

```bash
./deploy.sh
# 等待 30 秒，访问 http://<模板机IP>:9621/webui
```

### 11. 验证

```bash
curl -s http://localhost:9621/health | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])"
docker compose -f docker-compose.deploy.yml ps
```

---

## 后续代码更新（模板机断网也能做）

每次更新 LightRAG 源码时：

```bash
# 把新 lightrag/ 目录放到 deploy-package/ 旁边
cd /opt
rsync -a --delete ./new-lightrag/ ./deploy-package/lightrag/
cd deploy-package
./update.sh
```

就这么简单——更新源码不用动镜像，只重启容器。

---

## .env 关键配置确认

模板机上 `.env` 需确认：

```bash
LLM_BINDING=openai
LLM_BINDING_HOST=https://api.deepseek.com/v1    # 能访问的 LLM API
LLM_MODEL=deepseek-v4-flash
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.siliconflow.cn/v1  # 能访问的 Embedding API
MILVUS_URI=http://milvus:19530   # compose 内网地址，不用改
```

如果模板机连 LLM/Embedding API 也访问不了，需要单独部署本地模型。
