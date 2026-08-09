# LightRAG-EDP

基于 [LightRAG](https://github.com/HKUDS/LightRAG) v1.5.5 二次开发，面向企业文档处理与情报分析场景。在原始 LightRAG 基础上增强了中文文档分段、元数据提取、实体类型定制、图片服务等功能。

---

## 目录

1. [核心增强](#核心增强)
2. [快速开始](#快速开始)
3. [分段策略](#分段策略)
4. [检索模式](#检索模式)
5. [知识图谱](#知识图谱)
6. [元数据与过滤](#元数据与过滤)
7. [图片服务](#图片服务)
8. [配置参考](#配置参考)
9. [API 速查](#api-速查)
10. [架构与运维](#架构与运维)
11. [注意事项](#注意事项)

---

## 核心增强

| 功能 | 说明 |
|------|------|
| **S 策略分段** | 基于标题树的结构化分段，支持中文编号格式 |
| **父子分段召回** | 检索时自动召回命中 chunk 所在 section 的兄弟 chunk |
| **EDP 元数据** | 多源元数据提取（文档/标题/LLM/文件名），写入 Milvus 动态字段 |
| **元数据过滤** | 基于 Milvus 原生标量表达式，支持 ==、IN、LIKE、$or 等操作符 |
| **Chunk 删除** | 单个/批量删除 chunk，清理 5 层存储 |
| **元数据同步** | Milvus ↔ JSON KV 双向同步（配合 Attu 编辑使用） |
| **图片服务** | 文档图片通过 `/assets/` 直接 HTTP 访问，chunk 中 `<drawing>` 标签自动转换 |
| **跳过图谱** | 上传时可跳过实体/关系提取，仅分段+向量化 |
| **实体类型定制** | 通过 YAML 配置文件自定义实体提取类型和示例 |
| **品牌定制** | WEBUI_TITLE / WEBUI_DESCRIPTION 可配置顶栏名称和浏览器标题 |

---

## 快速开始

### 在线部署

```bash
# 1. 构建镜像（或使用预构建的 lightrag-base）
docker build -t lightrag-base:latest .

# 2. 配置
cp .env.example .env
vi .env   # 必填: LLM_BINDING_API_KEY, EMBEDDING_BINDING_API_KEY

# 3. 启动
./deploy.sh
```

### 离线部署

```bash
# 将整个项目目录 + Docker 镜像 tar 包拷贝到离线服务器
# 首次部署:
docker load -i images/lightrag-base.tar
docker load -i images/milvus.tar
./deploy.sh

# 日常代码更新（无需重建镜像，源码通过 bind mount 挂载）:
tar -xzf lightrag-v13-update.tar.gz
./restart.sh
```

### 服务端口

| 服务 | 端口 | 地址 |
|------|------|------|
| LightRAG API | 9621 | http://localhost:9621 |
| WebUI | 9621 | http://localhost:9621/webui |
| Milvus | 19530 | 向量数据库 |
| Attu | 3000 | Milvus 可视化管理 |

---

## 分段策略

LightRAG 支持 5 种分段策略，通过 `LIGHTRAG_PARSER` 环境变量配置。策略标识为文件名 hint 或 `LIGHTRAG_PARSER` 规则中的字母代码。

### 策略一览

| 代码 | 策略 | 原理 | 适用场景 |
|:---:|------|------|------|
| `F` | **固定 Token** | 按 token 数切分，可设重叠 | 通用场景，无结构要求 |
| `R` | **递归字符** | 按分隔符层级递归切分（`\n\n` → `\n` → `。` → 空格 → 字符） | 混合中英文文档 |
| `V` | **语义向量** | 计算相邻句子向量的语义距离，在断点处切分 | 语义连贯性要求高的文档 |
| `P` | **段落语义** | 按段落切分后，语义相近的相邻段落合并 | 结构化文档、参考引用自动识别 |
| `S` | **结构感知** | 基于标题树的分段，每段带 heading_path 等元数据 | **推荐**：有标题层级的文档（中文编号、Markdown 标题） |

### S 策略详解

S 策略是本项目的核心分段增强，专为中文情报文档设计。

**标题检测** — 支持 5 类中文编号格式：

| 格式 | 示例 | 默认层级 | 对应 tag |
|------|------|:---:|------|
| Markdown ATX | `# 第一章` `## 1.1 概述` | 按 # 数量 | `markdown` |
| 中文编号大标题 | `一、概述` / `一. 概述` | 1 | `chinese_h1` |
| 中文括号子标题 | `（一）背景` / `(一) 背景` | 2 | `chinese_h2_full` |
| 阿拉伯括号子标题 | `(1) 数据来源` / `（1）数据来源` | 4 | `chinese_h2_half` |
| 数字多级编号 | `1. 概述` / `1.1 模块设计` | dots+3 | `decimal` |
| 章节条 | `第一章 总则` / `第3节 定义` | 自适应 | `chapter_section` |

**v13.7 增强**: 当一行同时命中 markdown 和中文编号时（如 docx 转换产生的 `## 一、题目`），中文编号模式的层级优先。只有纯 `#` 标题（无中文编号）才按 `#` 数量判定层级。

**分段逻辑**:
1. 构建标题树（Heading Tree）
2. DFS 遍历每个 section：如果内容 ≤ chunk_token_size，整个 section 作为一个 chunk
3. 如果 section 太大，递归处理子 section
4. 叶子 section 过大时回退到段落级 / 句子级切分
5. 图片/图表标签（`<drawing>`、`<table>`）自动合并入相邻正文 chunk

**每个 chunk 携带的元数据**:
- `section_id` — 所属的 section 标题
- `parent_section_id` — 父 section 的标题
- `heading_path` — 完整标题面包屑（如 `一、雷达对抗 → （一）截获记录 → 1. 信号参数`）
- `level` — 标题层级深度
- `heading_breadcrumb` — 面包屑文本

**S 策略配置**:
```bash
LIGHTRAG_PARSER=docx:native-teS;*:legacy-S   # docx用原生解析+S，其他纯文本+S
CHUNK_S_SIZE=1024         # S 策略 chunk token 数（默认 1200）
# EMBEDDING_MODE=rich     # rich=文档标题+标题路径+正文, compact=元数据, raw=纯正文
# HEADING_MAX_LEVEL=3     # 超过此层级视为正文
# HEADING_LEVELS={"chinese_h2_full": 3, "decimal_base": 1}  # 自定义各模式层级
```

### 父子分段召回

S 策略的 section 下可能有多个 chunk。开启父子分段召回后，检索命中一个 chunk 时，自动召回同一 section 的所有兄弟 chunk。

```
文档结构:
  一、雷达对抗              ← section
    chunk-001: 截获记录描述
    chunk-002: 信号参数表格
    chunk-003: 分析结论

检索命中 chunk-001 → 自动召回 chunk-002, chunk-003
```

```bash
SECTION_RECALL=true          # 启用
SECTION_RECALL_DEPTH=1       # 1=同section兄弟, 2=含父section兄弟
SECTION_RECALL_MAX=20        # 最大扩展chunk数
```

---

## 检索模式

通过 `/query` 或 `/query/data` 的 `mode` 参数指定。共 6 种模式。

| 模式 | 检索路径 | 图谱 | 向量 | 速度 | 适合场景 |
|------|----------|:---:|:---:|:---:|------|
| `naive` | 原始查询向量化 → 搜 chunks | ❌ | ✅ | 最快 | 事实问答、文档查找 |
| `local` | ll_keywords → 搜实体 → 遍历关系 → 拉关联 chunks | ✅ | ✅ | 中等 | "XX关联了哪些实体/事件" |
| `global` | hl_keywords → 搜关系 → 拉关联实体 | ✅ | ✅ | 中等 | "XX领域的全局关系模式" |
| `hybrid` | local + global 结果轮询合并 | ✅ | ✅ | 较慢 | 综合型查询 |
| `mix` | 向量搜 chunks + ll搜实体 + hl搜关系 | ✅ | ✅ | 较慢 | 最全面的关联分析 |
| `bypass` | 不检索，直接透传 | ❌ | ❌ | - | 纯 LLM 对话 |

### 模式选择建议

```
用户: "E-2K预警机的探测距离是多少"
  → naive: 向量检索直接找到参数描述

用户: "E-2K预警机参与了哪些事件"
  → local: 搜E-2K实体 → 遍历关系 → 找到"环太平洋军演2025"等事件实体

用户: "网电对抗态势综合分析"
  → mix: 向量chunks + 实体检索 + 关系检索，三路并行

不确定时: naive 先试，效果不够切 mix
```

### `keyword` — API 检索接口

```bash
# /query     — 返回 LLM 生成的完整回答
# /query/data — 返回原始检索结果（chunks + entities + relations），不调 LLM
# /query/stream — 流式回答

# 示例: 获取原始检索数据
curl -X POST http://localhost:9621/query/data \
  -H "Content-Type: application/json" \
  -d '{"query": "雷达对抗", "mode": "mix", "chunk_top_k": 10, "top_k": 20}'
```

---

## 知识图谱

### 提取控制

```bash
MAX_EXTRACTION_RECORDS=50    # 每chunk提取的实体+关系总数上限
MAX_EXTRACTION_ENTITIES=20   # 每chunk提取的实体数上限
FORCE_LLM_SUMMARY_ON_MERGE=4 # 同名实体积累N条描述后触发LLM合并摘要
MAX_ASYNC_LLM=4              # 并发LLM调用数
```

### 实体类型定制

通过 YAML 文件替换默认的实体类型（默认为 Person/Organization/Location 等通用类型）。

```bash
ENTITY_TYPE_PROMPT_FILE=edp_entity.yml
```

文件位于 `data/prompts/entity_type/` 目录，需包含：
- `entity_types_guidance` — 自定义实体类型定义
- `entity_extraction_json_examples` — JSON 格式示例（至少 1 条）

### 图谱 API

```bash
# 修改实体
curl -X POST http://localhost:9621/graph/entity/edit \
  -H "Content-Type: application/json" \
  -d '{"entity_name": "E-2K预警机", "updated_data": {"description": "新描述"}}'

# 删除实体（同时删除关联关系，chunk 不受影响）
curl -X DELETE http://localhost:9621/graph/entity/delete \
  -H "Content-Type: application/json" \
  -d '{"entity_name": "错误的实体名"}'

# 合并重名实体（E-2K → E-2K预警机，关系全部迁移）
curl -X POST http://localhost:9621/graph/entities/merge \
  -H "Content-Type: application/json" \
  -d '{"entities_to_merge": ["E-2K"], "target_name": "E-2K预警机"}'
```

### 跳过图谱

批量上传时，对于不需要图谱的低价值文档，跳过实体/关系提取：

```bash
# API 上传
curl -X POST http://localhost:9621/documents/upload \
  -F "file=@bulk_data.docx" -F "skip_graph=true"

# WebUI: 上传弹窗中勾选 "跳过图谱生成"
```

---

## 元数据与过滤

### 数据字段

EDP 元数据提取模块从多个来源提取字段并写入每个 chunk：

| 来源 | 说明 | 配置位置 |
|------|------|------|
| `document` | 文档级别（文档标题、类型） | `metadata_builder.document_fields` |
| `heading` | 标题路径、层级深度 | `metadata_builder.heading_fields` |
| `llm` | LLM 提取（摘要、关键词） | `metadata_builder.llm_fields` |
| `filename` | 从文件名正则提取（装备名、日期等） | `metadata_builder.filename_fields` |
| `rule` | 规则匹配（正则） | `metadata_builder.rule_fields` |
| `manual` | 手动配置 | `metadata_builder.manual_fields` |

配置在 `lightrag/edp/profiles/` 下的 YAML 文件中。`.env` 中通过 `EDP_DEFAULT_PROFILE` 指定默认 profile。

### 元数据过滤

在 `/query` 或 `/query/data` 请求中传 `metadata_filter`，系统将其转换为 Milvus 原生标量表达式。

```json
{
  "query": "装备性能分析",
  "metadata_filter": {"knowledge_type": "网电对抗态势"},
  "filter_mode": "strict"
}
```

支持的操作符：

| 操作 | 示例 |
|------|------|
| 精确匹配 | `{"entity_name": "E-2K预警机"}` |
| 多值 IN | `{"knowledge_type": ["基本情况", "网电对抗态势"]}` |
| 范围 | `{"created_at": {">": "2025-01-01"}}` |
| 模糊 LIKE | `{"doc_title": {"LIKE": "%周报%"}}` |
| 逻辑或 $or | `{"$or": [{"entity_name": "雷达"}, {"entity_name": "通信"}]}` |

`filter_mode`:
- `soft`（默认）— 过滤无结果时自动回退到无过滤查询
- `strict` — 过滤无结果时返回空

### 元数据同步

Milvus 元数据与 JSON KV 存储之间可通过 API 同步：

```bash
# 同步单个文档
curl -X POST http://localhost:9621/documents/sync-metadata/<doc_id>

# 批量同步所有文档
./sync-metadata.sh
```

---

## 图片服务

### 存储位置

文档解析时提取的图片存储在：

```
<input_dir>/__parsed__/<文件名>.docx.parsed/<文件名>.blocks.assets/image1.png
```

### 访问方式

图片通过 `/assets/` 端点直接 HTTP 访问：

```
http://<服务器IP>:9621/assets/<文件名>.blocks.assets/image1.png
```

仅放行 `*.blocks.assets/` 下的文件，JSON KV 和其他数据文件不暴露。

### chunk 中的图片标签

chunk 内容中保留 `<drawing>` 标签（v13.5 起不再丢弃），Dify 代码节点可自动将其转为 Markdown 图片链接。

---

## 配置参考

### .env 关键配置

```bash
# === 检索 ===
WEBUI_TITLE='网电知识库'                    # WebUI 标题
WEBUI_DESCRIPTION='电子对抗情报分析平台'      # WebUI 副标题

# === 解析策略 ===
LIGHTRAG_PARSER=docx:native-teS;*:legacy-S  # docx用原生+S，其他纯文本+S
CHUNK_S_SIZE=1024                            # S策略chunk大小
EDP_DEFAULT_PROFILE=week_report              # 默认EDP profile

# === 实体提取 ===
MAX_EXTRACTION_RECORDS=50
MAX_EXTRACTION_ENTITIES=20
FORCE_LLM_SUMMARY_ON_MERGE=4
# ENTITY_TYPE_PROMPT_FILE=edp_entity.yml     # 自定义实体类型

# === 父子分段召回 ===
SECTION_RECALL=true
SECTION_RECALL_DEPTH=1
SECTION_RECALL_MAX=20

# === 图谱展示 ===
MAX_GRAPH_NODES=1000
```

完整配置项见 `.env.example`。

### Docker Compose

```yaml
# lightrag 服务关键配置
volumes:
  - ./lightrag:/app/lightrag:ro     # 源码 bind mount（只读）
  - ./data/rag_storage:/app/data/rag_storage
  - ./data/inputs:/app/data/inputs
  - ./data/prompts/entity_type:/app/data/prompts/entity_type

environment:
  - MILVUS_URI=http://milvus:19530
  - MILVUS_DB_NAME=lightrag
  - SECTION_RECALL=true
```

---

## API 速查

```bash
# 检索
POST /query              # 完整问答（含LLM生成）
POST /query/data         # 原始检索数据（chunks/entities/relations）
POST /query/stream       # 流式问答

# 文档
POST /documents/upload   # 上传文件（支持 skip_graph）
DELETE /documents/{id}   # 删除文档
DELETE /documents/chunks/{id}  # 删除单个chunk
POST /documents/sync-metadata/{id}  # 同步元数据

# 图谱
POST /graph/entity/create   # 创建实体
POST /graph/entity/edit     # 修改实体（支持重命名+合并）
DELETE /graph/entity/delete # 删除实体
POST /graph/entities/merge  # 合并实体
POST /graph/relation/create # 创建关系
POST /graph/relation/edit   # 修改关系
GET  /graphs                 # 获取图数据（支持max_depth/max_nodes）
```

---

## 架构与运维

### Docker 架构

```
┌──────────────────────────────────────────┐
│              Docker Compose               │
│                                           │
│  ┌──────────┐ ┌─────────┐ ┌──────┐       │
│  │ LightRAG │ │ Milvus  │ │ Attu │       │
│  │  :9621   │ │  :19530 │ │ :3000│       │
│  └────┬─────┘ └───┬─────┘ └──────┘       │
│       │            │                       │
│       │   ┌────────┼────────┐             │
│       │   │   etcd │ MinIO  │             │
│       │   │  :2379 │ :9000  │             │
│       │   └────────┴────────┘             │
│       │                                    │
│  bind mount:                               │
│    ./lightrag → /app/lightrag:ro (源码)     │
│    ./data/rag_storage (JSON KV)            │
│    ./data/inputs (上传文件 + sidecar 图片)  │
└──────────────────────────────────────────┘
```

### 常用命令

```bash
./deploy.sh     # 首次部署（加载镜像 + 创建目录 + 启动）
./restart.sh    # 重启 LightRAG（代码更新后）
./stop.sh       # 停止所有服务
./status.sh     # 查看服务状态

# 查看日志
docker compose -f docker-compose.deploy.yml logs -f lightrag

# 重新构建镜像（加新依赖后）
docker build -t lightrag-base:latest .

# 导出/导入镜像（离线）
docker save -o lightrag-base.tar lightrag-base:latest
docker load -i lightrag-base.tar
```

### 文件结构

```
fixed-pkg/
├── docker-compose.deploy.yml  # 部署配置
├── Dockerfile                 # 多架构镜像构建 (x86/ARM)
├── .env.example               # 配置模板
├── deploy.sh                  # 部署脚本
├── restart.sh                 # 重启脚本
├── sync-metadata.sh           # 元数据同步脚本
├── lightrag/                  # 源码（bind mount）
│   └── lightrag/
│       ├── api/               # FastAPI 服务
│       │   └── routers/       # API 路由
│       ├── edp/               # EDP 增强模块
│       │   ├── chunk_strategy_s.py
│       │   ├── heading_detector.py
│       │   ├── metadata_builder.py
│       │   ├── filter_builder.py
│       │   ├── context_builder.py
│       │   └── profiles/
│       ├── kg/                # 存储后端
│       ├── parser/            # 文档解析器
│       ├── operate.py         # 查询/检索/提取逻辑
│       ├── lightrag.py        # 主入口
│       └── prompt.py          # Prompt 模板
├── data/                      # 运行时数据
│   ├── rag_storage/           # JSON KV 存储
│   ├── inputs/                # 上传文件 + sidecar
│   ├── prompts/entity_type/   # 自定义实体类型配置
│   ├── milvus/                # Milvus 数据
│   └── tiktoken/              # tiktoken 离线缓存
└── lightrag_webui/            # 前端源码
    └── src/
```

---

## 注意事项

1. **不要修改向量维度** — 首次启动后 `EMBEDDING_DIM` 不可变，切换需要清空 Milvus 重建
2. **不要切换 embedding 模型** — 不同模型的向量不可混用
3. **JSON KV 文件不要手动编辑** — 通过 API 或 Attu 操作，编辑后运行 sync-metadata
4. **skip_graph 的文档不参与图谱检索** — local/global/hybrid 模式对这些文档无效
5. **x86/ARM 镜像不通用** — Dockerfile 支持双架构，但需在目标架构上构建
6. **ENTITY_TYPE_PROMPT_FILE 必须包含 examples** — 缺少 `entity_extraction_json_examples` 会启动失败
7. **图谱实体精确匹配** — "E-2K" 和 "E-2K预警机" 是不同的实体，合并需用 `/graph/entities/merge`
8. **源码 bind mount 只读** — 修改`.env` 配置也需重启生效
