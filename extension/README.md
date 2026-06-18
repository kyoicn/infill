# infill 小红书千帆抓单 Chrome 扩展

## 开发模式加载

1. 打开 chrome://extensions/
2. 右上角"开发者模式"开关
3. 点"加载已解压的扩展程序" → 选择本仓库的 `extension/` 目录
4. 复制扩展页显示的扩展 ID
5. 粘到 `frontend/.env` 的 `VITE_INFILL_EXT_ID=<id>`，重启前端

## 构建发布包

```
bash scripts/build-extension.sh
```

产物：`release/extension/infill-xhs-scraper-v<version>.zip`

## DOM 选择器维护

千帆改版后更新 `content_xhs.js` 顶部 `SELECTORS` 常量 → 升 `manifest.json` 的 version → 重新构建。
