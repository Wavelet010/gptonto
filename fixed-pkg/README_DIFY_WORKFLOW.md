# LightRAG + Dify 工作流最佳实践

## 两种模式，两个场景

| 模式 | 检索内容 | 速度 | 适合场景 |
|------|----------|:---:|------|
| `naive` | 纯向量 chunk | 快 | 事实问答、文档查找、"XX是什么" |
| `mix` | chunk + 知识图谱实体/关系 | 慢 | 关联分析、多文档综合、"XX和YY有什么关系" |

naive 适合 80% 的场景，mix 在有图谱时能发现跨文档的关联。建议两个都支持，让用户选。

---

## 工作流

```
用户提问
  │
  ▼
节点1: HTTP → /query/data  （mode 可配 naive 或 mix）
  │
  ▼
节点2: 代码 → 把 chunks + entities + relations 拼成 System Prompt
  │
  ▼
节点3: LLM → 生成回答
```

---

## 节点1: HTTP 请求

### naive 模式（纯向量）

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10,
  "enable_rerank": true
}
```

只需 4 个参数。naive 不调知识图谱，不提取关键词，最快。

### mix 模式（向量 + 知识图谱）

```json
{
  "query": "{{用户问题}}",
  "mode": "mix",
  "chunk_top_k": 10,
  "top_k": 20,
  "enable_rerank": true
}
```

比 naive 多一个 `top_k`：从知识图谱中检索多少个实体/关系。

### 参数速查

| 参数 | naive | mix | 说明 |
|------|:---:|:---:|------|
| `query` | ✅ | ✅ | 用户问题 |
| `mode` | `"naive"` | `"mix"` | 检索模式 |
| `chunk_top_k` | ✅ | ✅ | 返回 chunk 数（5~15） |
| `top_k` | ❌ 不需要 | ✅ | 图谱实体数（10~30） |
| `enable_rerank` | 推荐 | 推荐 | 重排序提升相关度 |
| `hl_keywords` | ❌ 不需要 | 可选 | 高层关键词（概念） |
| `ll_keywords` | ❌ 不需要 | 可选 | 低层关键词（实体） |
| `metadata_filter` | 可选 | 可选 | 元数据过滤 |

### mix 模式为什么需要 hl/ll keywords

mix 的检索分三条路同时跑：

```
用户问题 "雷达对抗态势分析中的截获记录"
  │
  ├─→ 原始query向量化 → 搜chunks（和naive一样）
  ├─→ ll_keywords向量化 → 搜实体节点 → 拉出关联关系和chunks
  └─→ hl_keywords向量化 → 搜关系边 → 拉出关联实体和chunks
```

如果不传 hl/ll keywords，LightRAG 会**自己调一次 LLM** 提取关键词——多一次 LLM 调用，慢 1~3 秒。如果你在 Dify 侧已经做了关键词提取，传进来就能省掉这次调用：

```json
{
  "query": "{{用户问题}}",
  "mode": "mix",
  "chunk_top_k": 10,
  "top_k": 20,
  "hl_keywords": ["电子对抗", "态势感知"],
  "ll_keywords": ["雷达", "截获记录"],
  "enable_rerank": true
}
```

---

## 节点2: 代码执行

这个代码同时支持 naive 和 mix，自动根据 mode 决定要不要拼实体和关系。

### 完整代码

```python
import json

def main(query_data: str, user_query: str) -> dict:
    """
    query_data: 节点1 HTTP 返回的完整 JSON（字符串）
    user_query: 用户问题
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

    # ===== 1. 拼 chunks =====
    chunk_lines = []
    for chunk in chunks:
        ref_id = chunk.get("reference_id", "")
        content = chunk.get("content", "")
        heading = chunk.get("content_headings", "")
        if heading:
            chunk_lines.append(f"[{ref_id}] ({heading})\n{content}")
        else:
            chunk_lines.append(f"[{ref_id}] {content}")
    chunks_text = "\n\n---\n\n".join(chunk_lines)

    # ===== 2. 拼引用列表 =====
    ref_lines = []
    for ref in references:
        rid = ref.get("reference_id", "")
        fpath = ref.get("file_path", "")
        if rid:
            ref_lines.append(f"[{rid}] {fpath}")
    refs_text = "\n".join(ref_lines)

    # ===== 3. 拼实体（mix 模式才有）=====
    entities_text = ""
    if entities:
        ent_lines = []
        for ent in entities:
            name = ent.get("entity_name", "")
            desc = ent.get("description", "")
            etype = ent.get("entity_type", "")
            if etype:
                ent_lines.append(f"- **{name}**（{etype}）: {desc}")
            else:
                ent_lines.append(f"- **{name}**: {desc}")
        entities_text = "\n".join(ent_lines)

    # ===== 4. 拼关系（mix 模式才有）=====
    relations_text = ""
    if relationships:
        rel_lines = []
        for rel in relationships:
            src = rel.get("src_id", "")
            tgt = rel.get("tgt_id", "")
            desc = rel.get("description", "")
            rel_lines.append(f"- {src} → {tgt}: {desc}")
        relations_text = "\n".join(rel_lines)

    # ===== 5. 组装 System Prompt =====
    # 根据有没有图谱数据来组织结构
    if entities_text or relations_text:
        kg_section = ""
        if entities_text:
            kg_section += f"### 知识图谱实体\n\n{entities_text}\n\n"
        if relations_text:
            kg_section += f"### 实体间关系\n\n{relations_text}\n\n"

        system_prompt = f"""你是一个专业的知识库问答助手。你必须**仅**根据下方提供的参考资料来回答问题，不得使用你自己的知识。

## 知识图谱数据

这部分是从知识图谱中检索到的结构化信息，反映了文档中实体之间的关联关系：

{kg_section}
## 文档片段

以下是检索到的相关文档片段。每个片段以 [引用编号] 开头：

{chunks_text}

## 参考文档列表

{refs_text}

## 回答要求

1. **严格基于资料**：只能使用上面参考资料中明确写到的信息。如果资料中没有答案，直接说"参考资料中未找到相关信息"，不要猜测。
2. **优先使用图谱**：知识图谱数据反映了跨文档的实体关联，可以帮助你发现不同文档之间的隐含联系。
3. **引用来源**：回答中的事实必须标注引用编号，如 [ref-1]。
4. **语言一致**：用和用户问题相同的语言回答。
5. **格式清晰**：使用 Markdown 格式，适当使用标题、列表、加粗等。
6. **引用章节**：在回答最后列出引用的文档：
   ### 参考来源
   - [ref-1] 文档路径"""
    else:
        # naive 模式 — 只有 chunks
        system_prompt = f"""你是一个专业的知识库问答助手。你必须**仅**根据下方提供的参考资料来回答问题，不得使用你自己的知识。

## 文档片段

以下是检索到的相关文档片段。每个片段以 [引用编号] 开头：

{chunks_text}

## 参考文档列表

{refs_text}

## 回答要求

1. **严格基于资料**：只能使用上面参考资料中明确写到的信息。如果资料中没有答案，直接说"参考资料中未找到相关信息"，不要猜测。
2. **引用来源**：回答中的事实必须标注引用编号，如 [ref-1]。
3. **语言一致**：用和用户问题相同的语言回答。
4. **格式清晰**：使用 Markdown 格式，适当使用标题、列表、加粗等。
5. **引用章节**：在回答最后列出引用的文档：
   ### 参考来源
   - [ref-1] 文档路径"""

    return {
        "system_prompt": system_prompt,
        "user_message": user_query,
        "chunks_count": len(chunks),
        "entities_count": len(entities),
        "relations_count": len(relationships),
    }
```

### 这个代码做了什么

**输入**: 3 种数据 —
- `chunks`: 文档片段列表，每个有 content（正文）、reference_id（引用编号）、content_headings（标题路径）
- `entities`: 知识图谱实体，entity_name / entity_type / description（**只有 mix 有**）
- `relationships`: 实体间关系，src_id → tgt_id + description（**只有 mix 有**）

**处理**: 把每个部分格式化成 LLM 容易理解的文本
- chunks → `[ref-1] (标题路径) 正文内容`
- entities → `- **实体名**（类型）: 描述`
- relationships → `- 源实体 → 目标实体: 关系描述`

**输出**: 三个变量 —
- `system_prompt`: 拼好的完整 System Prompt
- `user_message`: 透传的用户问题
- `chunks_count` / `entities_count` / `relations_count`: 调试用

### 两种模式的 System Prompt 区别

```
mix 模式:
  ## 知识图谱数据
  ### 知识图谱实体        ← 有实体列表
  ### 实体间关系          ← 有关系列表
  ## 文档片段             ← chunks
  "优先使用图谱"          ← 提示LLM利用关联

naive 模式:
  ## 文档片段             ← 只有chunks
  （没有图谱数据段）
```

---

## 节点3: LLM — 生成回答

| 配置项 | 值 |
|--------|-----|
| System | `{{节点2.system_prompt}}` |
| User | `{{节点2.user_message}}` |
| Temperature | 0.1~0.3 |

---

## 选择建议

```
用户问 "XXX是什么"、"XXX的定义"、"XXX包括哪些"
  → naive，快且准确

用户问 "XXX和YYY有什么关系"、"综合分析XXX的影响"
  → mix，图谱提供跨文档关联

不确定？
  → naive 先试，效果不够再切 mix
```
