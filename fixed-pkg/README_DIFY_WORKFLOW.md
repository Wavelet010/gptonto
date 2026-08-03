# LightRAG + Dify 工作流最佳实践

> 基于 LightRAG `prompt.py` 原始模板改写，适配 Dify 代码节点。  
> 支持 naive / mix / local 三种检索模式。

---

## 整体架构

```
用户提问  →  关键词提取（可选）  →  /query/data 检索  →  拼装 Prompt  →  LLM 回答
```

---

## 节点0: 关键词提取（可选，推荐用于 mix / local）

LightRAG 在 mix / local 模式下会内部调一次 LLM 提取关键词语。
如果在 Dify 侧预先提取，可以省掉这次调用。

**System Prompt:**

```
你是一个关键词提取专家。分析用户查询，提取两类关键词，以 JSON 格式返回，不要包含任何其他内容：

{
  "high_level_keywords": ["高层概念1", "高层概念2"],
  "low_level_keywords": ["具体实体1", "具体实体2"]
}

规则：
- high_level_keywords: 用户查询涉及的主题、领域、概念（2-3个）
- low_level_keywords: 用户查询中提到的具体实体、专有名词、术语（2-5个）
- 所有关键词必须来源于用户查询，不得编造
```

输出变量: `hl` (array), `ll` (array)

---

## 节点1: HTTP 请求 — /query/data

### naive 模式（纯向量）

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10,
  "enable_rerank": true
}
```

### mix 模式（向量 + 图谱）

```json
{
  "query": "{{用户问题}}",
  "mode": "mix",
  "chunk_top_k": 10,
  "top_k": 20,
  "hl_keywords": {{节点0.hl}},
  "ll_keywords": {{节点0.ll}},
  "enable_rerank": true
}
```

### local 模式（实体检索 + 关系遍历）

```json
{
  "query": "{{用户问题}}",
  "mode": "local",
  "top_k": 20,
  "hl_keywords": {{节点0.hl}},
  "ll_keywords": {{节点0.ll}},
  "enable_rerank": true
}
```

local 模式下 `chunk_top_k` 不需要，chunk 数量由实体关联的 source_id 决定。

### 参数速查

| 参数 | naive | mix | local | 说明 |
|------|:---:|:---:|:---:|------|
| `query` | ✅ | ✅ | ✅ | 用户问题 |
| `mode` | naive | mix | local | 检索模式 |
| `chunk_top_k` | 5~15 | 5~15 | ❌ | 向量检索 chunk 数 |
| `top_k` | ❌ | 10~30 | 10~30 | 图谱实体/关系数 |
| `hl_keywords` | ❌ | ✅ | ✅ | 高层关键词（概念） |
| `ll_keywords` | ❌ | ✅ | ✅ | 低层关键词（实体） |
| `enable_rerank` | 推荐 | 推荐 | 推荐 | 重排序 |

### 三种模式的区别

```
naive:  query向量化 → 搜chunks → 返回chunks
        无图谱参与，最快

mix:    query向量化 → 搜chunks
        + ll_keywords → 搜实体 → 拉关系 → 拉关联chunks
        + hl_keywords → 搜关系 → 拉实体 → 拉关联chunks
        三条路并行，最全

local:  ll_keywords → 搜实体 → 遍历关系图 → 拉邻居实体和关系
        + 拉实体关联的chunks
        深度遍历关系，适合"XX关联了什么"
```

### 返回数据结构

```json
{
  "status": "success",
  "data": {
    "chunks": [
      {
        "content": "...",
        "chunk_id": "...",
        "reference_id": "ref-1",
        "content_headings": "一、雷达对抗 → （一）截获记录",
        "file_path": "/data/inputs/xxx.docx"
      }
    ],
    "entities": [
      {
        "entity_name": "E-2K预警机",
        "entity_type": "装备",
        "description": "舰载预警机...",
        "reference_id": "ref-1",
        "file_path": "/data/inputs/xxx.docx"
      }
    ],
    "relationships": [
      {
        "src_id": "E-2K预警机",
        "tgt_id": "关岛安德森基地",
        "description": "部署于关岛安德森基地",
        "keywords": "部署,前沿存在",
        "reference_id": "ref-1",
        "file_path": "/data/inputs/xxx.docx"
      }
    ],
    "references": [
      {"reference_id": "ref-1", "file_path": "/data/inputs/xxx.docx"}
    ]
  },
  "metadata": {
    "query_mode": "mix",
    "keywords": {"high_level": [...], "low_level": [...]}
  }
}
```

---

## 节点2: 代码执行 — 拼装 Prompt

### 输入变量

| 变量名 | 类型 | 来源 |
|--------|------|------|
| `query_data` | string | `{{节点1.body}}` |
| `user_query` | string | `{{开始.query}}` |
| `response_type` | string | 固定值 `"Multiple Paragraphs"` 或由用户选择 |

### 输出变量

| 变量名 | 类型 |
|--------|------|
| `system_prompt` | string |
| `user_message` | string |
| `chunks_count` | number |

### 完整代码

```python
import json
import re


def main(query_data: str, user_query: str, lightrag_url: str = "", response_type: str = "Multiple Paragraphs") -> dict:
    """
    参照 LightRAG prompt.py 原始模板，将 /query/data 返回的检索结果
    拼装为 LLM 可用的 System Prompt。支持 naive / mix / local 三种模式。

    参数:
        query_data:   /query/data 返回的完整 JSON
        user_query:   用户问题
        lightrag_url: LightRAG 服务地址，如 http://192.168.1.100:9621
                      用于将 <drawing> 标签转为可访问的图片 URL
        response_type: 回答格式
    """
    # ===== 解析 =====
    if isinstance(query_data, str):
        data = json.loads(query_data)
    else:
        data = query_data

    inner = data.get("data", {})
    chunks = inner.get("chunks", [])
    entities = inner.get("entities", [])
    relationships = inner.get("relationships", [])
    references = inner.get("references", [])
    mode = data.get("metadata", {}).get("query_mode", "naive")
    has_kg = len(entities) > 0 or len(relationships) > 0

    # ===== 图片标签转换 =====
    # 将 chunk 中的 <drawing path="xxx.png" /> 转为 Markdown 图片链接
    # URL 格式: /assets/<doc_id>.blocks.assets/<path>
    def _convert_drawing_tags(text: str) -> str:
        def _replace_drawing(m):
            attrs = m.group(0)
            # 提取 id 和 path
            path_m = re.search(r'path="([^"]*)"', attrs)
            id_m = re.search(r'id="([^"]*)"', attrs)
            img_path = path_m.group(1) if path_m else ""
            alt = id_m.group(1) if id_m else "图片"
            if img_path and lightrag_url:
                return f"\n![{alt}]({lightrag_url.rstrip('/')}/assets/{img_path})\n"
            elif img_path:
                return f"\n> 📷 图片: {img_path}\n"
            return f"\n> 📷 {alt}\n"
        return re.sub(r"<drawing\b[^>]*/>", _replace_drawing, text)

    # ===== 拼 chunks 上下文 =====
    # 对应 prompt.py 的 naive_query_context / kg_query_context 模板
    chunk_entries = []
    for chunk in chunks:
        entry = {
            "reference_id": chunk.get("reference_id", ""),
            "content": _convert_drawing_tags(chunk.get("content", "")),
        }
        # content_headings: 标题面包屑，帮助 LLM 理解上下文
        if chunk.get("content_headings"):
            entry["content_headings"] = chunk["content_headings"]
        chunk_entries.append(entry)

    text_chunks_str = "\n".join(
        json.dumps(entry, ensure_ascii=False) for entry in chunk_entries
    )

    # ===== 拼引用列表 =====
    reference_lines = []
    for ref in references:
        rid = ref.get("reference_id", "")
        fpath = ref.get("file_path", "")
        if rid:
            reference_lines.append(f"[{rid}] {fpath}")
    reference_list_str = "\n".join(reference_lines)

    # ===== 拼图谱上下文（mix / local 模式）=====
    entities_str = json.dumps(entities, ensure_ascii=False)
    relations_str = json.dumps(relationships, ensure_ascii=False)

    # ===== 组装 Context =====
    if has_kg:
        # 对应 PROMPTS["kg_query_context"] — KG 模式（mix / local）
        context_block = f"""Knowledge Graph Data (Entity):

```json
{entities_str}
```

Knowledge Graph Data (Relationship):

```json
{relations_str}
```

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document, e.g. `Section 1 → Subsection 1.2`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```"""
    else:
        # 对应 PROMPTS["naive_query_context"] — 纯向量模式
        context_block = f"""Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document, e.g. `Section 1 → Subsection 1.2`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```"""

    # ===== 组装 System Prompt =====
    # 对应 PROMPTS["rag_response"] (KG模式) / PROMPTS["naive_rag_response"] (naive模式)
    # 使用原始英文模板结构，翻译为中文以适配 Dify LLM

    if has_kg:
        system_prompt = f"""---角色---

你是一个专业的知识库问答助手。你的主要职能是仅使用下方**上下文**中提供的信息，准确回答用户问题。

---目标---

根据用户查询，生成一个全面、结构清晰的回答。
回答必须整合**上下文**中的知识图谱数据和文档片段中的相关事实。
如有对话历史，请加以考虑以保持对话连贯性，避免重复信息。

---指令---

1. 分步执行:
  - 仔细分析用户查询意图，结合对话历史全面理解用户的信息需求。
  - 详细审查**上下文**中的"知识图谱数据"和"文档片段"，识别并提取与回答用户查询直接相关的所有信息。
  - 将提取的事实编织成连贯且有逻辑的回答。你的自有知识仅用于组织流畅的语句和连接观点，不得引入任何外部信息。
  - 追踪直接支撑回答中所述事实的文档片段的 reference_id，将其与"参考文档列表"中的条目对应，生成规范的引用标注。
  - 在回答末尾生成引用章节。每条引用文档必须直接支撑回答中呈现的事实。
  - 引用章节之后不得生成任何内容。

2. 内容与依据:
  - 严格遵守**上下文**中提供的内容；不得编造、假设或推断任何未明确陈述的信息。
  - 如果**上下文**中找不到答案，请说明"现有资料中未找到相关信息"。不要猜测。

3. 格式与语言:
  - 回答必须使用与用户查询相同的语言。
  - 回答必须使用 Markdown 格式以增强清晰度和结构（如标题、加粗、列表）。
  - 回答应以 {response_type} 的形式呈现。

4. 引用章节格式:
  - 引用章节标题为：`### 参考来源`
  - 引用条目格式为：`* [n] 文档标题`。左方括号 `[` 后不加 `^`。
  - 引用中的文档标题保留其原始语言。
  - 每条引用单独一行。
  - 最多提供 5 条最相关的引用。
  - 引用章节之后不得生成脚注、评论、总结或任何解释。

5. 引用章节示例:
```
### 参考来源

* [1] 文档标题一
* [2] 文档标题二
```

---上下文---

{context_block}"""
    else:
        system_prompt = f"""---角色---

你是一个专业的知识库问答助手。你的主要职能是仅使用下方**上下文**中提供的信息，准确回答用户问题。

---目标---

根据用户查询，生成一个全面、结构清晰的回答。
回答必须整合**上下文**中"文档片段"部分的相关事实。
如有对话历史，请加以考虑以保持对话连贯性，避免重复信息。

---指令---

1. 分步执行:
  - 仔细分析用户查询意图，结合对话历史全面理解用户的信息需求。
  - 详细审查**上下文**中的"文档片段"，识别并提取与回答用户查询直接相关的所有信息。
  - 将提取的事实编织成连贯且有逻辑的回答。你的自有知识仅用于组织流畅的语句和连接观点，不得引入任何外部信息。
  - 追踪直接支撑回答中所述事实的文档片段的 reference_id，将其与"参考文档列表"中的条目对应，生成规范的引用标注。
  - 在回答末尾生成引用章节。每条引用文档必须直接支撑回答中呈现的事实。
  - 引用章节之后不得生成任何内容。

2. 内容与依据:
  - 严格遵守**上下文**中提供的内容；不得编造、假设或推断任何未明确陈述的信息。
  - 如果**上下文**中找不到答案，请说明"现有资料中未找到相关信息"。不要猜测。

3. 格式与语言:
  - 回答必须使用与用户查询相同的语言。
  - 回答必须使用 Markdown 格式以增强清晰度和结构（如标题、加粗、列表）。
  - 回答应以 {response_type} 的形式呈现。

4. 引用章节格式:
  - 引用章节标题为：`### 参考来源`
  - 引用条目格式为：`* [n] 文档标题`。左方括号 `[` 后不加 `^`。
  - 引用中的文档标题保留其原始语言。
  - 每条引用单独一行。
  - 最多提供 5 条最相关的引用。
  - 引用章节之后不得生成脚注、评论、总结或任何解释。

5. 引用章节示例:
```
### 参考来源

* [1] 文档标题一
* [2] 文档标题二
```

---上下文---

{context_block}"""

    return {
        "system_prompt": system_prompt,
        "user_message": user_query,
        "chunks_count": len(chunks),
    }
```

### 与 LightRAG 原始的对应关系

| 代码节点 | prompt.py | 说明 |
|----------|-----------|------|
| `has_kg=True` 分支 | `rag_response` | KG 模式（mix / local），有实体+关系 |
| `has_kg=False` 分支 | `naive_rag_response` | 纯向量模式，只有 chunks |
| KG context block | `kg_query_context` | 实体 json + 关系 json + chunks json |
| Naive context block | `naive_query_context` | 只有 chunks json |

模板结构完整保留：`---角色---` → `---目标---` → `---指令---` → `---上下文---`，翻译为中文，`{response_type}` 占位符保留。

---

## 节点3: LLM — 生成回答

| 配置项 | 值 |
|--------|-----|
| System | `{{节点2.system_prompt}}` |
| User | `{{节点2.user_message}}` |
| Temperature | 0.2 |

---

## 模式选择指南

```
用户问 "XX是什么"、"XX的参数"、"查找XX相关文档"
  → naive: 纯向量检索，最快

用户问 "XX和YY有什么关联"、"综合分析XX的影响"
  → mix: 向量+图谱，最全

用户问 "XX参与了哪些事件"、"XX关联了哪些实体"
  → local: 沿着KG关系边遍历，深度关联
```

---

## 完整工作流节点配置

```
[开始]
  │ query = 用户问题
  ▼
[代码: 关键词提取]（mix / local 推荐，naive 不需要）
  │ LLM提取 hl_keywords + ll_keywords
  ▼
[HTTP: /query/data]
  │ mode: naive / mix / local
  ▼
[代码: 拼装 Prompt]
  │ 按 mode 选择 System Prompt 模板
  │ 输出 system_prompt + user_message
  ▼
[LLM]
  │ System: {{节点.system_prompt}}
  │ User: {{节点.user_message}}
  ▼
[结束]
```
