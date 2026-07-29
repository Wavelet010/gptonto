# LightRAG + Dify 工作流最佳实践

## 整体架构

```
用户提问
  │
  ▼
┌──────────────────────────────────────┐
│  节点1: HTTP请求 → /query/data       │  ← LightRAG 检索
│  返回: entities, relationships, chunks│
└──────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────┐
│  节点2: 代码节点 → 拼装 Context       │  ← 仿照 LightRAG prompt.py
│  输入: 节点1的原始数据                │
│  输出: 拼好的 system_prompt + context │
└──────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────┐
│  节点3: LLM → 生成回答               │
│  System: 节点2的 system_prompt       │
│  User: 用户原始问题                   │
└──────────────────────────────────────┘
  │
  ▼
  输出: 回答 + 引用
```

## 节点1: HTTP 请求 — /query/data

### 基础配置

| 配置项 | 值 |
|--------|-----|
| 请求方式 | POST |
| URL | `http://<LightRAG IP>:9621/query/data` |
| Headers | `Content-Type: application/json` |

### 参数配置

**最简单有效的配置（推荐起步）：**

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10,
  "hl_keywords": ["{{高层关键词}}"],
  "ll_keywords": ["{{低层关键词}}"],
  "enable_rerank": true
}
```

**带元数据过滤：**

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10,
  "metadata_filter": {"entity_name": "{{知识库名称}}"},
  "filter_mode": "strict",
  "enable_rerank": true
}
```

**进阶 — 预填关键词跳过 LLM 关键词提取：**

如果 Dify 工作流里已经有一个 LLM 节点做了关键词提取，直接把结果传给 hl_keywords / ll_keywords，LightRAG 就不会再调一次 LLM 提取关键词，省一次 API 调用：

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10,
  "hl_keywords": {{dify关键词提取节点.高层关键词}},
  "ll_keywords": {{dify关键词提取节点.低层关键词}},
  "enable_rerank": false
}
```

> **注意**: `hl_keywords` 和 `ll_keywords` 是数组类型。如果 Dify 传的是字符串，需要用代码节点 split。

### 参数说明

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `mode` | `"naive"` | 纯向量检索，不打图谱。**Dify 场景首选** |
| `mode` | `"mix"` | 向量 + 图谱混合，适合需要关联分析的场景 |
| `chunk_top_k` | 5~10 | 召回 chunk 数，越多上下文越丰富，但 token 消耗越大 |
| `enable_rerank` | true | 开启重排序。如果已在 Dify 侧做重排可以关掉 |
| `only_need_context` | false | `/query/data` 默认就是只返数据不调 LLM，不用设 |
| `response_type` | - | `/query/data` 不生成回答，不用设 |
| `include_references` | - | `/query/data` 始终返回引用 |

### mode 选择建议

```
Dify 场景推荐:
  naive    ← 90% 场景，纯向量就够了
  mix      ← 需要知识图谱关联分析时

不推荐:
  local/global/hybrid ← 依赖 KG，图谱乱了效果差
  bypass              ← 空返回，没用
```

## 节点2: 代码节点 — 拼装 Context

### 完整代码（Python）

```python
import json

def main(query_data: str, user_query: str, response_type: str = "Multiple Paragraphs") -> dict:
    """
    把 LightRAG /query/data 返回的原始数据拼成 LLM 可用的 System Prompt。
    
    Args:
        query_data: 节点1 HTTP 请求返回的 body（JSON 字符串或 dict）
        user_query: 用户原始问题
        response_type: 回答格式，可选 "Multiple Paragraphs", "Single Paragraph", "Bullet Points"
    """
    # 解析
    if isinstance(query_data, str):
        data = json.loads(query_data)
    else:
        data = query_data
    
    # 提取各字段
    chunks = data.get("data", {}).get("chunks", [])
    entities = data.get("data", {}).get("entities", [])
    relationships = data.get("data", {}).get("relationships", [])
    references = data.get("data", {}).get("references", [])
    mode = data.get("metadata", {}).get("query_mode", "naive")
    
    # ── 1. 拼 chunks 上下文 ──
    # 每个 chunk 保留 reference_id, content, content_headings
    chunks_context = []
    for chunk in chunks:
        entry = {
            "reference_id": chunk.get("reference_id", ""),
            "content": chunk.get("content", ""),
        }
        if chunk.get("content_headings"):
            entry["content_headings"] = chunk["content_headings"]
        chunks_context.append(entry)
    
    text_chunks_str = "\n".join(
        json.dumps(c, ensure_ascii=False) for c in chunks_context
    )
    
    # ── 2. 拼引用列表 ──
    reference_lines = []
    for ref in references:
        rid = ref.get("reference_id", "")
        fpath = ref.get("file_path", "")
        if rid:
            reference_lines.append(f"[{rid}] {fpath}")
    reference_list_str = "\n".join(reference_lines)
    
    # ── 3. 按 mode 选择 System Prompt ──
    if mode in ("local", "global", "hybrid", "mix"):
        # KG 模式 — 带实体和关系
        entities_str = json.dumps(entities, ensure_ascii=False)
        relations_str = json.dumps(relationships, ensure_ascii=False)
        
        context_block = f"""Knowledge Graph Data (Entity):

```json
{entities_str}
```

Knowledge Graph Data (Relationship):

```json
{relations_str}
```

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```"""
    else:
        # Naive 模式 — 只有 chunks
        context_block = f"""Document Chunks (Each entry has a reference_id refer to the `Reference Document List`; the optional `content_headings` field gives the chunk's heading path within its source document):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```"""

    # ── 4. 组装 System Prompt ──
    system_prompt = f"""---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent to fully understand the user's information need.
  - Scrutinize `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a **References** section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

---Context---

{context_block}"""

    return {
        "system_prompt": system_prompt,
        "user_message": user_query,
        "chunks_count": len(chunks),
        "entities_count": len(entities),
        "references_count": len(references),
    }
```

### Dify 代码节点输出变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `system_prompt` | string | 拼好的 System Prompt，传给 LLM |
| `user_message` | string | 透传的用户问题 |
| `chunks_count` | number | 召回的 chunk 数（调试用） |
| `entities_count` | number | 实体数 |
| `references_count` | number | 引用数 |

## 节点3: LLM — 生成回答

### 配置

| 配置项 | 值 |
|--------|-----|
| Model | 选择你的 LLM（DeepSeek / GPT / Qwen 等） |
| System | `{{节点2.system_prompt}}` |
| User | `{{节点2.user_message}}` |
| Temperature | 0.3（低温度保证忠实性） |
| Max Tokens | 2048~4096 |

## 进阶优化

### 优化1: 关闭 LightRAG 侧重排序（省时间）

Dify 侧如果不需要重排序，直接在请求里关掉：

```json
"enable_rerank": false
```

### 优化2: 预填关键词（省一次 LLM 调用）

在 HTTP 节点前加一个简单的关键词提取 LLM 节点：

```
节点0: LLM → 提取关键词
  输入: 用户问题
  System: "Extract 3-5 high-level keywords (concepts/themes) and 3-5 low-level keywords (specific entities/terms) from the query. Output ONLY a JSON: {\"hl\": [...], \"ll\": [...]}"
  输出: {"hl": ["电子对抗", "雷达"], "ll": ["信号参数", "截获"]}

节点1: HTTP → /query/data
  hl_keywords: {{节点0.hl}}
  ll_keywords: {{节点0.ll}}
```

这样 LightRAG 不会内部再调一次 LLM 提取关键词，**省 1~3 秒**。

### 优化3: chunk_top_k 动态调整

```python
# 在代码节点里根据问题复杂度动态设 chunk_top_k
if len(user_query) > 50:
    chunk_top_k = 10  # 复杂问题多召回
else:
    chunk_top_k = 5   # 简单问题少召回
```

### 优化4: 多轮对话

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "conversation_history": [
    {"role": "user", "content": "上一个问题"},
    {"role": "assistant", "content": "上一个回答"}
  ],
  "chunk_top_k": 10
}
```

> `conversation_history` 只传给 LLM 做上下文，**不参与检索**。LightRAG 每轮都重新检索。

## 完整 Dify 工作流参数速查

```
[开始]
  │ 输入: query (用户问题)
  ▼
[LLM 关键词提取] (可选，推荐)
  │ System: 提取 hl/ll 关键词
  │ 输出: hl_keywords, ll_keywords
  ▼
[HTTP: /query/data]
  │ Body: {
  │   "query": "{{query}}",
  │   "mode": "naive",
  │   "chunk_top_k": 10,
  │   "hl_keywords": {{hl_keywords}},
  │   "ll_keywords": {{ll_keywords}},
  │   "enable_rerank": true
  │ }
  │ 输出: query_data
  ▼
[代码: 拼装 Prompt]
  │ 输入: query_data, query
  │ 输出: system_prompt, user_message
  ▼
[LLM: 生成回答]
  │ System: {{system_prompt}}
  │ User: {{user_message}}
  │ 输出: answer
  ▼
[结束]
  │ 输出: answer
```

## 调试技巧

在 Dify 中调试时，可以在 HTTP 节点后加一个"查看"节点：

1. 先看 `/query/data` 返回的 chunks 数量和质量
2. 如果 chunks 太少 → 加大 `chunk_top_k`
3. 如果 chunks 不相关 → 检查关键词提取质量，或调 `enable_rerank`
4. 如果回答幻觉 → 降低 Temperature 到 0.1
