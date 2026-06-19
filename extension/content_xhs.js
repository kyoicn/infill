// content_xhs.js
// 注：实际抓取由 background.js 的 chrome.scripting.executeScript({func})
// 在目标页执行，并不依赖此 content script。保留这个文件只是为了 manifest
// content_scripts.matches 注册 — 让扩展在打开 ark/qianfan tab 时即被加载，
// 便于 chrome.tabs.query 能找到（即使 background.js 已用 tabs API 查询）。
//
// 真实的选择器和提取逻辑见 background.js::extractQianfanOrders()
// 这里不再重复占位代码，避免误以为是真实实现。
console.debug('[infill-ext] content script loaded on', location.href);
