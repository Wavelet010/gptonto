# LightRAG EDP v10 离线更新操作手册

## 一、更新包内容

`lightrag-update-v10.tar.gz` (21MB)

```
├── install.sh                     # 一键安装脚本
├── docker-compose.deploy.yml      # 容器编排(已修复 entrypoint + command)
├── deploy.sh                      # 首次部署脚本(更新版)
├── sync-metadata.sh               # 元数据同步脚本
├── lightrag/    (934 文件)        # 完整源码目录
│   ├── docker-entrypoint.sh       # 容器启动脚本
│   └── lightrag/                  # Python 源码
└── README.md
```

## 二、更新步骤

### 1. 刻盘/传输

将 `lightrag-update-v10.tar.gz` 拷贝到 ARM 服务器。

### 2. 解压

```bash
cd /path/to  # 随便放哪里
tar -xzf lightrag-update-v10.tar.gz
cd lightrag-update-20260723/
```

### 3. 安装

```bash
bash install.sh /path/to/fixed-pkg
```

脚本自动完成：
- 备份旧源码 → `lightrag.bak.时间戳`
- 覆盖新源码
- 更新 docker-compose + deploy.sh
- 安装 sync-metadata.sh
- 保留原有 `.env`（LLM 密钥不丢）
- 重建 lightrag 容器

### 4. 验证

```bash
# 等 30 秒后
curl http://localhost:9621/health
# 应返回 {"status":"healthy"}

# 访问 WebUI
http://<ARM服务器IP>:9621/webui/
```

### 5. 设置每晚自动同步（可选）

```bash
crontab -l 2>/dev/null | grep -v sync-metadata > /tmp/cron.tmp
echo "0 3 * * * /path/to/fixed-pkg/sync-metadata.sh >> /path/to/fixed-pkg/data/sync-metadata.log 2>&1" >> /tmp/cron.tmp
crontab /tmp/cron.tmp
```

## 三、回滚

```bash
cd /path/to/fixed-pkg
docker compose -f docker-compose.deploy.yml stop lightrag
mv lightrag lightrag.failed
mv lightrag.bak.时间戳 lightrag
docker compose -f docker-compose.deploy.yml up -d --no-deps lightrag
```

## 四、v10 新增功能

| 功能 | 说明 |
|------|------|
| filename_fields | 从文件名提取元数据(title/doc_category/entity_name/entity_type/target_country) |
| 5种标题鲁棒检测 | 一、/（一）/(1)/第一章/1. 及其格式变体 |
| full_heading 面包屑 | 如 "二、网电对抗态势 → （一）雷达对抗 → 1. 截获记录" |
| 换行符全覆盖 | \n/\r\n/\r 及其组合 |
| 句子级贪心合并 | 句子拆分后合并填满 token 预算 |
| compact 模式修复 | include_doc_title + include_heading_path |
| LLM 提取容错 | 分批+重试+逐chunk兜底 |
| metadata_filter | Milvus 原生过滤(==/IN/range/LIKE/$or) |
| 元数据同步 API | POST /documents/sync-metadata/{doc_id} |
| entrypoint 热更新 | docker-entrypoint.sh 也由 bind mount 管理 |

## 五、注意事项

1. **不丢数据**：`data/` 目录不动，文档/向量/图谱都在
2. **不需要镜像**：Docker 镜像已在 ARM 服务器上
3. **不需要重启其他服务**：milvus/etcd/minio/attu 不动
4. **日常改代码**：改 `fixed-pkg/lightrag/` 文件 → `docker compose restart lightrag`
5. **改 .env**：改完需要 `docker compose restart lightrag`
6. **Profile 改完立刻生效**：不用重启
