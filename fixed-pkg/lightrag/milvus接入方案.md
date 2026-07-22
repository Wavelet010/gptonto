

> **不要把 Milvus 当成 LightRAG 的一种 Storage。**

而是把它抽象成：

```text
VectorStore
    ├── NanoVectorDB
    ├── MilvusLite
    ├── Milvus
    ├── Qdrant（以后）
```

这样整个项目扩展性最好，也符合 LightRAG 的架构。

---

# 我建议最终文档（精简版）

```text
LightRAG Metadata Filter & Milvus Integration Design.md
```

控制在 **1500~2500 行 Markdown**。

---

# 1. 项目目标

## 目标

LightRAG 企业版支持：

* Milvus Lite
* Milvus
* Metadata 存储
* Metadata Filter
* Progressive Fallback
* Dynamic Metadata
* Chunk 混存（有 Metadata / 无 Metadata）

## 不修改

* Graph
* Workspace
* Prompt
* Entity
* Relation

保持完全兼容。

---

# 2. 总体架构

```text
                 Dify Workflow
                       │
          Query + Metadata Extraction
                       │
                       ▼
              LightRAG Query API
                       │
        ┌──────────────┴──────────────┐
        │                             │
   Metadata Filter Builder      Graph Retriever
        │                             │
        └──────────────┬──────────────┘
                       ▼
                 VectorStore
        ┌──────────────┼──────────────┐
        │              │              │
 NanoVectorDB     MilvusLite      Milvus
```

重点：

LightRAG：

**不负责 Metadata 提取。**

只负责：

Metadata Filter。

---

# 3. Chunk 模型

统一：

```python
Chunk {

    id

    workspace

    chunk_text

    embedding_text

    embedding

    metadata(optional)

}
```

其中：

metadata

允许：

```python
None
```

也允许：

```python
{}
```

也允许：

```python
{

knowledge_type:"雷达"

}
```

---

# 4. Milvus设计

Collection：

固定字段：

```text
id

workspace

chunk_text

embedding_text

vector
```

开启：

```python
enable_dynamic_field=True
```

所有 Metadata：

直接写 Dynamic Field。

例如：

```json
{

knowledge_type

equipment

year

author

...
}
```

无需提前定义。

---

# 5. Import流程

新增：

```text
Import Chunk

↓

Chunk

↓

Metadata

↓

Embedding

↓

VectorStore.save()
```

Storage：

判断：

```python
if metadata:

    insert(metadata)

else:

    insert()
```

无需补：

NULL。

---

# 6. Query API

新增：

```json
{

query,

top_k,

metadata_filter,

filter_mode

}
```

其中：

metadata_filter：

建议：

JSON。

例如：

```json
{

knowledge_type:[

"雷达"

],

equipment:[

"054A"

]

}
```

不要字符串。

---

# 7. Metadata Filter Builder（重点）

Builder：

负责：

JSON

↓

Milvus Expression

例如：

```json
{

knowledge_type:[

雷达

电子战

]
}
```

↓

生成：

```text
knowledge_type in ["雷达","电子战"]
```

支持：

* =
* !=
* >
* <
* > =
* <=
* IN
* LIKE
* ARRAY_CONTAINS
* ARRAY_CONTAINS_ANY
* ARRAY_CONTAINS_ALL

以后：

Qdrant：

Builder：

换一下即可。

---

# 8. Retrieval Pipeline（重点）

默认：

Soft。

流程：

```text
User Query

↓

Metadata Filter

↓

Milvus Recall

↓

Graph

↓

LLM
```

---

## Strict

```
Metadata Filter

↓

Milvus

↓

Graph
```

无 Metadata：

不参与。

---

## Soft（默认）

采用：

Progressive Fallback。

流程：

```text
① Metadata Filter

↓

结果>=TopK？

↓

YES

结束

↓

NO

↓

② 放宽 Metadata

↓

结果>=TopK？

↓

YES

结束

↓

NO

↓

③ 去掉 Metadata

↓

全库 ANN

↓

补足TopK
```

例如：

TopK=10

第一次：

```
knowledge_type=雷达

↓

4
```

第二次：

```
knowledge_type

OR

document_type

↓

7
```

第三次：

```
No Filter

↓

10
```

---

# 9. Progressive Fallback

建议：

抽象：

```python
FallbackStrategy
```

接口：

```python
search()

↓

strict()

↓

relax()

↓

global()
```

以后：

都可以扩展。

---

# 10. Storage接口

新增：

```python
class BaseVectorStore:

    insert()

    delete()

    search()

    search_with_filter()

    update()

    rebuild()
```

Nano：

实现。

Milvus：

实现。

以后：

Qdrant：

直接实现。

---

# 11. 兼容策略

支持：

✅ Nano

✅ MilvusLite

✅ Milvus

支持：

Chunk：

```
有Metadata
```

```
没有Metadata
```

混存。

无需：

任何特殊处理。

---

# 12. 源码修改

建议：

```
storage/

    vector_store/

        base.py

        nano.py

        milvus.py
```

新增：

```
retriever/

    filter_builder.py

    fallback.py
```

新增：

```
models/

    metadata.py
```

修改：

```
api/query.py
```

新增：

```
metadata_filter

filter_mode
```

修改：

```
query_param.py
```

新增：

```
metadata_filter

filter_mode
```

修改：

```
retriever.py
```

增加：

```
Filter Builder

↓

Fallback

↓

Vector Search
```

---

# 13. 测试

至少：

### 导入

* 有 Metadata
* 无 Metadata

### 查询

* 无 Filter
* 有 Filter
* Filter 无结果

### Fallback

* 命中
* 降级
* 全库

### Workspace

多个 Workspace。

### MilvusLite

全部通过。

---

# 我建议再补充一个"兼容性原则"

这是我认为整个方案最重要的一页，可以作为开发时必须遵守的原则。

> **兼容性原则**
>
> 1. **Metadata 是可选增强信息，不是必填信息。**
> 2. **没有 Metadata 的 Chunk 必须可以正常导入、Embedding、检索。**
> 3. **Metadata Filter 只是缩小检索范围，不能影响没有 Filter 时的原始检索能力。**
> 4. **默认使用 `soft` 模式，通过 Progressive Fallback 保证召回率；只有显式指定 `strict` 时才执行严格过滤。**
> 5. **所有新增能力必须保持对 NanoVectorDB 的兼容，不破坏现有 LightRAG 用户的使用方式。**
> 6. **Filter Builder 与底层向量库解耦，不直接拼接 Milvus API，未来可以平滑扩展到 Qdrant、pgvector 等其他 VectorStore。**

---

## 我还有两个建议（我认为会让你的方案更稳）

1. **`metadata_filter` 不要直接暴露 Milvus 表达式。**
   建议 API 接收结构化 JSON，由 `FilterBuilder` 翻译成 Milvus、Nano 或未来其他向量库的查询表达式。这样 API 永远不依赖底层存储。

2. **Fallback 最好设计成可配置策略。**
   不要把三步流程写死，可以在配置中定义，例如：

```yaml
retrieval:
  filter_mode: soft
  fallback:
    enabled: true
    min_results: 10
    steps:
      - strict
      - relax
      - global
```

以后如果你想增加 `"graph_only"`、`"hybrid"` 等策略，就不用修改核心检索逻辑了。

我认为这份精简版文档已经足够指导 LightRAG 的二次开发，同时又保留了后续扩展的空间。







# Milvus 接入 + FilterBuilder + Progressive Fallback 完整方案

## 1. 目标

- Milvus Lite 接入（嵌入式，零 Docker）
- 结构化 JSON 做 metadata_filter（不用字符串拼 Milvus expr）
- FilterBuilder 解耦后端（换 Qdrant/pgvector 只需换翻译层）
- Progressive Fallback（soft 模式分级降级）
- 有/无 metadata 的 chunk 混存
- 完全兼容 NanoVectorDB，不破坏现有用户

## 2. 架构

```
Query API {query, metadata_filter: JSON, filter_mode: "soft"|"strict"}
    │
    ▼
FilterBuilder.parse(json) → 后端无关的 Filter 对象
    │
    ├── NanoVectorDB: 内存过滤（metadata_filter 忽略，全库 ANN）
    └── Milvus: 翻译为 expr 字符串
    │
    ▼
ProgressiveFallback (仅 soft 模式)
    ├── ① strict:  全量 filter → result >= min_results?
    ├── ② relax:   放宽 filter (OR 关键字段) → result >= min_results?
    └── ③ global:  去掉 filter → 全库 ANN 补足 top_k
    │
    ▼
Graph Retrieval → LLM 生成
```

## 3. Filter JSON 格式

```json
// 精确匹配
{"knowledge_type": "网电对抗态势"}

// 多值 IN
{"knowledge_type": ["雷达", "电子战"]}

// 组合条件
{"knowledge_type": "网电对抗态势", "equipment": "054A"}

// 范围
{"year": {"gte": 2024, "lte": 2026}}
```

**不直接暴露 Milvus 表达式。** FilterBuilder 负责翻译。

## 4. Progressive Fallback

```
TopK=10, min_results=5

① strict:  knowledge_type=="网电对抗态势" → 结果 3 (< 5)
    ↓
② relax:   knowledge_type=="网电对抗态势" OR parent_section_id=="网电对抗态势"
            → 结果 7 (>= 5) → 补足到 10
    ↓
③ global:  去掉 filter → 全库 ANN 取前 10
```

## 5. 改动清单

### 新文件
| 文件 | 作用 |
|------|------|
| `lightrag/edp/filter_builder.py` | JSON → 各后端 filter 对象翻译 |
| `lightrag/edp/fallback.py` | Progressive Fallback 策略 |

### 修改文件
| 文件 | 改动 |
|------|------|
| `lightrag/base.py` | `QueryParam.metadata_filter` 类型改为 `dict|None`（JSON），新增 `filter_mode` |
| `lightrag/operate.py` | `_get_vector_context` 加入 FilterBuilder + Fallback 调用 |
| `lightrag/api/routers/query_routes.py` | `metadata_filter` 改为 JSON dict，`filter_mode` 已有 |
| `.env` | 新增 `LIGHTRAG_VECTOR_STORAGE=MilvusVectorDBStorage` |
| `lightrag/kg/milvus_impl.py` | `query()` 的 `filter_expr` 参数已就绪 |

### 不改
- Graph、Entity/Relation、Prompt、Workspace
- EDP chunker/context/metadata 提取（已就绪）
- 导入接口

## 6. 兼容性原则

1. Metadata 可选，None/{} 都正常
2. 无 metadata 的 chunk 正常导入+检索
3. metadata_filter 不传时行为完全不变
4. NanoVectorDB 忽略 filter，全库 ANN
5. 默认 soft 模式保证召回率

## 7. 验证

```bash
# 1. 切 Milvus
LIGHTRAG_VECTOR_STORAGE=MilvusVectorDBStorage

# 2. 导入：S + Profile（有 metadata）+ 无 Profile（无 metadata）
curl .../documents/text {"chunking":{"strategy":"structure_aware"},"profile":"week_report"}
curl .../documents/text {"text":"普通文本","file_source":"plain.txt"}

# 3. 查询对比
# 无 filter → 所有 chunk 命中
curl .../query {"query":"态势"}

# 有 filter → 仅 metadata chunk 命中  
curl .../query {"query":"态势","metadata_filter":{"knowledge_type":"网电对抗态势"}}

# soft fallback → 过滤结果不足时自动补全
curl .../query {"query":"态势","metadata_filter":{"knowledge_type":"不存在"},"filter_mode":"soft"}
```

## 8. 实施顺序

1. FilterBuilder（JSON → expr 翻译）
2. Progressive Fallback
3. operate.py 集成
4. query_routes.py 改 metadata_filter 类型
5. .env 切 Milvus → 测试
