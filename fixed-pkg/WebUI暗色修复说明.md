# WebUI 暗色主题修复说明

## 问题原因

你改的 `.tsx` 是源码，但前端跑的是**编译后的 JS**。改源码不生效，必须改编译产物。

## 一键修复

把 `fix-webui-dark.sh` 拷到 ARM 服务器的 `fixed-pkg/` 目录下，执行：

```bash
bash fix-webui-dark.sh
```

脚本自动完成：
1. 找到编译后的 JS 文件
2. 替换暗色背景 `bg-primary/5` → `bg-card border`
3. 替换标题色 `text-amber-700` → `text-amber-700 dark:text-amber-400`
4. 重启容器

## 什么情况需要重建前端

只有**新增前端功能**（改组件逻辑、加新页面）才需要 `bun install && bun run build`。只改 CSS 样式用上面的方法就够了。
