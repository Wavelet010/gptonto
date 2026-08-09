# LightRAG EDP 企业文档解析器 使用手册

## 一、基础原理

### 1.1 整体架构

```
文档 (DOCX/PDF/MD)
    │
    ▼
LightRAG Parser（解析文本，由 LIGHTRAG_PARSER 控制）
    │
    ▼
切分策略（F/R/V/P/S，由 API chunking 参数或 LIGHTRAG_PARSER 控制）
    │
    ├── S 策略专属 ──────────────────┐
    │  ① HeadingDetector（标题检测）   │
    │     打分制多阶段识别              │
    │  ② Tree Split（树形父子切分）     │
    │     DFS 遍历标题树                │
    │     section_id/parent_section_id  │
    └─────────────────────────────────┘
    │
    ▼
EDP 后处理（S 策略专属，由 Profile 控制）
    │
    ├── ③ Context Builder（上下文增强）
    │     embedding_text = 文档标题 + 标题路径 + 正文
    │
    └── ④ Metadata Builder（元数据提取）
         四来源：document（LLM）/ heading（自动）/ llm（LLM）/ rule（正则）
    │
    ▼
LightRAG Core（向量化 + 知识图谱 + 检索 + Rerank + LLM 回答）
```

### 1.2 父子分段原理（Tree Split，S 策略专属）

```
算法：DFS 遍历标题树

对于每个标题节点 N：
  N 的 token ≤ chunk_token_size？
    ├── 是 → 整个章节生成 1 个 Chunk
    │        section_id = N, parent_section_id = 父标题
    │
    └── 否 → N 有子标题吗？
           ├── 有 → 提取引导文字生成 section_lead chunk
           │        对每个子标题递归（parent_section_id = N）
           │
           └── 没有（叶子节点）
                → Paragraph → Sentence Fallback
                → 多个 chunk 共享同一个 section_id
```

**示例**：`chunk_token_size=1200`

```
文档结构                          切分结果
───────                          ────────
一、基本情况 (788t ≤ 1200) → Chunk0: section_id="基本情况", parent=(top)

二、网电对抗态势 (1763t > 1200) ★ 触发拆分
  ├─（一）雷达对抗 (670t)  → Chunk1: parent="网电对抗态势"
  ├─（二）通信对抗 (454t)  → Chunk2: parent="网电对抗态势"
  └─（三）导航对抗 (256t)  → Chunk3: parent="网电对抗态势"

三、态势研判 (923t ≤ 1200)  → Chunk4: parent=(top)
四、处置建议 (1016t ≤ 1200) → Chunk5: parent=(top)
```

### 1.3 每个 Chunk 携带的字段（S 策略）

```json
{
  "content":            "原始文本，用于 Graph 实体提取",
  "chunk_text":         "= content",
  "embedding_text":     "2026年第02周周报 | 网电对抗态势 → 雷达对抗 | 原始文本",
  "heading":            "雷达对抗",
  "heading_breadcrumb": "网电对抗态势 → 雷达对抗",
  "level":              2,
  "section_id":         "雷达对抗",
  "parent_section_id":  "网电对抗态势",
  "split_type":         "section | section_lead | paragraph | sentence",
  "tokens":             670,
  "metadata": {
    "heading_path":     "网电对抗态势 → 雷达对抗",
    "section_depth":    2,
    "knowledge_type":   "网电对抗态势",
    "target":           "P-8A, E-2D, CVN-70",
    "equipment":        "054A型驱逐舰",
    "keywords":         ["雷达对抗", "X波段", "Link-16"],
    "summary":          "本周雷达对抗信号增长23%"
  }
}
```

### 1.4 检索增强（S 策略）

```
查询命中 Chunk1 (section_id="雷达对抗", parent_section_id="网电对抗态势")
  → 按 parent_section_id 查找同级 chunk → Chunk2, Chunk3
  → 按 heading_path 向上定位完整上下文
  → 合并后进入 Graph Retrieval + LLM 生成
```

---

## 二、HeadingDetector 标题检测规则

### 2.1 支持的中文标题类型

| 类型 | 示例 | 默认 Level | 加分 |
|------|------|-----------|------|
| Markdown | `# 标题` `## 子标题` | 1-6 | +80 |
| 章节标记 | `第一章` `第3节` `第二条` | 1 | +70 |
| 中文编号 | `一、基本情况` `十二、总结` | 1 | +70 |
| 全角括号 | `（一）雷达对抗` | 2 | +70 |
| 半角括号 | `(1) X波段` `（2）配置` | 2 | +65 |
| 数字编号1级 | `1. 概述` | 2 | +60 |
| 数字编号2级 | `1.1 模块` | 3 | +60 |
| 数字编号3级 | `1.1.1 参数` | 4 | +60 |

### 2.2 上下文加分

| 特征 | 条件 | 加分 |
|------|------|------|
| 短行 | len < 80 | +15 |
| 无句末标点 | 行尾没有 。！？．. | +10 |
| 前后有空行 | 前后至少一行空白 | +10 |
| 下一行更长 | 后一行是正文 | +5 |

### 2.3 排除规则

| 条件 | 示例 | 减分 |
|------|------|------|
| 段落标签 | `第一段` `第二部分` | -60 |
| 图表编号 | `图1` `表2` `Figure 1` | -60 |
| 行尾冒号 | `活动规律：` | -30 |

> **判定**：总分 ≥ 70 → 标题，否则普通段落

---

## 三、配置文件

### 3.1 `.env` 环境变量

```bash
# ===== Parser 路由（控制解析引擎 + 默认切分策略）=====
LIGHTRAG_PARSER=docx:native-teS;md:native-S;pdf:legacy-S;doc:legacy-S;*:legacy-S
# 格式: <后缀>:<解析引擎>-<选项>
# 解析引擎: native(内嵌) / legacy(内嵌) / mineru(需Docker) / docling(需Docker)
# 选项: i=图片 t=表格 e=公式 S=结构感知切分 F=固定窗口 R=递归字符
#       V=语义向量 P=段落语义 !=跳过图谱

# ===== 默认 Profile（上传/扫描时的元数据配置）=====
EDP_DEFAULT_PROFILE=week_report

# ===== 语言 =====
SUMMARY_LANGUAGE=Chinese

# ===== Rerank（可选）=====
RERANK_BINDING=cohere
RERANK_MODEL=Qwen/Qwen3-Reranker-8B
RERANK_BINDING_HOST=https://api.siliconflow.cn/v1/rerank
RERANK_BINDING_API_KEY=sk-xxx
RERANK_BY_DEFAULT=true
```

### 3.2 Parser Profile（YAML）

路径：`lightrag/edp/profiles/<name>.yml`

```yaml
profile_name: my_profile               # 必填：唯一标识
description: "我的解析配置"             # 必填：描述说明
chunk_token_size: 800                  # chunk token 上限（越小父子拆分越细）

context_builder:                       # Context Builder 配置
  enabled: true                        # 是否生成 embedding_text
  include_doc_title: true              # embedding 中包含文档标题
  include_heading_path: true           # embedding 中包含标题路径
  sep: " | "                           # 分隔符

metadata_builder:                      # Metadata Builder 配置
  document_fields:                     # 文档级 LLM 提取（整篇调一次LLM）
    - name: field_name
      source: document
      type: string
      llm_prompt: "从本文档中提取xxx"

  heading_fields:                      # 标题自动提取（无需LLM）
    - name: heading_path
      source: heading
      type: string
      description: "标题面包屑路径"
    - name: section_depth
      source: heading
      type: number
    - name: section_id
      source: heading
      type: string
    - name: parent_section_id
      source: heading
      type: string

  llm_fields:                          # 逐 chunk LLM 提取（每个chunk调一次LLM）
    - name: keywords
      source: llm
      type: text
      llm_prompt: "提取3-5个关键词，返回JSON数组"

  rule_fields:                         # 正则提取（无需LLM）
    - name: has_numeric_data
      source: rule
      type: boolean
      rule_pattern: '\d+(\.\d+)?\s*(°|MHz|GHz|dB)'
```

### 3.3 内置 Profile

| Profile | 用途 | chunk_token_size |
|---------|------|-----------------|
| `oled_research_v1` | OLED 材料研究报告 | 1200 |
| `week_report` | 电磁态势周报 | 1024 |

---

## 四、API 接口

### 4.1 POST /documents/text — 文本导入

最灵活的导入方式，所有参数自由选择。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | ✅ | 文档文本内容 |
| `file_source` | string | ✅ | 文件名（用于溯源和去重），如 `report.md` |
| `chunking` | object | ❌ | 切分配置，不传用默认 |
| `chunking.strategy` | string | ❌ | `structure_aware` / `fixed_token` / `recursive_character` / `semantic_vector` / `paragraph_semantic` |
| `chunking.params` | object | ❌ | 策略参数，如 `{"chunk_token_size": 800}` |
| `profile` | string | ❌ | Profile 名称，如 `week_report`。不传则用 `EDP_DEFAULT_PROFILE` |

```bash
# 示例1：S策略 + 周报Profile（完整父子分段 + 全量元数据）
curl -X POST http://127.0.0.1:9621/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "# 标题\n正文内容...",
    "file_source": "report.md",
    "chunking": {"strategy": "structure_aware"},
    "profile": "week_report"
  }'

# 示例2：F策略 + 无Profile（固定窗口切分，无元数据）
curl -X POST http://127.0.0.1:9621/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "正文内容...",
    "file_source": "doc.txt",
    "chunking": {"strategy": "fixed_token", "params": {"chunk_token_size": 600}}
  }'

# 示例3：F策略 + 周报Profile（固定窗口 + LLM元数据。等后续支持）
curl -X POST http://127.0.0.1:9621/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "正文内容...",
    "file_source": "doc.txt",
    "chunking": {"strategy": "fixed_token"},
    "profile": "week_report"
  }'

# 示例4：全默认（env的LIGHTRAG_PARSER + EDP_DEFAULT_PROFILE）
curl -X POST http://127.0.0.1:9621/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "正文内容...",
    "file_source": "doc.txt"
  }'
```

**响应**

```json
{
  "status": "success",
  "message": "Text successfully received. Processing will continue in background.",
  "track_id": "insert_20260713_140425_578853c9"
}
```

### 4.2 POST /documents/import — 批量导入

一次导入多个文件，共用同一个 Profile 和切分策略。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `profile` | string | ✅ | Profile 名称 |
| `files` | string[] | ✅ | 文件路径列表（绝对路径或相对 inputs 目录） |
| `chunking` | object | ❌ | 切分配置，不传默认使用 S 策略 |
| `chunking.strategy` | string | ❌ | 同 `/documents/text` |
| `chunking.params` | object | ❌ | 同 `/documents/text` |

```bash
# 示例1：S + 周报Profile
curl -X POST http://127.0.0.1:9621/documents/import \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "week_report",
    "files": ["2026年第20周周报.docx", "2026年第21周周报.docx"]
  }'

# 示例2：R策略 + OLED Profile
curl -X POST http://127.0.0.1:9621/documents/import \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "oled_research_v1",
    "chunking": {"strategy": "recursive_character", "params": {"chunk_token_size": 500}},
    "files": ["oled_report.docx"]
  }'
```

**响应**

```json
{
  "status": "import_started",
  "message": "Importing 2 files with profile 'week_report'",
  "total_files": 2
}
```

### 4.3 POST /documents/upload — 单文件上传

上传一个文件。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | UploadFile | ✅ | 上传的文件 |
| `chunking` | string | ❌ | JSON，切分配置。不传则从 `LIGHTRAG_PARSER` 推断 |
| `profile` | string | ❌ | Profile 名称。**S 策略时必须传**，不传会返回 422 错误 |

```bash
# 最小上传（非S策略）
curl -F "file=@report.docx" http://127.0.0.1:9621/documents/upload

# S策略 + Profile（必须）
curl -F "file=@report.docx" \
  -F 'chunking={"strategy":"structure_aware","params":{}}' \
  -F "profile=week_report" \
  http://127.0.0.1:9621/documents/upload
```

### 4.4 POST /documents/scan — 扫描目录

扫描 `inputs/` 目录下的所有文件并导入。**切分策略和 Profile 均从 `.env` 读取（`LIGHTRAG_PARSER` + `EDP_DEFAULT_PROFILE`）。**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 无 | - | - | scan 接口本身不接受 chunking/profile 参数，全部依赖 `.env` |

```bash
# 1. 把文件放到 inputs/
cp *.docx inputs/

# 2. 扫描导入
curl -X POST http://127.0.0.1:9621/documents/scan
```

**响应**

```json
{
  "status": "scanning_started",
  "message": "Scanning process has been initiated in the background",
  "track_id": "scan_20260713_192812_f8e03d00"
}
```

### 4.5 POST /query — 查询

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 查询文本 |
| `mode` | string | ❌ | `mix`(默认) / `local` / `global` / `hybrid` / `naive` / `bypass` |
| `top_k` | int | ❌ | 返回实体/关系数量，默认40 |
| `chunk_top_k` | int | ❌ | 返回chunk数量，默认20 |
| `metadata_filter` | string | ❌ | **新** EDP 元数据过滤表达式。如 `knowledge_type=="网电对抗态势"` |
| `filter_mode` | string | ❌ | `soft`(默认，空则退化普通检索) / `strict` |

```bash
# 普通查询
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{"query": "雷达对抗的重点目标", "mode": "mix"}'

# 带元数据过滤
curl -X POST http://127.0.0.1:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达对抗态势",
    "mode": "mix",
    "metadata_filter": "knowledge_type==\"网电对抗态势\""
  }'
```

> ⚠️ `metadata_filter` 需要 Milvus 后端，当前 NanoVectorDB 会忽略过滤条件但不影响查询

### 4.6 支持的切分策略

| strategy 值 | 策略 | 说明 | EDP 后处理 |
|-------------|------|------|:--:|
| `structure_aware` | S | Tree Split 结构感知 + 父子分段 | ✅ |
| `fixed_token` | F | 固定 token 窗口（默认） | ❌ 后续支持 |
| `recursive_character` | R | 递归字符切分 | ❌ 后续支持 |
| `semantic_vector` | V | 语义向量切分 | ❌ 后续支持 |
| `paragraph_semantic` | P | 段落语义切分 | ❌ 后续支持 |

### 4.7 接口总览

| 方法 | 路径 | chunking参数 | profile参数 | 说明 |
|------|------|:--:|:--:|------|
| POST | `/documents/text` | ✅ 任意 | ✅ 可选 | 最灵活 |
| POST | `/documents/import` | ✅ 可选 | ✅ 必填 | 批量导入 |
| POST | `/documents/upload` | ✅ 可选(S策略必传) | ✅ S策略时必传 | 单文件(WebUI可选) |
| POST | `/documents/scan` | `.env` | `.env` | 目录扫描 |
| POST | `/query` | - | - | 查询（含metadata_filter） |
| POST | `/query/stream` | - | - | 流式查询 |
| GET | `/documents/` | - | - | 文档列表 |
| DELETE | `/documents/{id}` | - | - | 删除文档 |
| GET | `/health` | - | - | 健康检查 |
| GET | `/docs` | - | - | Swagger API 文档 |

---

## 五、WebUI

地址：`http://127.0.0.1:9621`

支持在线选择切分策略和 Profile（上传对话框中可选）。如需全局默认，可在 `.env` 中设置 `LIGHTRAG_PARSER` 和 `EDP_DEFAULT_PROFILE`。

| 功能 | 说明 |
|------|------|
| 📤 文档上传 | 拖拽/选择文件，自动解析 + 切分 + 元数据提取 |
| 🔍 知识库查询 | RAG 检索 + LLM 回答，支持 mode/stream 切换 |
| 📊 知识图谱 | 可视化实体关系图 |
| 📋 文档管理 | 查看已导入文档列表、状态 |
| ⚙️ 本地设置 | 调整 top_k、chunk_top_k 参数 |

> 需要精细控制（选切分策略/Profile/元数据过滤）用 API。

---

## 六、使用示例

### 场景1：批量处理周报（env 默认配置）

```bash
# .env
LIGHTRAG_PARSER=docx:native-teS;*:legacy-S
EDP_DEFAULT_PROFILE=week_report

# 操作
cp *.docx inputs/
lightrag-server &
curl -X POST http://127.0.0.1:9621/documents/scan
curl -X POST http://127.0.0.1:9621/query \
  -d '{"query":"本周雷达对抗的重点目标","mode":"mix"}'
```

### 场景2：单文档 S+Profile 导入

```bash
curl -X POST http://127.0.0.1:9621/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "# 标题\n内容...",
    "file_source": "report.md",
    "chunking": {"strategy": "structure_aware"},
    "profile": "week_report"
  }'
```

### 场景3：批量导入，指定策略和Profile

```bash
curl -X POST http://127.0.0.1:9621/documents/import \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "week_report",
    "chunking": {"strategy": "structure_aware"},
    "files": ["week01.docx", "week02.docx"]
  }'
```

### 场景4：自定义 Profile

```bash
# 1. 创建 lightrag/edp/profiles/my_report.yml
# 2. .env 设置 EDP_DEFAULT_PROFILE=my_report
# 3. 重启服务 → scan → 自动使用
```

---

## 七、Chunk 切分粒度控制

| 优先级 | 配置位置 | 说明 |
|--------|---------|------|
| 1（最高） | API `chunking.params.chunk_token_size` | 单次导入覆盖 |
| 2 | Profile YAML `chunk_token_size` | Profile 默认值 |
| 3（最低） | `.env` `CHUNK_S_SIZE` | 全局默认 |

**经验值**：
- 中文周报：800-1200
- 技术文档：1200-1500
- 论文/长文：1500-2000
