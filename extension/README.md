# infill 小红书千帆抓单 Chrome 扩展

## 开发模式加载

1. 打开 chrome://extensions/
2. 右上角"开发者模式"开关
3. 点"加载已解压的扩展程序" → 选择本仓库的 `extension/` 目录
4. 复制扩展页显示的扩展 ID（32 位小写字母）
5. 粘到 `frontend/.env` 的 `VITE_INFILL_EXT_ID=<id>`，重启前端 dev server / 重新 build

## 构建发布包

```
bash scripts/build-extension.sh
```

产物：

- `release/extension/infill-xhs-scraper-v<version>.zip` — Git-ignored，本地分发
- `backend/static/extensions/infill-xhs-scraper-v<version>.zip` — 部署服务用

后者由后端通过 `/static/extensions/...` 暴露，前端 CUJ-4 设置页中的「下载扩展安装包」链接即指向此处。

## 获取扩展 ID

装入 Chrome 后，在 `chrome://extensions/` 页面顶部找到本扩展，复制 ID（32 位小写字母字符串）。
写入 `frontend/.env`：

```
VITE_INFILL_EXT_ID=abcdefghijklmnopqrstuvwxyzabcdef
```

重启前端 dev server / 重新 build 后生效。

## DOM 选择器维护

千帆订单页 DOM 改版后：

1. 在 `content_xhs.js` 顶部更新 `SELECTORS` 常量（订单卡片、外部订单号、买家昵称、下单时间、商品标题、件数）
2. 升 `manifest.json` 的 version 号（DOM 变化通常是 patch 或 minor）
3. 重新构建：`bash scripts/build-extension.sh`
4. 用户在 `chrome://extensions/` 点击 reload 即可生效（zip 安装则需重新加载已解压扩展）
