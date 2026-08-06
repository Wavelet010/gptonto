# LightRAG + Dify 工作流最佳实践

> 基于 LightRAG `prompt.py` 原始模板改写，适配 Dify 代码节点。  
> 支持 naive / mix / local 三种检索模式。

---

## Dify 代码节点 — 完整代码（直接复制）

### 输入变量

在 Dify 代码节点里声明以下**输入变量**：

| 变量名 | 类型 | 来源 |
|--------|------|------|
| `query_data` | string | `{{节点1.body}}` |
| `user_query` | string | `{{开始.query}}` |
| `lightrag_url` | string | LightRAG 服务地址，如 `http://192.168.1.100:9621`（可选） |
| `response_type` | string | 固定值 `"Multiple Paragraphs"` |

### 输出变量

| 变量名 | 类型 |
|--------|------|
| `system_prompt` | string |
| `user_message` | string |
| `chunks_count` | number |

### 代码

~~~python
import json
import re


def main(query_data: str, user_query: str, lightrag_url: str = "", response_type: str = "Multiple Paragraphs") -> dict:
    """
    参照 LightRAG prompt.py 原始模板，将 /query/data 返回的检索结果
    拼装为 LLM 可用的 System Prompt。支持 naive / mix / local 三种模式。
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
    # 将 chunk 中的 <drawing path="<base>.blocks.assets/xxx.png" /> 转为 Markdown 图片链接。
    # 图片通过 /assets/<base>.blocks.assets/<file> 直接 HTTP 访问。
    def _convert_drawing_tags(text: str) -> str:
        def _replace_drawing(m):
            attrs = m.group(0)
            path_m = re.search(r'path="([^"]*)"', attrs)
            id_m = re.search(r'id="([^"]*)"', attrs)
            img_path = path_m.group(1) if path_m else ""
            alt = id_m.group(1) if id_m else "图片"
            if img_path:
                if ".blocks.assets/" in img_path and lightrag_url:
                    url = f"{lightrag_url.rstrip('/')}/assets/{img_path}"
                    return f"\n![{alt}]({url})\n"
                if img_path.startswith(("http://", "https://")):
                    return f"\n![{alt}]({img_path})\n"
                return f"\n> 📷 图片: {img_path}\n"
            return f"\n> 📷 {alt}\n"
        return re.sub(r"<drawing\b[^>]*/>", _replace_drawing, text)

    # ===== 拼 chunks =====
    chunk_entries = []
    for chunk in chunks:
        entry = {
            "reference_id": chunk.get("reference_id", ""),
            "content": _convert_drawing_tags(chunk.get("content", "")),
        }
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

    # ===== 拼图谱数据 =====
    entities_str = json.dumps(entities, ensure_ascii=False)
    relations_str = json.dumps(relationships, ensure_ascii=False)

    # ===== 组装 Context =====
    # 对应 prompt.py 的 kg_query_context / naive_query_context
    FENCE = "```"
    if has_kg:
        context_block = f"""Knowledge Graph Data (Entity):

{FENCE}json
{entities_str}
{FENCE}

Knowledge Graph Data (Relationship):

{FENCE}json
{relations_str}
{FENCE}

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document, e.g. `Section 1 → Subsection 1.2`):

{FENCE}json
{text_chunks_str}
{FENCE}

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

{FENCE}
{reference_list_str}
{FENCE}"""
    else:
        context_block = f"""Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document, e.g. `Section 1 → Subsection 1.2`):

{FENCE}json
{text_chunks_str}
{FENCE}

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

{FENCE}
{reference_list_str}
{FENCE}"""

    # ===== 组装 System Prompt =====
    # 对应 prompt.py 的 rag_response (KG模式) / naive_rag_response (naive模式)
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
~~~

---

## API 参数速查

### /query/data 请求参数

| 参数 | naive | mix | local | 说明 |
|------|:---:|:---:|:---:|------|
| `query` | ✅ | ✅ | ✅ | 用户问题 |
| `mode` | `"naive"` | `"mix"` | `"local"` | 检索模式 |
| `chunk_top_k` | 5~15 | 5~15 | ❌ | 向量检索 chunk 数 |
| `top_k` | ❌ | 10~30 | 10~30 | 图谱实体/关系数 |
| `hl_keywords` | ❌ | ✅ | ✅ | 高层关键词（概念） |
| `ll_keywords` | ❌ | ✅ | ✅ | 低层关键词（实体） |
| `enable_rerank` | 推荐 | 推荐 | 推荐 | 重排序 |
| `metadata_filter` | 可选 | 可选 | 可选 | 元数据过滤，如 `{"knowledge_type":"网电对抗态势"}` |

### 三种模式

```
naive:  query向量化 → 搜chunks → 返回chunks （最快）

mix:    query向量化 → 搜chunks
        + ll_keywords → 搜实体 → 拉关系 → 拉关联chunks
        + hl_keywords → 搜关系 → 拉实体 → 拉关联chunks

local:  ll_keywords → 搜实体 → 遍历关系图 → 拉邻居实体和关系
```

---

## 模式选择

```
"XX是什么"、"XX的参数"、"查找XX相关文档"
  → naive，快

"XX和YY有什么关联"、"综合分析XX的影响"
  → mix，向量+图谱

"XX参与了哪些事件"、"XX关联了哪些实体"
  → local，深度遍历关系
```
