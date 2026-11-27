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
- TTL 支持具名类、具名父类继承、具名 domain/range、部分 XSD 类型。使用封闭世界抽取策略，OWL 限制、复杂类表达式、跨独立本体联合抽取暂未实现。含 owl:imports 的 TTL 暂需在本地合并为完整文件并去掉 imports 声明，不访问网络加载外部本体。
- 每个实体、属性、关系要求原文证据字符串。证据子串匹配不等于事实语义自动证明；非法记录隔离在抽取详情中。
- 实体只有配置 identity_properties 且抽到唯一值时才跨块合并，否则保留独立候选。属性多值与证据保留；关系按源实例、谓词 URI、目标实例唯一标识。
- SQLite 事务保存结果，文档删除只删除本次来源，重新抽取使用原快照并原子替换旧结果。失败暂按整篇重试。任务中断后重启可重试。暂未提供取消、单块重试、TTL 导出、精细分页和原 UI 完整功能迁移。
- DOCX/Markdown 复用 native parser，PDF 等使用原 legacy extractor；扫描 PDF 需 OCR 前置，MinerU/Docling 与视觉模型暂未接入新服务。
- JSON 导出包含实例、属性、关系及证据；尚未验证 ontology-platform 导入契约。

## 测试

```bash
cd fixed-pkg/lightrag
python -m pytest tests/test_gptonto.py -q
```

需准备 API 依赖、pytest 和 tiktoken 缓存。测试覆盖方向、非法属性、原文证据、多标签路由、无匹配、标题边界、来源删除及重复提交；真实模型输出及 Docker 部署需另行验证。
