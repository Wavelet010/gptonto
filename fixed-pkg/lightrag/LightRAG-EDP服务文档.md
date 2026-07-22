# LightRAG-EDP 企业文档解析服务文档

> **版本**：基于 LightRAG v1.5.5 二次开发  
> **核心定位**：面向中文企业文档的结构化知识库构建与智能检索服务  
> **最后更新**：2026-07-15

---

## 一、系统概述

### 1.1 什么是 LightRAG-EDP

LightRAG-EDP 在 LightRAG 基础上增加了 **Enterprise Document Parser（EDP）** 模块，专为中文企业文档（周报、研究报告、技术规范、Excel 数据表等）设计，提供：

- **结构感知分块（S策略）**：按文档标题层级树切分，保留章节关系
- **双文本体系**：`content`（原文，供图谱+LLM）与 `embedding_text`（检索增强文本，供向量化）彻底解耦
- **多源元数据提取**：文档级、标题级、LLM 级、正则级、人工标注级五类字段
- **元数据过滤检索**：8 种操作符（含 contains/IN/OR），AND/OR 逻辑组合，软降级兜底，Web UI 可视化配置
- **Parser Profile 系统**：YAML 驱动的文档解析配置，不同文档类型独立配置

### 1.2 核心设计原则

> - **content**：保存原始内容，是知识图谱构建、LLM 上下文和最终展示的唯一数据来源，不做语义压缩。
> - **embedding_text**：保存经过结构化增强后的检索文本，仅用于生成向量，不参与图谱构建。
> - **metadata**：多级元数据系统，支持精确过滤和降级检索。

```
文档 → S策略分块 → Metadata提取 → embedding_text生成
                ↘ content → 实体提取 → 知识图谱

查询: 用户Query → Metadata Filter → Embedding(Query) → Milvus ANN
      → ChunkID → 加载 content → Graph扩展 → LLM生成
```

---

## 二、系统架构

### 2.1 技术栈

| 层 | 技术 |
|---|---|
| **Web 框架** | FastAPI + Uvicorn |
| **前端** | React + Vite + Tailwind CSS |
| **向量存储** | Milvus 2.6 (Docker) |
| **图谱存储** | NetworkX (可切换 Neo4j) |
| **LLM** | DeepSeek V4 Flash (`openai` binding) |
| **Embedding** | Qwen3-Embedding-8B (SiliconFlow, 4096d) |
| **Rerank** | Qwen3-Reranker-8B (SiliconFlow) |
| **文档解析** | python-docx, openpyxl, pypdf, openpyxl, legacy extractors |
| **配置** | YAML Profile + .env 环境变量 |

### 2.2 模块结构

```
lightrag/
├── api/                    # FastAPI 服务
│   ├── routers/
│   │   ├── document_routes.py   # 文档上传/导入/管理 API
│   │   ├── query_routes.py      # 检索/问答 API
│   │   └── graph_routes.py      # 图谱查询 API
│   └── webui/                   # 编译后的前端静态文件
├── edp/                    # EDP 企业文档解析模块
│   ├── models.py               # Pydantic 数据模型
│   ├── profile_manager.py      # YAML Profile 管理器
│   ├── profiles/               # Profile 配置文件夹
│   │   ├── week_report.yml
│   │   └── oled_research_v1.yml
│   ├── chunk_strategy_s.py     # S策略：标题树切分
│   ├── heading_detector.py     # 打分制中文标题检测
│   ├── context_builder.py      # embedding_text 构建器
│   ├── metadata_builder.py     # 元数据提取器
│   ├── metadata_filter.py      # 元数据过滤（向后兼容）
│   ├── filter_builder.py       # JSON → 后端过滤器
│   └── fallback.py             # 分级降级策略
├── parser/                 # 文档解析
│   ├── routing.py              # 解析器路由 + chunk策略默认值
│   └── legacy/extractors.py    # DOCX/PDF/PPTX/XLSX 提取
├── kg/                     # 知识图谱存储
│   ├── milvus_impl.py          # Milvus 向量存储实现
│   └── ...
├── pipeline.py             # 文档处理管线（核心）
├── operate.py              # 检索/实体提取/查询逻辑
├── lightrag.py             # LightRAG 主类
└── base.py                 # 基础数据类型
```

---

## 三、快速开始

### 3.1 环境准备

```bash
# 1. 启动 Milvus
docker compose -f docker-compose.yml up -d

# 2. 配置 .env（参考第五节）

# 3. 启动服务
lightrag-server --host 127.0.0.1 --port 9621

# 4. 访问 Web UI
open http://127.0.0.1:9621/webui
```

### 3.2 第一条文档

**Web UI 方式**：上传对话框 → 选择分段策略 → (S策略选Profile) → 拖入文件

**API 方式**：
```bash
# S策略 + Profile
curl -X POST http://127.0.0.1:9621/documents/upload \
  -F "file=@report.docx" \
  -F 'chunking={"strategy":"structure_aware","params":{}}' \
  -F 'profile=week_report'

# Excel 按行切分
curl -X POST http://127.0.0.1:9621/documents/upload \
  -F "file=@data.xlsx" \
  -F 'chunking={"strategy":"recursive_character","params":{"separators":["\n"]}}'
```

### 3.3 检索查询

```bash
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "东海方向雷达信号分析",
    "mode": "hybrid",
    "metadata_filter": {"knowledge_type": "态势分析"},
    "filter_mode": "soft"
  }'
```

---

## 四、API 参考

### 4.1 文档管理

| 端点 | 方法 | 说明 |
|---|---|---|
| `/documents/upload` | POST | 上传文件（multipart），支持 `chunking` + `profile` form 字段 |
| `/documents/text` | POST | 插入单条文本 |
| `/documents/texts` | POST | 批量插入文本 |
| `/documents/import` | POST | 批量导入（指定 profile 必填） |
| `/documents/paginated` | POST | 分页列出文档 |
| `/documents/pipeline_status` | GET | 查看处理进度 |
| `/documents/delete_document` | DELETE | 删除指定文档 |
| `/documents/profiles` | GET | 列出可用 Parser Profile |
| `/documents/clear_cache` | POST | 清除 LLM 缓存 |
| `/documents/reprocess_failed` | POST | 重处理失败文档 |

### 4.2 检索

| 端点 | 方法 | 说明 |
|---|---|---|
| `/query` | POST | 检索问答 |
| `/query/stream` | POST | 流式检索问答 |

**QueryRequest 参数**：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 查询文本 |
| `mode` | string | `"hybrid"` | 检索模式：`naive`/`local`/`global`/`hybrid`/`mix`/`bypass` |
| `metadata_filter` | object | `null` | JSON 格式元数据过滤条件 |
| `filter_mode` | string | `"soft"` | 过滤严格度：`strict`/`soft` |
| `top_k` | int | 40 | 实体/关系检索数量 |
| `chunk_top_k` | int | 20 | 返回 LLM 的 chunk 数 |
| `only_need_context` | bool | `false` | 仅返回检索上下文，不生成回复 |
| `enable_rerank` | bool | `true` | 是否启用 Rerank |

### 4.3 元数据过滤（metadata_filter）

`metadata_filter` 是一个 JSON 对象，支持以下过滤方式：

#### 4.3.1 精确匹配

```json
// 字符串
{"knowledge_type": "态势分析"}

// 数字
{"section_depth": 2}

// 布尔
{"has_numeric_data": true}
```

#### 4.3.2 比较操作符

```json
// 不等于：{"field": {"ne": value}}
{"has_numeric_data": {"ne": true}}

// 大于等于：{"field": {"gte": value}}
{"section_depth": {"gte": 2}}

// 小于：{"field": {"lt": value}}
{"section_depth": {"lt": 3}}

// 范围（组合）：{"field": {"gte": min, "lte": max}}
{"section_depth": {"gte": 1, "lte": 3}}

// 包含（LIKE）：{"field": {"contains": "text"}}
{"heading_path": {"contains": "东海"}}
```

#### 4.3.3 多值 IN

```json
// 匹配多个值之一
{"knowledge_type": ["一、基本情况", "雷达对抗侦察"]}
```

#### 4.3.4 组合 AND

```json
// 多个字段同时匹配（隐式 AND）
{
  "knowledge_type": "态势分析",
  "has_numeric_data": true
}
```

#### 4.3.5 OR 逻辑（$or）

```json
// 简单 OR：两个条件满足其一
{"$or": [
  {"knowledge_type": "态势分析"},
  {"knowledge_type": "装备动态"}
]}

// 混合 AND + OR
{
  "has_numeric_data": true,
  "$or": [
    {"knowledge_type": "态势分析"},
    {"knowledge_type": "雷达对抗侦察"}
  ]
}
```

#### 4.3.6 过滤模式（filter_mode）

| 模式 | 说明 |
|---|---|
| `soft`（默认） | 过滤结果不足时自动降级兜底（strict → relax → global fallback） |
| `strict` | 严格匹配，没有结果就返回空 |

#### 4.3.7 完整 API 示例

```bash
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达信号特征分析",
    "mode": "naive",
    "metadata_filter": {
      "has_numeric_data": true,
      "section_depth": {"gte": 2},
      "$or": [
        {"knowledge_type": "态势分析"},
        {"knowledge_type": "雷达对抗侦察"}
      ]
    },
    "filter_mode": "soft"
  }'
```

### 4.4 检索模式说明

| 模式 | 说明 | 适用场景 |
|---|---|---|
| `naive` | 纯向量检索 | 事实查询、关键词搜索 |
| `local` | 实体→chunk 桥接 | 问"谁/什么/哪里" |
| `global` | 关系→chunk 桥接 | 问"A和B的关系" |
| `hybrid` | local + global | 综合问题 |
| `mix` | 图谱 + 向量混合 | 最全面 |
| `bypass` | 不走检索，直接问 LLM | 与文档无关的对话 |

### 4.4 上传端点参数

**`/documents/upload`** 的 form 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | file | 文档文件 |
| `chunking` | string (JSON) | 分段策略配置，如 `{"strategy":"structure_aware","params":{}}` |
| `profile` | string | EDP Parser Profile 名称（仅 S 策略有效） |

**`chunking` JSON 格式**：

```json
{
  "strategy": "fixed_token | recursive_character | semantic_vector | paragraph_semantic | structure_aware",
  "params": {
    "chunk_token_size": 800,
    "separators": ["\n"]
  }
}
```

---

## 五、配置指南

### 5.1 核心环境变量

#### LLM 配置

```bash
LLM_BINDING=openai
LLM_BINDING_HOST=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash
MAX_ASYNC_LLM=4              # 实体提取并发数
LLM_TIMEOUT=240
SUMMARY_LANGUAGE=Chinese      # 实体提取语言
```

#### Embedding 配置

```bash
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_DIM=4096
EMBEDDING_BATCH_NUM=32
EMBEDDING_TOKEN_LIMIT=8192
```

#### Rerank 配置

```bash
RERANK_BINDING=cohere
RERANK_MODEL=Qwen/Qwen3-Reranker-8B
RERANK_BINDING_HOST=https://api.siliconflow.cn/v1/rerank
RERANK_BY_DEFAULT=True
```

#### Milvus 配置

```bash
MILVUS_URI=http://localhost:19530
MILVUS_DB_NAME=lightrag
```

#### 文档解析配置

```bash
LIGHTRAG_PARSER=docx:native-teS;*:legacy-S     # DOCX 优先 native 解析(表格/图片)，其他用 legacy+S策略
EDP_DEFAULT_PROFILE=week_report                 # 默认 Profile
```

### 5.2 分段策略配置

```bash
CHUNK_SIZE=1200                 # F/R/V 策略默认 chunk 大小
CHUNK_S_SIZE=1024               # S 策略专属 chunk 大小
# HEADING_MAX_LEVEL=3           # 限制最大标题层级（注释=不限）
# HEADING_LEVELS={"chinese_h2_full": 3, "decimal_base": 1}  # 标题级别覆盖（JSON，全局默认）
```

### 5.3 实体提取控制

```bash
MAX_EXTRACTION_ENTITIES=20      # 每 chunk 最多提取实体数（默认40）
MAX_EXTRACTION_RECORDS=50       # 每 chunk 实体+关系总数上限（默认100）
MAX_GRAPH_NODES=1000            # 图谱节点上限
```

### 5.4 检索配置

```bash
TOP_K=40                        # 实体/关系检索数
CHUNK_TOP_K=20                  # 最终返回 chunk 数
COSINE_THRESHOLD=0.2            # 向量相似度阈值
RELATED_CHUNK_NUMBER=5          # 每实体/关系关联 chunk 数
```

---

## 六、EDP 企业文档解析

### 6.1 S 策略（Structure-Aware Chunking）

按文档标题层级树进行分块，保留章节父子关系。

**标题检测**：打分制多阶段检测（模式匹配 + 上下文特征 - 排除规则 ≥ 阈值70分）

| 标题模式 | Tag | 默认 Level | 示例 |
|---|---|---|---|
| 中文编号 | `chinese_h1` | 1 | `一、基本情况` |
| 章节条 | `chapter_section` | 1 | `第一章 总则` |
| 全角括号子标题 | `chinese_h2_full` | 2 | `（一）东海方向` |
| 半角括号子标题 | `chinese_h2_half` | 2 | `(1) 概述` |
| 数字编号 | `decimal_base` | 3→ | `1.` L3, `1.1` L4, `1.1.1` L5 |
| Markdown | `markdown` | #数 | `## 标题` = L2 |

**标题级别自定义**：在 Profile YAML 的 `heading_levels` 中覆盖：

```yaml
heading_levels:
  chinese_h2_full: 3    # （一）改为3级
  decimal_base: 1       # 1.改为1级（1.1=2级...）
```

### 6.2 双文本体系

每个 chunk 包含两个文本字段：

| 字段 | 用途 | 用于 Embedding | 用于图谱 | 用于 LLM |
|---|---|---|---|---|
| `content` | 原始文本 | ❌ | ✅ | ✅ |
| `embedding_text` | 检索增强文本 | ✅ | ❌ | ❌ |

**embedding_mode**（Profile `context_builder.mode`）：

| Mode | embedding_text 内容 | 适用场景 |
|---|---|---|
| `rich` | `doc_title \| heading_path \| content` | 全文向量检索（默认） |
| `compact` | 仅 `compact_fields` 指定字段 | 元数据精确检索 |
| `raw` | 等同于 `content` | 不使用增强 |

```yaml
context_builder:
  mode: compact
  compact_fields:
    - title
    - heading_path
    - knowledge_type
    - doc_type
    - summary
```

### 6.3 元数据系统

四类元数据来源（+ 人工标注）：

| 来源 | source 值 | 提取方式 | 适用字段 |
|---|---|---|---|
| **文档级** | `document` | LLM 对整个文档提取一次 | 标题、客户、项目名等 |
| **标题级** | `heading` | 自动从标题结构提取 | heading_path、section_depth、knowledge_type |
| **LLM级** | `llm` | LLM 对每个 chunk 提取 | 关键词、摘要 |
| **正则级** | `rule` | 正则匹配 | has_numeric_data 等布尔/匹配字段 |
| **人工标注** | `manual` | 空值占位，后续在 Milvus 手填 | doc_type、reviewed 等 |

查询时用 JSON 格式过滤：

```json
{"knowledge_type": "态势分析", "has_numeric_data": true}
{"target": ["P-8A", "RC-135V"], "section_depth": {"gte": 2}}
```

`filter_mode`：
- `strict`：严格匹配，结果不足不补
- `soft`（默认）：分级降级（strict → relax → global fallback）

---

## 七、Parser Profile

### 7.1 概念

Profile 是一个 YAML 文件，定义了某类文档的上下文构建和元数据提取策略。与分段策略解耦——可独立选择。

### 7.2 Week Report Profile（默认）

```yaml
profile_name: week_report

# 标题级别覆盖（可选）
heading_levels: {}

# 上下文构建
context_builder:
  enabled: true
  mode: rich               # rich | compact | raw
  compact_fields:          # compact 模式有效
    - title
    - heading_path
    - knowledge_type
    - doc_type
    - summary
  include_doc_title: true
  include_heading_path: true
  sep: " | "

# 元数据提取
metadata_builder:
  document_fields:         # LLM 对全文档提取一次
    - name: target
    - name: equipment
    - name: title
  heading_fields:          # 自动提取
    - name: heading_path
    - name: section_depth
    - name: knowledge_type
  llm_fields:              # LLM 对每个 chunk 提取
    - name: keywords
    - name: summary
  rule_fields:             # 正则提取
    - name: has_numeric_data
  manual_fields:           # 人工标注占位
    - name: doc_type
    - name: reviewed
```

### 7.3 Profile 管理

```bash
# 列出可用 Profile
curl http://127.0.0.1:9621/documents/profiles
# → ["oled_research_v1", "week_report"]

# 新增 Profile：在 lightrag/edp/profiles/ 下创建 .yml 文件，无需重启
```

---

## 八、分段策略总览

| 策略 | API 值 | 适用场景 | 关键参数 |
|---|---|---|---|
| Fixed Token | `fixed_token` | 通用文本 | `chunk_token_size`, `chunk_overlap_token_size` |
| Recursive Character | `recursive_character` | Excel行、结构化文本 | `chunk_token_size`, `separators` |
| Semantic Vector | `semantic_vector` | 语义边界清晰的文本 | `chunk_token_size`, `breakpoint_threshold_type` |
| Paragraph Semantic | `paragraph_semantic` | 段落结构文档 | `chunk_token_size`, `chunk_overlap_token_size` |
| **Structure Aware** | `structure_aware` | **带标题层级的中文文档** | `chunk_token_size` (CHUNK_S_SIZE) |

**默认策略**：由 `LIGHTRAG_PARSER` env 控制（当前：`docx:native-teS;*:legacy-S`，即 DOCX 优先 native 解析 + 表格+图片+公式，其他文件 legacy 解析 + S 策略）。

---

## 九、文档格式支持

| 格式 | 扩展名 | 解析引擎 | 表格 | 图片 | 公式 |
|---|---|---|---|---|---|
| Word | .docx | native (优先) / legacy | ✅ | ✅ | ✅ |
| PDF | .pdf | legacy (pypdf) | ❌ | ❌ | ❌ |
| PowerPoint | .pptx | legacy | - | - | - |
| Excel | .xlsx | legacy (openpyxl) | ✅ | - | - |
| Markdown | .md | legacy | - | - | - |
| 纯文本 | .txt, .json, .csv 等 | legacy | - | - | - |

> **注意**：`.xls`（旧格式）不支持，仅 `.xlsx`。PDF 图片/表格提取需 MinerU 等外部服务。

---

## 十、Web UI 功能

| 功能 | 说明 |
|---|---|
| **文档管理** | 上传、批量上传、扫描目录、删除、分页列表、处理状态 |
| **上传配置** | 分段策略选择器（5种）、S策略模板选择器、参数JSON输入（R/V策略） |
| **检索对话** | 多模式检索、流式输出、Rerank开关、对话历史 |
| **元数据过滤** | 可折叠条件列表，支持 8 种操作符（`= ≠ > ≥ < ≤ ≈ ∈`），AND/OR 逻辑，JSON 模式 |
| **知识图谱** | 可视化浏览、节点/关系查询 |
| **API 文档** | 内置 Swagger UI (`/docs`) |

### 10.1 元数据过滤 UI 使用

检索页面右侧「元数据过滤」面板（可折叠）：

1. 点击 **▸ 元数据过滤** 展开
2. 点击 **添加条件**，逐条输入：
   - **字段名** — 自由输入（如 `knowledge_type`）
   - **操作符** — 下拉选择（`=` `≠` `>` `≥` `<` `≤` `≈` `∈`）
   - **值** — 自由输入
3. 第二条条件起，显示 **AND / OR** 切换按钮
4. 所有改动实时生效，查询时自动带上过滤
5. 过滤模式支持 **软降级 (soft)** / **严格 (strict)**

| 操作符 | 含义 | 值示例 | 效果 |
|---|---|---|---|
| `=` | 等于 | `态势分析` | `key == "态势分析"` |
| `≠` | 不等于 | `态势分析` | `key != "态势分析"` |
| `>` | 大于 | `2` | `key > 2` |
| `≥` | 大于等于 | `2` | `key >= 2` |
| `<` | 小于 | `3` | `key < 3` |
| `≤` | 小于等于 | `3` | `key <= 3` |
| `≈` | 包含 | `东海` | `key like "%东海%"` |
| `∈` | 多值 | `P-8A, RC-135V` | `key in ["P-8A","RC-135V"]` | |

---

## 十一、常用操作示例

### 上传周报（S策略 + rich模式）

```bash
curl -X POST http://127.0.0.1:9621/documents/upload \
  -F "file=@周报.docx" \
  -F 'chunking={"strategy":"structure_aware","params":{}}' \
  -F 'profile=week_report'
```

### 上传Excel（按行切分）

```bash
curl -X POST http://127.0.0.1:9621/documents/upload \
  -F "file=@项目计划.xlsx" \
  -F 'chunking={"strategy":"recursive_character","params":{"separators":["\n"],"chunk_token_size":800}}'
```

### 元数据过滤检索

```bash
# 精确匹配
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达信号分析",
    "mode": "hybrid",
    "metadata_filter": {"knowledge_type": "雷达对抗侦察"},
    "chunk_top_k": 10
  }'

# 布尔 + 范围组合
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达信号分析",
    "mode": "hybrid",
    "metadata_filter": {"has_numeric_data": true, "section_depth": {"gte": 2}},
    "chunk_top_k": 10
  }'

# IN 多值
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "装备对比分析",
    "mode": "naive",
    "metadata_filter": {"knowledge_type": ["一、基本情况", "四、行动建议"]}
  }'

# contains 模糊匹配
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "东海方向侦察",
    "mode": "naive",
    "metadata_filter": {"heading_path": {"contains": "东海"}}
  }'

# OR 逻辑
curl -s -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "电磁态势",
    "mode": "naive",
    "metadata_filter": {"$or": [{"knowledge_type": "态势分析"}, {"knowledge_type": "装备动态"}]}
  }'

# 严格模式（不降级）
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达信号分析",
    "mode": "naive",
    "metadata_filter": {"knowledge_type": "态势分析"},
    "filter_mode": "strict"
  }'
```

### 查看文档 chunks（Milvus直查）

```python
from pymilvus import MilvusClient
client = MilvusClient(uri="http://localhost:19530", db_name="lightrag")
chunks = client.query(
    collection_name="chunks_qwen_qwen3_embedding_8b_4096d",
    filter='full_doc_id like "doc-xxx%"',
    output_fields=["id", "content", "metadata", "embedding_text"],
    limit=50,
)
```

### 人工标注 chunk 元数据

在 Attu (http://127.0.0.1:3000, Milvus地址 `milvus-standalone:19530`) 中直接编辑 chunk 字段。

---

## 十二、Attu 可视化工具

```bash
docker run -d --name attu --network milvus -p 3000:3000 zilliz/attu:latest
```

访问 `http://127.0.0.1:3000`，Milvus 地址填 `milvus-standalone:19530`。

---

## 十三、常见问题

**Q: S 策略和其他策略可以共存吗？**  
A: 可以。每个文档上传时独立选择策略。F/R/V 策略不走 EDP Profile，直接对原文做向量化。

**Q: compact 模式的 embedding_text 为什么看起来像摘要？**  
A: compact 模式的 `embedding_text` 由 `compact_fields` 完全控制。字段值来自 metadata（如 LLM 提取的 summary），不含原文。

**Q: 如何查看某个文档的切分效果？**  
A: 在 Attu 中查询 `full_doc_id like "doc-xxx%"`，查看 `content` 和 `embedding_text` 字段。或通过检索 API 的 `only_need_context: true` 参数。

**Q: 上传后多久能检索到？**  
A: 取决于文档大小和 LLM 调用次数。一篇 5000 字的中文周报约需 2-5 分钟（含 10 个 chunk 的实体提取 + embedding）。可通过 `/documents/pipeline_status` 查看进度。

**Q: Milvus 挂了数据会丢吗？**  
A: Milvus 数据存在 Docker volume 中。`docker compose down` 不会删除数据卷。删除需手动 `docker compose down -v`。
