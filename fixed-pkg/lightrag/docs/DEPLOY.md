# LightRAG-EDP ARM 部署指南

## 一、文件结构

```
lightrag/
├── docker-compose.deploy.yml    # ARM 部署编排文件
├── Dockerfile                   # LightRAG 镜像构建
├── .env                         # 环境配置（已预配好，仅需改密钥）
├── data/                        # 持久化数据（自动创建）
│   ├── rag_storage/             # 知识库数据
│   └── inputs/                  # 文档输入目录
└── lightrag_webui/              # 前端源码（构建时编译）
```

## 二、快速启动

### 1. 拷贝到 ARM 服务器

```bash
rsync -av --exclude='.venv' --exclude='node_modules' --exclude='data' \
  /path/to/lightrag/ user@arm-server:/opt/lightrag/
```

### 2. 基本启动（LightRAG + Milvus）

```bash
cd /opt/lightrag
docker compose -f docker-compose.deploy.yml up -d
```

### 3. 含 Attu 管理界面

```bash
docker compose -f docker-compose.deploy.yml --profile full up -d
```

## 三、服务端口

| 服务 | 端口 | 说明 |
|---|---|---|
| LightRAG API | 9621 | 主服务 + Web UI |
| Milvus | 19530 | 向量数据库 |
| Milvus Health | 9091 | 健康检查 |
| MinIO Console | 9001 | 对象存储管理 |
| Attu | 3000 | Milvus 可视化管理（需 --profile full） |

访问：`http://<服务器IP>:9621/webui`

## 四、日常操作

```bash
# 查看状态
docker compose -f docker-compose.deploy.yml ps

# 查看日志
docker compose -f docker-compose.deploy.yml logs -f lightrag

# 重启
docker compose -f docker-compose.deploy.yml restart lightrag

# 停止
docker compose -f docker-compose.deploy.yml down

# 清空所有数据（含向量库和知识图谱）
docker compose -f docker-compose.deploy.yml down -v
```

## 五、上传文档

文档放入宿主机的 `./data/inputs/` 目录，然后在 Web UI 上传，或用 API：

```bash
# 上传 + S策略 + Profile
curl -X POST http://<服务器IP>:9621/documents/upload \
  -F "file=@report.docx" \
  -F 'chunking={"strategy":"structure_aware","params":{}}' \
  -F 'profile=week_report'
```

## 六、ARM 兼容性说明

所有镜像均支持 `linux/arm64`：
- `python:3.12-slim-bookworm` — 官方 ARM 镜像
- `milvusdb/milvus:v2.5.9` — 官方 ARM 支持
- `quay.io/coreos/etcd:v3.5.18` — 多架构
- `minio/minio` — 多架构
- `zilliz/attu` — 多架构

首次构建 LightRAG 镜像约需 5-10 分钟（含依赖安装），后续重启秒级。

## 七、.env 关键配置确认

部署前检查 `.env` 中：

```bash
# LLM（可用远程 API，无需本地 GPU）
LLM_BINDING=openai
LLM_BINDING_HOST=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash

# Embedding（可用远程 API）
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B

# 已在 compose 中自动设置的（无需改 .env）
# MILVUS_URI → docker-compose 自动设为 http://milvus:19530
```

## 八、故障排查

```bash
# Milvus 未就绪
docker compose -f docker-compose.deploy.yml logs milvus

# LightRAG 启动失败
docker compose -f docker-compose.deploy.yml logs lightrag

# 重置所有数据
docker compose -f docker-compose.deploy.yml down -v
```
