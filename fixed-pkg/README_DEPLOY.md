# LightRAG-EDP v13 部署与使用说明

> 适用版本: v13.3 (2026-07-29)
> 基于 LightRAG (HKUDS/LightRAG) 二次开发，面向企业文档解析的增强版本

---

## 目录

1. [版本更新总览](#1-版本更新总览)
2. [ARM 离线部署](#2-arm-离线部署)
3. [代码更新（已有部署）](#3-代码更新已有部署)
4. [S 策略使用指南](#4-s-策略使用指南)
5. [父子分段召回](#5-父子分段召回)
6. [元数据过滤](#6-元数据过滤)
7. [Chunk 删除 API](#7-chunk-删除-api)
8. [元数据同步](#8-元数据同步)
9. [跳过图谱生成](#9-跳过图谱生成)
10. [常用运维命令](#10-常用运维命令)
11. [环境变量速查](#11-环境变量速查)

---

## 1. 版本更新总览

### v12 — S 策略父子分段召回

**功能**: 检索时自动召回命中 chunk 所在 section 的所有兄弟 chunk。

**解决的问题**: 当一个 section 下有多个 chunk（如"雷达对抗" section 下有 chunk-001 和 chunk-002），向量检索只命中 chunk-001 时，chunk-002 也会被召回，保证 LLM 获得完整的 section 上下文。

**涉及文件**:
- `lightrag/lightrag/operate.py` — `_expand_section_chunks()` 函数
- `lightrag/lightrag/lightrag.py` — 全局配置读取
- `.env` — 新增 `SECTION_RECALL` / `SECTION_RECALL_DEPTH` / `SECTION_RECALL_MAX`
- `docker-compose.deploy.yml` — 环境变量注入

### v13 — docx 图表被错误分段修复

**解决的问题**:
1. `<drawing id="xxx"/>` / `<table>...</table>` 等 XML 标签被误判为 Markdown 标题（`# <drawing .../>` → H1）
2. 图表标注行（`图1 xxx`、`表2 xxx`）独立成 chunk，无检索价值

**涉及文件**:
- `lightrag/lightrag/edp/heading_detector.py` — 标题检测 guard
- `lightrag/lightrag/edp/chunk_strategy_s.py` — `_is_figure_only()` 过滤

### v11 — Chunk 删除 + Milvus 元数据过滤

**Chunk 删除**: `DELETE /documents/chunks/{chunk_id}` 和批量删除接口
**元数据过滤**: `metadata_filter` 改用 Milvus 原生标量表达式，不再内存后过滤

---

## 2. ARM 离线部署

### 前置条件

- ARM64 服务器（鲲鹏/飞腾/树莓派等）
- Docker 或 Podman 已安装
- 已获取以下文件：
  - `lightrag-v13-update.tar.gz`（本更新包）
  - Docker 镜像 tar 文件（首次部署需要，放在 `images/` 目录）

### 首次部署

```bash
# 1. 解压
tar -xzf lightrag-v13-update.tar.gz
cd fixed-pkg

# 2. 修改 .env 中的 LLM / Embedding 配置
vi .env
# 必改项:
#   LLM_BINDING_HOST=      # 你的 LLM API 地址
#   LLM_BINDING_API_KEY=   # 你的 API Key
#   LLM_MODEL=             # 你的模型名
#   EMBEDDING_BINDING_HOST=
#   EMBEDDING_BINDING_API_KEY=
#   EMBEDDING_MODEL=

# 3. 加载 Docker 镜像（首次）
docker load -i images/lightrag-base.tar
docker load -i images/milvus.tar
# ... 其他镜像

# 4. 启动所有服务
./deploy.sh

# 5. 确认启动成功
./status.sh
```

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| LightRAG API | 9621 | 主服务 |
| WebUI | 9621/webui | 前端界面 |
| Attu | 3000 | Milvus 可视化管理 |
| Milvus | 19530 | 向量数据库 |

---

## 3. 代码更新（已有部署）

如果你的 ARM 服务器上已有旧版部署，只需更新源码：

```bash
# 方法 1: 使用 update.sh（推荐）
cd fixed-pkg
./update.sh /path/to/new/lightrag

# 方法 2: 手动替换
# 将 lightrag/ 目录整体覆盖到 fixed-pkg/lightrag/
# 然后重启:
docker compose -f docker-compose.deploy.yml restart lightrag
```

> **注意**: 源码通过 bind mount 挂载（`:ro`），修改后只需 `restart` 即可生效，无需重新构建镜像。

---

## 4. S 策略使用指南

### 什么是 S 策略

S（Structure-aware）策略是一种**基于标题树的智能分段策略**：
- 识别文档中的标题层级（中文编号、Markdown heading 等）
- 按标题 section 切分，保持语义完整性
- 每个 chunk 带有 `heading_path`（标题面包屑）

### 如何启用

在 `.env` 中配置：

```bash
# 所有文件使用 S 策略
LIGHTRAG_PARSER=*:legacy-S

# 或按文件类型指定（推荐）
LIGHTRAG_PARSER=docx:native-teS;*:legacy-S
```

规则语法：`<后缀>:<解析器>-<过程选项>`，其中：
- `native` = 内置解析器，`legacy` = 纯文本
- 过程选项：`t`=实体提取, `e`=关系提取, `S`=S策略分段, `P`=段落语义

### S 策略专属配置

```bash
CHUNK_S_SIZE=1024          # S 策略 chunk 大小（默认 1200）
# CHUNK_S_OVERLAP_SIZE=0   # token 重叠（默认 0）

# embedding_text 生成模式:
#   rich    = doc_title + heading_breadcrumb + content（默认）
#   compact = 仅元数据
#   raw     = 纯正文
# EMBEDDING_MODE=rich

# 标题最大层级（超过此层级的标题视为正文）
# HEADING_MAX_LEVEL=3
```

### 支持的标题格式

| 格式 | 示例 | 默认层级 |
|------|------|----------|
| Markdown ATX | `# 第一章` `## 1.1 概述` | 按 # 数量 |
| 中文编号大标题 | `一、概述` `二、方法` | 1 |
| 中文括号子标题 | `（一）背景` `(一) 背景` | 2 |
| 阿拉伯括号子标题 | `(1) 数据来源` `（1）数据来源` | 4 |
| 数字多级编号 | `1. 概述` `1.1 背景` `2.3.1 细节` | dots+3 |
| 章节条 | `第一章 总则` `第3节 定义` | 自适应 |

### 标题层级自定义

```bash
# 覆盖默认层级（JSON 格式）
HEADING_LEVELS={"chinese_h2_full": 3, "decimal_base": 1}
```

---

## 5. 父子分段召回

### 功能说明

S 策略将文档按标题 section 切分，同一 section 下可能有多个 chunk。当检索命中其中一个时，**父子分段召回**自动将其兄弟 chunk 也返回给 LLM。

### 工作原理

```
文档结构:
  一、雷达对抗          ← section
    chunk-001: 截获记录描述
    chunk-002: 信号参数表格
    chunk-003: 分析结论

检索命中 chunk-001 → 自动召回 chunk-002, chunk-003
```

### 配置

```bash
SECTION_RECALL=true          # 启用（默认 false）
SECTION_RECALL_DEPTH=1       # 召回深度:
                              #   1 = 仅同 section 的兄弟 chunk
                              #   2 = 同时查父 section 的兄弟
SECTION_RECALL_MAX=20        # 最大扩展 chunk 数
```

### 使用建议

- **深度=1** 适合大多数场景
- **深度=2** 适合标题层级深、单个 section 内容短的文档
- **MAX** 根据 `CHUNK_TOP_K` 调整，建议设为 `CHUNK_TOP_K × 2`
- 仅对 S 策略有效，其他分段策略不受影响

---

## 6. 元数据过滤

### 功能说明

支持在检索时按文档元数据过滤，使用 Milvus 原生标量表达式（不走内存后过滤，性能更好）。

### 支持的操作符

| 操作 | 示例 |
|------|------|
| 精确匹配 | `entity_name == "雷达"` |
| 多值匹配 | `entity_name IN ["雷达", "通信"]` |
| 范围 | `created_at > "2025-01-01"` |
| 模糊匹配 | `doc_title LIKE "%报告%"` |
| 逻辑或 | `$or: [entity_name == "雷达", doc_title LIKE "%报告%"]` |

### 使用方式

**API 调用**:
```bash
curl -X POST http://localhost:9621/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "雷达对抗态势分析",
    "mode": "mix",
    "metadata_filter": "entity_name == \"电子对抗\""
  }'
```

**WebUI**: 在查询输入框下方填入 filter 表达式。

### 文件名元数据提取

上传文件时自动从文件名提取字段。在 profiles 中配置 `filename_fields`:

```yaml
# general.yml 示例
filename_fields:
  - field_name: entity_name
    pattern: "^([^-]+)-"       # 从文件名前缀提取
  - field_name: report_date
    pattern: "(\\d{8})"        # 从文件名提取日期
```

---

## 7. Chunk 删除 API

### 删除单个 chunk

```bash
curl -X DELETE "http://localhost:9621/documents/chunks/doc-xxx-chunk-001"
```

### 批量删除（按过滤条件）

```bash
# 删除 content 只有图表标签的 chunk
curl -X DELETE "http://localhost:9621/documents/chunks" \
  -H "Content-Type: application/json" \
  -d '{"filters": {"content_pattern": "<(drawing|img|image|table)"}}'

# 删除 token 数过少的 chunk
curl -X DELETE "http://localhost:9621/documents/chunks" \
  -H "Content-Type: application/json" \
  -d '{"filters": {"max_tokens": 50}}'

# 预览（不实际删除）
curl -X DELETE "http://localhost:9621/documents/chunks" \
  -H "Content-Type: application/json" \
  -d '{"filters": {"content_pattern": "<drawing", "max_tokens": 50}, "dry_run": true}'
```

### 删除范围

删除操作会清理以下 5 个存储层：
1. Milvus 向量数据
2. JSON KV 存储（full_docs, text_chunks）
3. 文档状态（doc_status）
4. 实体- chunk 关联
5. 关系- chunk 关联

---

## 8. 元数据同步

### 功能说明

将 Milvus 中 `$meta` 动态字段的数据写回到 JSON KV 存储，保持两端一致。

### 手动同步

```bash
# 同步单个文档
curl -X POST "http://localhost:9621/documents/sync-metadata/doc-xxx"

# 同步所有文档（使用脚本）
./sync-metadata.sh
```

### 定时同步

```bash
# crontab 示例：每天凌晨 2 点同步
0 2 * * * /path/to/fixed-pkg/sync-metadata.sh >> /var/log/lightrag-sync.log 2>&1
```

---

## 9. 跳过图谱生成

### 场景

- 批量上传大量文档，不想生成知识图谱
- 减少 LLM API 调用（图谱生成需要实体/关系提取）

### 使用方式

**WebUI**: 上传文件后 → 点击 `Enqueue` 时，勾选 "Skip Graph" 选项

**API**:
```bash
curl -X POST http://localhost:9621/documents/upload \
  -F "file=@document.docx" \
  -F "skip_graph=true"
```

**input 目录自动处理**: 在文件名后加标记（当前版本需通过 API 参数控制）

> 注意: 跳过图谱后，`mix` / `local` / `global` / `hybrid` 等依赖 KG 的检索模式将回退到 `naive` 模式。

---

## 10. 常用运维命令

```bash
# 查看服务状态
docker compose -f docker-compose.deploy.yml ps

# 查看 LightRAG 日志
docker compose -f docker-compose.deploy.yml logs -f lightrag

# 查看最近 100 行日志
docker compose -f docker-compose.deploy.yml logs --tail=100 lightrag

# 重启 LightRAG（源码更新后）
./restart.sh

# 停止所有服务
./stop.sh

# 完全清理（包括数据卷）
docker compose -f docker-compose.deploy.yml down -v

# 进入 Milvus Attu 管理界面
# 浏览器打开 http://<服务器IP>:3000

# 测试 API 是否正常
curl http://localhost:9621/health
```

---

## 11. 环境变量速查

### S 策略分段

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LIGHTRAG_PARSER` | - | 解析策略，如 `*:legacy-S` |
| `CHUNK_S_SIZE` | 1200 | S 策略 chunk token 数 |
| `CHUNK_S_OVERLAP_SIZE` | 0 | token 重叠 |
| `EMBEDDING_MODE` | rich | embedding 文本模式 |
| `HEADING_MAX_LEVEL` | - | 标题最大层级限制 |
| `HEADING_LEVELS` | - | 自定义标题层级 JSON |

### 父子分段召回

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECTION_RECALL` | false | 启用 section 召回 |
| `SECTION_RECALL_DEPTH` | 1 | 召回深度 |
| `SECTION_RECALL_MAX` | 20 | 最大扩展数 |

### 图谱相关

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_LLM_CACHE` | false | LLM 响应缓存 |
| `MAX_EXTRACTION_RECORDS` | 50 | 每次提取最大记录数 |
| `MAX_EXTRACTION_ENTITIES` | 20 | 每次提取最大实体数 |

### Milvus

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MILVUS_URI` | localhost:19530 | Milvus 地址 |
| `MILVUS_DB_NAME` | lightrag | 数据库名 |

---

## 附录: 文件清单

```
fixed-pkg/
├── README_DEPLOY.md          # 本文件
├── .env                      # 环境变量配置
├── docker-compose.deploy.yml # Docker Compose 部署配置
├── Dockerfile            # ARM64 镜像构建文件
├── deploy.sh                 # 首次部署脚本
├── update.sh                 # 源码更新脚本
├── restart.sh                # 重启 LightRAG
├── stop.sh                   # 停止所有服务
├── status.sh                 # 查看服务状态
├── sync-metadata.sh          # Milvus→JSON 元数据同步
├── fix-webui-dark.sh         # WebUI 暗色主题修复
├── WebUI暗色修复说明.md       # 暗色主题修复文档
└── lightrag/                 # 源码目录
    └── lightrag/
        ├── edp/
        │   ├── chunk_strategy_s.py    # S 策略分段
        │   ├── heading_detector.py    # 标题检测
        │   ├── metadata_builder.py    # 元数据提取
        │   ├── filter_builder.py      # 过滤条件构建
        │   ├── context_builder.py     # 上下文构建
        │   ├── models.py              # 数据模型
        │   └── profiles/              # EDP 配置文件
        ├── operate.py                 # 检索逻辑
        ├── lightrag.py                # 主入口
        ├── pipeline.py                # 文档处理管线
        └── api/
            └── routers/
                ├── document_routes.py # 文档管理 API
                └── query_routes.py    # 检索 API
```
