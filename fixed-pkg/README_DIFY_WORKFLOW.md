# LightRAG + Dify 工作流最佳实践

## 重要前提

Dify 工作流里所有节点**只有两种语言**：
- **Python**（代码节点）
- **JSON**（HTTP 请求的 body、变量传递）

没有别的东西，没有模板语法，没有魔法。

## 三节点工作流

```
用户提问 "雷达对抗态势分析"
  │
  ▼
节点1: HTTP请求
  调用 LightRAG 的 /query/data 接口
  拿到 chunks、entities、references 等原始数据
  │
  ▼
节点2: 代码执行
  把这堆原始数据拼成一段带"系统指令+参考资料"的文本
  │
  ▼
节点3: LLM
  把节点2拼好的文本作为 System Prompt
  把用户原始问题作为 User Message
  生成最终回答
```

---

## 节点1: HTTP 请求

### 最简配置（推荐直接用这个）

```json
{
  "query": "{{用户问题}}",
  "mode": "naive",
  "chunk_top_k": 10
}
```

就三个参数。`naive` 模式只用向量检索，不碰知识图谱，2~3 秒出结果。

### 参数解释

| 参数 | 填什么 | 为什么 |
|------|--------|--------|
| `query` | 用户原始问题 | 用来做向量相似度搜索 |
| `mode` | `"naive"` | 纯向量，不依赖图谱 |
| `chunk_top_k` | 5~15 | 返回多少个 chunk。越多上下文越全，但后面 LLM 消耗 token 也越多 |

### 返回的数据长什么样

```json
{
  "status": "success",
  "data": {
    "chunks": [
      {
        "content": "实验数据显示，截获记录包含...",
        "chunk_id": "doc-xxx-chunk-001",
        "reference_id": "ref-1",
        "file_path": "/data/inputs/报告.docx"
      },
      { "content": "信号参数包括频率...", ... }
    ],
    "references": [
      { "reference_id": "ref-1", "file_path": "/data/inputs/报告.docx" }
    ],
    "entities": [],
    "relationships": []
  }
}
```

`naive` 模式下 `entities` 和 `relationships` 是空的，核心数据在 `chunks` 和 `references` 里。

---

## 节点2: 代码执行（逐行解释）

Dify 代码节点就是一个 Python 函数。输入是上面 HTTP 返回的 JSON，输出是你想传给下一个节点的变量。

### 完整代码

```python
import json

def main(query_data: str, user_query: str) -> dict:
    """
    query_data: 节点1返回的原始JSON（字符串）
    user_query: 用户在对话框输入的问题
    """
    # ===== 第1步：解析JSON =====
    # Dify HTTP节点返回的是字符串，需要转成dict
    if isinstance(query_data, str):
        data = json.loads(query_data)
    else:
        data = query_data

    chunks = data["data"]["chunks"]
    references = data["data"]["references"]

    # ===== 第2步：把chunks拼成一段文本 =====
    # 每个chunk是一个dict，包含content（正文）和reference_id（引用编号）
    # 我们把它们拼成编号列表，方便LLM引用
    chunk_lines = []
    for i, chunk in enumerate(chunks):
        ref_id = chunk.get("reference_id", "")
        content = chunk.get("content", "")
        # 同时带上标题路径（如果有的话），帮助LLM理解上下文
        heading = chunk.get("content_headings", "")
        if heading:
            chunk_lines.append(f"[{ref_id}] ({heading})\n{content}")
        else:
            chunk_lines.append(f"[{ref_id}] {content}")

    chunks_text = "\n\n---\n\n".join(chunk_lines)

    # ===== 第3步：拼引用列表 =====
    ref_lines = []
    for ref in references:
        rid = ref.get("reference_id", "")
        fpath = ref.get("file_path", "")
        if rid:
            ref_lines.append(f"[{rid}] {fpath}")
    refs_text = "\n".join(ref_lines)

    # ===== 第4步：组装System Prompt（中文版）=====
    system_prompt = f"""你是一个专业的知识库问答助手。你必须**仅**根据下方提供的参考资料来回答问题，不得使用你自己的知识。

## 参考资料

以下是检索到的文档片段：

{chunks_text}

## 参考文档列表

{refs_text}

## 回答要求

1. **严格基于资料**：只能使用上面参考资料中明确写到的信息。如果资料中没有答案，直接说"参考资料中未找到相关信息"，不要猜测。
2. **引用来源**：回答中提到的每个事实，必须在末尾标注引用编号，如 [ref-1]。
3. **语言一致**：用和用户问题相同的语言回答。
4. **格式清晰**：使用 Markdown 格式，适当使用标题、列表、加粗等。
5. **引用章节**：在回答最后列出引用的文档，格式为：
   ### 参考来源
   - [ref-1] 文档路径"""

    return {
        "system_prompt": system_prompt,
        "user_message": user_query,
    }
```

### 逐段解释

**第1步 — 解析 JSON**：
```python
data = json.loads(query_data)
```
LightRAG 返回的是 JSON 字符串，`json.loads()` 把它变成 Python 字典。之后就能用 `data["data"]["chunks"]` 取数据了。

**第2步 — 拼 chunks**：
```python
for i, chunk in enumerate(chunks):
    chunk_lines.append(f"[{chunk['reference_id']}] {chunk['content']}")
```
把每个 chunk 的正文前面加上它的引用编号，这样 LLM 读到后就知道"这段内容来自哪个文档"。多个 chunk 之间用 `---` 分隔，LLM 能区分段落边界。

**第3步 — 拼引用列表**：
```python
ref_lines.append(f"[{rid}] {fpath}")
```
生成一个 `[ref-1] /data/inputs/报告.docx` 这样的列表，LLM 最后引用时用。

**第4步 — System Prompt**：
把上面的内容全部填进一段中文提示词里。核心就一句：**"仅根据参考资料回答"**。

### 输出变量

代码节点的 `return` 会变成 Dify 下游节点可用的变量：
- `{{节点2.system_prompt}}` — 拼好的系统提示词
- `{{节点2.user_message}}` — 用户原始问题

---

## 节点3: LLM — 生成回答

| 配置项 | 值 |
|--------|-----|
| System | `{{节点2.system_prompt}}` |
| User | `{{节点2.user_message}}` |
| Temperature | 0.1~0.3（低温度减少幻觉） |

---

## 可选优化：预填关键词加速

naive 模式下虽然不影响检索，但如果你用 mix 模式需要加速，可以在 HTTP 节点前加一个 LLM 提取关键词：

```
节点0: LLM
  输入: 用户问题
  System: 从用户问题中提取概念关键词和实体关键词，返回JSON:
          {"high_level_keywords": ["概念1", "概念2"], "low_level_keywords": ["实体1", "实体2"]}

节点1: HTTP
  Body: {
    "query": "{{用户问题}}",
    "mode": "naive",
    "chunk_top_k": 10,
    "hl_keywords": {{节点0.high_level_keywords}},
    "ll_keywords": {{节点0.low_level_keywords}}
  }
```

> **注意**: 仅 `mix` / `local` / `global` 模式需要关键词。**naive 模式不需要**，传了也只是记录在 metadata 里，不影响检索。
