# GPTOnto 多本体抽取服务（开发版）

本分支新增独立入口 `lightrag.ontology.app`，不初始化 LightRAG 问答、Milvus、rerank 或向量索引。旧 LightRAG 入口仍保留，便于回归及回退；新功能不使用旧的无向关系合并逻辑。

## 本地启动

```bash
cd fixed-pkg/lightrag
pip install -e '.[api]'
export LLM_MODEL=your-model
export LLM_BINDING_HOST=http://your-server:3000/v1
export LLM_BINDING_API_KEY=your-key
python -m lightrag.ontology.app
```

浏览器访问 http://127.0.0.1:9621 ，依次上传本体 TTL、提示词 YAML、创建抽取方案，上传文档。可先预览分块，再抽取。示例文件在 lightrag/ontology/example_device.ttl、example_device.yml、example_document.txt。

对外开放时设置 GPTONTO_HOST=0.0.0.0 和 GPTONTO_API_KEY（必须设置），页面中输入 API Key。单进程运行，不使用多 worker；数据库默认 data/gptonto.sqlite3。V 分块需要 EMBEDDING_MODEL、EMBEDDING_DIM、EMBEDDING_BINDING_HOST、EMBEDDING_BINDING_API_KEY。其他模式无需 embedding。

## Docker

基于原先已经可用的 lightrag-base:latest 镜像制作派生镜像。复制 fixed-pkg/.env.gptonto.example 为 fixed-pkg/.env.gptonto 并配置。先停止占用 9621 的旧服务。

```bash
cd fixed-pkg
docker compose -f docker-compose.gptonto.yml up -d --build
```

离线构建：提前在联网、同架构/同 Python 版本的环境下载 rdflib 及依赖 wheels，放到 fixed-pkg/gptonto-wheels/，然后：
```bash
docker compose -f docker-compose.gptonto.yml build --build-arg OFFLINE=1
docker compose -f docker-compose.gptonto.yml up -d --no-build
```
复用原镜像提供的文档解析、LLM、分块依赖，另需准备原 tiktoken 缓存。现有旧部署配置未删除。

## 实现约束

- H 独立于 S，支持中文、数字、括号、多级及 Markdown 标题，严格按配置深度分块，长节内部按 token 拆分。F/R/P/V/S 调用原算法；P 对无 sidecar 文档仍走原回退行为。
- 本体/提示词内容哈希作为不可变版本；任务开始时固定所有候选方案快照。单块可命中零个、一个或多个方案，每个方案独立抽取。
- TTL 支持具名类、具名父类继承、具名 domain/range、部分 XSD 类型。使用封闭世界抽取策略，支持显式选择已上传本体作为依赖，解析本地 owl:imports 闭包；跨命名空间关系可在组合 Schema 中抽取。未声明依赖的独立方案仍各自抽取。复杂 OWL 类表达式及通用限制未实现；functional property 的单次抽取冲突会隔离，不访问网络加载外部本体。
- 每个实体、属性、关系要求原文证据字符串。证据子串匹配不等于事实语义自动证明；非法记录隔离在抽取详情中。
- 实体只有配置 identity_properties 且抽到唯一值时才跨块合并，否则保留独立候选。属性多值与证据保留；关系按源实例、谓词 URI、目标实例唯一标识。
- SQLite 事务保存结果，文档删除只删除本次来源，重新抽取使用原快照并原子替换旧结果。默认仅重试失败块，支持显式整篇重抽取；失败重试保留旧结果及新诊断。任务中断后重启可重试。暂未提供取消、任意指定单块重试、精细分页和原 UI 完整功能迁移。
- DOCX/Markdown 复用 native parser，PDF 等使用原 legacy extractor；扫描 PDF 需 OCR 前置，MinerU/Docling 与视觉模型暂未接入新服务。
- JSON 导出包含实例、属性、关系及证据；TTL 导出仅含实例三元组，不含来源证据。尚未验证 ontology-platform 导入契约。

## 测试

```bash
cd fixed-pkg/lightrag
python -m pytest tests/test_gptonto.py -q
```

需准备 API 依赖、pytest 和 tiktoken 缓存。测试覆盖方向、非法属性、原文证据、多标签路由、无匹配、标题边界、来源删除及重复提交；真实模型输出及 Docker 部署需另行验证。

## 跨块补全、解析与图谱（第三轮）

- 上传文档可选择是否启用跨块关系补全（默认启用）。首轮抽取结束后，针对含显式实体名称/编号或指代信号的文本块，选择同文档候选实体，最多 40 个；指代候选优先来自前 5 块。第二轮仅补关系，不创建新实体，使用当前块证据及本体 domain/range 校验。补全失败标记部分失败；重试第二阶段失败保留旧文档结果。此为启发式补全，不保证覆盖所有跨章节指代，也不做跨文档补全。
- 可选择 auto、native、legacy、mineru、docling 解析器。后两者复用原远程解析客户端和 IR/sidecar 写入，需配置可信服务端点；暂无真实服务测试，图片描述及独立资产展示未接入。预览与上传使用相同解析配置，但会分别解析。
- 实例浏览接口 GET /ontology-api/instances 支持 document_id、q、class_uri、offset、limit（最多 100）、focus（直接邻域）。搜索匹配名称、ID、属性值；图谱仅展示本页实例间的关系，双击节点展开邻域，滚轮缩放、拖动空白平移。完整 JSON/TTL 导出独立于分页。当前仍是独立 HTML 界面，不是原 React UI 的完整迁移。
