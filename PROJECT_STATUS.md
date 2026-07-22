# LightRAG EDP ARM 离线部署 — 项目状态文档

## 项目概览

基于 LightRAG (HKUDS/LightRAG) 的企业文档解析器(EDP)，离线部署在 ARM64 服务器上，用于结构感知的文档分块、向量检索和知识图谱构建。

## 目录结构

```
/chenhs/rag/
├── fixed-pkg/                          # 部署包目录
│   ├── deploy.sh                       # 首次部署脚本
│   ├── docker-compose.deploy.yml       # 服务编排（5个容器）
│   ├── .env                            # 运行配置（LLM/Embedding/Rerank）
│   ├── update.sh                       # 增量更新脚本 (rsync + docker cp + restart)
│   ├── restart.sh / stop.sh / status.sh
│   ├── data/                           # 持久化数据（7个目录）
│   │   ├── rag_storage/                # LightRAG 数据（KV store JSON）
│   │   ├── inputs/                     # 上传文档存储
│   │   ├── milvus/                     # Milvus 内部数据
│   │   ├── etcd/                       # Milvus 元数据
│   │   ├── minio/                      # Milvus S3 存储
│   │   └── tiktoken/                   # tiktoken 离线缓存 (6.7MB)
│   ├── images/                         # Docker 镜像（5个 tar，总共 ~4.3GB）
│   │   ├── lightrag-base.tar (2.4G)    # 主服务镜像（Python + 前端 + 依赖）
│   │   ├── milvus.tar (1.5G)           # Milvus v2.5.9 向量数据库
│   │   ├── etcd.tar (41M)              # etcd v3.5.18 元数据存储
│   │   ├── minio.tar (161M)            # MinIO 对象存储
│   │   └── attu.tar (219M)             # Attu Milvus 管理界面
│   └── lightrag/                       # 源码目录（挂载到容器 /app/lightrag:ro）
│       ├── lightrag/                   # Python 包
│       │   ├── api/                    # FastAPI 服务
│       │   │   └── routers/document_routes.py  # 上传/文档/查询 API
│       │   ├── edp/                    # 企业文档解析器
│       │   │   ├── profiles/           # Parser Profile YAML 文件
│       │   │   ├── context_builder.py  # embedding_text 构建
│       │   │   ├── metadata_builder.py # 元数据提取
│       │   │   └── profile_manager.py  # Profile 加载管理
│       │   ├── kg/                     # 知识图谱存储
│       │   │   └── milvus_impl.py      # Milvus 向量存储实现
│       │   ├── pipeline.py             # 文档处理流水线
│       │   └── utils_pipeline.py       # 流水线工具（含 metadata carry-over）
│       ├── lightrag_webui/             # 前端 (React + Vite)
│       │   └── src/
│       │       ├── features/DocumentManager.tsx      # 文档管理页
│       │       └── components/documents/UploadDocumentsDialog.tsx  # 上传对话框
│       └── docker-entrypoint.sh        # 容器启动脚本
├── update-package.tar.gz (110K)        # LightRAG 增量更新包
├── openai-api-compatible-arm64-offline.tar.gz (9MB)  # Dify 插件离线包
└── PROJECT_STATUS.md                   # 本文件
```

## 服务架构

| 容器 | 镜像 | 端口 | 用途 |
|------|------|------|------|
| lightrag-edp | lightrag-base:latest | 9621 | FastAPI + WebUI |
| milvus-standalone | milvusdb/milvus:v2.5.9 | 19530, 9091 | 向量数据库 |
| milvus-etcd | quay.io/coreos/etcd:v3.5.18 | 2379 | Milvus 元数据 |
| milvus-minio | minio/minio:latest | 9000 | Milvus S3 存储 |
| attu | zilliz/attu:latest | 3000 | Milvus 可视化管理 |

## 已完成的修复和改动

### 1. 部署层面
- **镜像 RepoTag 修复**: attu.tar 和 etcd.tar 原打包无标签，已重新 save 为正确 tag
- **deploy.sh**: 增加镜像完整性检查、tiktoken 目录创建
- **docker-compose.deploy.yml**: 
  - Milvus/etcd/MinIO 数据改为本地 bind mount（`./data/xxx`），便于维护备份
  - 增加 `./data/tiktoken:/app/data/tiktoken` 挂载
  - Attu 修复 ARM 兼容：显式指定 `entrypoint: /bin/sh -c` 绕过 Node.js entrypoint bug
- **tiktoken 离线缓存**: 预下载 4 个 tokenizer 文件到 `data/tiktoken/`（6.7MB），解耦网络依赖

### 2. Profile 系统
- **ProfileManager 读取位置**: 
  - `/documents/profiles` API: 从挂载目录 `/app/lightrag/lightrag/edp/profiles/` 直接读 → 实时生效
  - `pipeline.py` 文档处理时: 原只读 site-packages 静态副本，已修复为也读挂载目录
- **Profile 更新**: 新增/修改 YAML → 立刻生效，无需重启
- **Profile 删除**: 删文件 → 重启容器（entrypoint 用 rm+cp 同步，会清理 site-packages 残留）

### 3. edp_profile 持久化
修复了 3 处 `edp_profile` 丢失的 bug：
1. `pipeline.py` full_docs 写入时漏掉 `edp_profile` → 已补
2. `pipeline.py` _initial_doc_status 元数据构建时漏掉 → 已补
3. `utils_pipeline.py` _DOC_STATUS_METADATA_CARRY_OVER_KEYS 不包含 `edp_profile` → 已补

现在上传时选的 profile 会正确存储在文档元数据中，API `/documents/paginated` 返回的 metadata 里可见。

### 4. 前端
- **策略/Profile 选择闪烁**: 用 CSS `hidden` 替代条件渲染保持 DOM 稳定
- **Profile 预加载**: 对话框打开时预取 profile 列表（不再选到 S 策略才加载）
- **Swagger 文档**: 上传接口描述已更新，包含 chunking/profile 参数说明

### 5. embedding_text 构建 (context_builder)
- **compact 模式**: 从 compact_fields 列表按顺序拼接字段值，空字段自动跳过
- **summary 缺失降级**: LLM 未能生成 summary 时，自动降级为 rich 模式 (`doc_title | heading_path | content`)
- **LLM 提取失败日志**: 从 DEBUG 提升到 WARNING，便于排查
- **表格 chunk**: 走独立路径，直接用 content 作为 embedding_text

### 6. S 策略段落拆分修复 v2 (chunk_strategy_s + heading_detector)

#### 6a. 标题检测鲁棒性 (heading_detector.py)
按约定 5 种标题类型，每种都容忍格式变体：
| Tag | 约定格式 | 修改后正则 | 接受的变体 |
|-----|---------|-----------|-----------|
| chinese_h1 | `一、` | `^([一二三...]+)[、.．]` | `一、` `一.` `一．` |
| chinese_h2_full | `（一）` | `^[\(（]([一二三...]+)[\)）]` | `（一）` `(一)` — 全角/半角括号，中文数字 |
| chinese_h2_half | `(1)` | `^[\(（](\d+)[\)）]` | `(1)` `（1）` — 全角/半角括号，阿拉伯数字 |
| chapter_section | `第一章` | 不变 | `第一章` `第1章` |
| decimal | `1.` | `^(\d+(?:\.\d+)*)[.．、\s]+` | `1.` `1．` `1、` `1空格` |

关键：chinese_h2_full 与 chinese_h2_half 按**数字类型**（中文 vs 阿拉伯）区分，不再因括号全角/半角导致混淆。

#### 6b. leading text 提取修复 (chunk_strategy_s.py)
- **发现的 bug**: `_find_child_offset_in_parent` 用 cleaned text（如 `材料合成`）搜索父文本，但父文本中是带编号前缀的原文（如 `（一）材料合成`）→ 中文标题 leading text 被丢弃
- **修复**: `_Heading` 增加 `full_text`，`_Section` 增加 `full_heading`，搜索时用完整标题文本定位
- `_relative_offset` 已删除，替换为 `_find_child_offset_in_parent`

#### 6c. 换行符全覆盖
- `_split_by_paragraph`: 预处理统一 `\r\n`(Windows) → `\n`，`\r`(旧Mac) → `\n`，然后按空行/单换行拆分

#### 6d. 无贪心合并
- 按用户要求回退贪心合并，保持简单段落→句子拆分（父 Section 召回已覆盖小片段场景）
- **修复文件**: `edp/chunk_strategy_s.py`, `edp/heading_detector.py`
- **新增测试**: `tests/edp/test_chunk_strategy_s.py` 新增 12 个测试用例

## 配置说明

### .env 关键配置
```bash
# LLM (OpenAI 兼容协议)
LLM_BINDING=openai
LLM_BINDING_HOST=https://api.deepseek.com/v1  # 或本地 LLM 地址
LLM_BINDING_API_KEY=sk-xxx                     # 本地 LLM 不需要真实 key，但不能留空
LLM_MODEL=deepseek-v4-flash

# Embedding (OpenAI 兼容协议)
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.siliconflow.cn/v1
EMBEDDING_BINDING_API_KEY=sk-xxx
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B

# Rerank (Cohere 兼容协议)
RERANK_BINDING=cohere
RERANK_BINDING_HOST=https://api.siliconflow.cn/v1/rerank  # 写到 /rerank
RERANK_BINDING_API_KEY=sk-xxx

# EDP 默认 Profile
EDP_DEFAULT_PROFILE=week_report
```

### 重要陷阱
- **API_KEY 不能留空**: 即使本地 LLM 不需要认证，也必须填占位值（如 `not-needed`），否则代码会崩
- **LLM/Embedding fallback 是 Ollama**: 如果 .env 没被读到，LLM_BINDING 默认 `ollama` → `localhost:11434` → 报 Ollama 连接错误
- **.env 改完要重启**: `docker compose restart lightrag`
- **.env 权限必须 ≥ 644**: 容器内进程 UID=1000，600 权限会读不到

### chunk_token_size 配置优先级
1. API 参数 `chunking.params.chunk_token_size` (最高)
2. Profile YAML `chunk_token_size` 字段
3. `.env` `CHUNK_S_SIZE` 环境变量 (最低)

## 更新流程

### 不重建镜像（日常热更新）
```bash
# 改 Python/前端/profile/entrypoint/.env → 重启即可
# entrypoint 已通过 compose 覆盖指向 bind mount 路径，无需 docker cp
docker compose -f docker-compose.deploy.yml restart lightrag

# 改 docker-compose 配置 → 重建容器
docker compose -f docker-compose.deploy.yml up -d
```

### 需要重建镜像的情况
只有改动需要固化到镜像中供新部署使用时才重建：
```bash
docker build -f Dockerfile -t lightrag-base:latest .
docker save lightrag-base:latest -o images/lightrag-base.tar
```

### Profile 特殊说明
- 新增/修改 → 立刻生效（ProfileManager 直接从挂载目录读）
- 删除 → 重启容器（entrypoint 的 rm+cp 会清理 site-packages 残留）

## 数据持久化

所有数据在 `fixed-pkg/data/` 下：
```bash
# 备份
tar -czf backup.tar.gz data/

# 恢复
tar -xzf backup.tar.gz
```

- `docker compose restart` → 数据不丢
- `docker compose down && up` → 数据不丢（bind mount）
- `docker compose down -v` → 数据不丢（bind mount，-v 只删 named volume）
- 删除 `data/` 目录 → 数据彻底丢失

## Workspace 多知识库

设置 `.env` 中 `WORKSPACE=xxx`：
- KV 数据隔离（不同 workspace 用不同的工作目录子目录）
- Milvus 集合共享（同一 `MILVUS_DB_NAME` 下）
- 完全隔离需不同 `MILVUS_DB_NAME` 或不同 Milvus 实例

## 已知问题

1. **502 错误**: API 从服务端正常返回，浏览器端 502 通常来自中间网络层（SLB 超时/防火墙）。Stream 模式可缓解。
2. **Attu ARM 报错**: 已通过 compose 显式 entrypoint 修复
3. **表格 chunk embedding_text**: 不走 compact 拼接，直接用解析后的表格描述文本（设计如此）
4. **无网络环境首次部署**: tiktoken 缓存已内置到 `data/tiktoken/`，部署脚本自动创建目录

## 传给下一个 AI 的快速上手指南

```
我是 LightRAG EDP 项目，ARM64 离线部署，5 个 Docker 容器。
源码在 fixed-pkg/lightrag/，挂载到容器 /app/lightrag:ro。
启动时 entrypoint 把挂载源码同步到 site-packages。

日常改代码：改文件 → docker compose restart lightrag → 生效。
所有 fixed-pkg/lightrag/ 下的文件（Python、前端、Profile、entrypoint）均通过 bind mount 热更新，无例外。
Profile 改完立刻生效，不用重启。
.env 改完要重启。
不要随便重建镜像，docker restart 就够。

所有修复过的文件和原因见上面「已完成的修复和改动」。
配置文件位置和陷阱见上面「配置说明」。
数据在 fixed-pkg/data/ 下，重启/down/up 都不丢。
```
