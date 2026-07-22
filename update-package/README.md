# LightRAG ARM 离线部署更新包

## 覆盖安装

```bash
# 在 ARM 服务器的 fixed-pkg 目录下执行
cp -r update-package/* fixed-pkg/
```

## 重启生效

```bash
cd fixed-pkg
docker compose -f docker-compose.deploy.yml restart lightrag
```

## 注意事项

1. **不需要重建镜像**，重启即可
2. **不需要 `docker compose down && up`**，restart 就生效
3. **数据不会丢**，都在 `./data/` 下
4. Profile 改完立刻生效，不用重启
5. `.env` 改完需要 restart
6. **所有 `lightrag/` 下的文件（含 entrypoint、前端、Profile）改完 restart 即生效**，无需 docker cp

## 本次更新内容

| 修复 | 文件 |
|------|------|
| Profile 改完立刻生效（pipeline 读挂载目录） | `pipeline.py` |
| compact 无 summary 时降级 rich 模式 | `context_builder.py` |
| edp_profile 在状态转换中保留 | `utils_pipeline.py` |
| Upload API Swagger 文档更新 | `document_routes.py` |
| 删文件同步（entrypoint cp→rm+cp） | `docker-entrypoint.sh` |
| tiktoken 离线缓存 + attu ARM 修复 | `docker-compose.deploy.yml` |
| deploy.sh mkdir tiktoken | `deploy.sh` |
| EDP 使用手册更新 | `EDP使用手册.md` |
