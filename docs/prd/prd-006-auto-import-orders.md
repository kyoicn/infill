---
id: prd-006
title: 自动导入订单
status: active
created: 2026-06-18
deprecation_reason:
---

# PRD-006：自动导入订单（小红书千帆 + 闲鱼）

> 本 PRD 与 [PRD-001 订单管理](prd-001-orders.md) 是**上下游关系**：自动导入（auto-import）**生产**新的 `Order` + `OrderItem` 记录，prd-001 **消费**它们（待处理队列 / 发货扣库存 / 已发货历史）。本 PRD 不替换 prd-001 CUJ-1 的「新建订单」入口 — 手动录单仍保留（少量个例订单 / 平台抓取失败时回退使用）；自动导入只是把日常 50 单 / 天的录单工作从「手抄 ~30 分钟」压缩到「点一个键 ~3 分钟」。

## 功能概述

作坊主每天经营两个销售渠道：**小红书（千帆后台）** 和 **闲鱼**。两者都没有可用的开放 API，目前每天晚间盘点要手工逐单录入到 prd-001。50 单 / 天的体量下，手抄痛点显著：① 录单耗时 ~30 分钟；② 平台 SKU 标题（如「龙猫摆件大号 灰白款」）与 catalog SKU 命名（如「龙猫-大号 - 灰白」）不一致，作坊主每单都要在脑内做翻译；③ 平台标题手抄易错。

本 PRD 把这两个渠道的订单抓取自动化：

- **小红书路径**：作坊主已在自己 PC 的 Chrome 浏览器登录千帆后台，infill 前端通过 Chrome 扩展（`externally_connectable` 机制）触发扩展抓取当前打开的千帆订单 tab 的 DOM，结构化后回传 infill 后端解析。
- **闲鱼路径**：作坊主在 PC 上跑 MuMu 模拟器（默认）/蓝叠/雷电/USB 真机运行闲鱼 app，infill 服务（跑在 Mac mini）通过局域网 ADB 截屏闲鱼「我的-订单列表」页，整页 PNG 上传给 LLM（Qwen3-omni-flash via DashScope，**假设 — 待确认**）解析订单字段。
- **统一预览**：两路径扫完后都进入同一张预览表格，每条原文标题（platform listing name）由 LLM 匹配到 catalog SKU，按置信度分三档高亮（高/中/低），用户校对低置信度行后一键导入 prd-001 的 `pending` 队列。

本 PRD 范围：

- CUJ-1：扫描小红书千帆订单（infill 触发 Chrome 扩展 → 扩展抓 DOM → 后端解析 + LLM 匹配 → 进预览）
- CUJ-2：扫描闲鱼订单（infill 触发 ADB 截屏 → 多张 PNG 累积 → LLM 解析 + 匹配 → 进预览）
- CUJ-3：预览校对 + 一键导入（统一表格 inline 改 SKU / 改件数 / 删商品 / 重复单去重 / 导入 pending 队列）
- CUJ-4：自动导入设置页（扩展状态 + ADB 设备配置 + 连通测试）

不在本 PRD 范围（v2+）：

- 抓取收货地址（闲鱼地址在二级页面，需 UI Automator 操作，复杂度太高暂不做；prd-001 的 `Order` 模型也尚无地址字段）
- 定时自动扫描（cron 在晚上 18:00 自动扫两个平台 — MVP 仍是用户手动点「开始扫描」）
- 批量回填历史订单（往前抓 N 天）
- 地址智能纠错 / 二维码登录托管
- 多平台并发扫描（MVP 串行，避免 LLM 限流 / 用户预览页面冲突）

## 与现有 PRD 的关系

auto-import → orders 链路约束：

| auto-import 阶段 | orders 端动作 |
|---|---|
| CUJ-1 / CUJ-2 扫描中 | 仅前端态 + 后端临时 batch 缓存；`Order` / `OrderItem` 表均不受影响 |
| CUJ-3 预览校对 | 仍是临时 batch；用户可任意改 SKU / 改件数 / 删商品 / 取消勾选 / 标记为重复改判 |
| CUJ-3 点「导入勾选的 N 单」 | 对勾选的每条订单，按 prd-001 数据模型创建 `Order(status='pending', created_at=外部订单创建时间, buyer_nickname=..., platform=..., external_order_id=...)` 一对多 `OrderItem(product_id, quantity)`，单事务批量提交（**假设 — 待确认**：批次原子性，任一单失败整批回滚） |
| 导入成功 | prd-001 「待处理」Tab 立即可见新订单；汇总条按产品聚合数量重算 |
| `(platform, external_order_id)` 唯一约束 | 预览阶段已通过后端查询识别重复单（灰底 + 默认不勾选 + 可点「改判为新单」override）；导入端点也做唯一约束兜底，若用户未 override 但数据库已存在 → 静默跳过该单（不算错误，归入"重复跳过"统计） |

新增字段要求（**假设 — 待确认**，需更新 prd-001 数据模型）：

- `Order` 表增加：`platform: enum('xiaohongshu','xianyu','manual')`（手动录单为 `manual`）、`external_order_id: str | null`（手动录单为 null）、`buyer_nickname: str | null`、`external_created_at: datetime | null`（外部平台的下单时间，与 `created_at` 区分 — `created_at` 是 infill DB 创建时间）
- `(platform, external_order_id)` 复合唯一索引，仅当 `external_order_id` 非 null 时生效
- 命名约定：手动录单走 prd-001 CUJ-1 入口、`platform='manual'`；自动导入走本 PRD、`platform∈{xiaohongshu,xianyu}`

## 数据流

```
用户进入 /orders/import          ──────►  默认小红书 tab，显示扩展/ADB 就绪状态
                                            │
              ┌─────────────────────────────┴─────────────────────────────┐
              ▼                                                             ▼
        CUJ-1 小红书扫单                                              CUJ-2 闲鱼扫单
        infill → Chrome 扩展（chrome.runtime.sendMessage）          用户手动滚 + 逐次点「截屏」→ infill 触发 ADB screencap
              │                                                             │
        扩展抓千帆订单 tab DOM                                          多张 PNG 累积（缩略图条 done/processing/queued）
              │                                                             │
        后端解析 + LLM 匹配 catalog SKU                              每张 PNG 异步上传 LLM 解析订单 + 用户点「完成」后 LLM 二次匹配 catalog SKU
              │                                                             │
              └──────────────────────────┬──────────────────────────────────┘
                                         ▼
                              CUJ-3 预览校对（N 单 × M 商品）
                              · 置信度分三档（绿✓/黄?/红!）行底色高亮
                              · 重复单（external_order_id 已存在）灰底 + 默认不勾选 + 可改判 override
                              · inline 改 SKU（picker 浮窗）/ 改件数 / 删商品 / + 添加商品
                              · bulk actions（全选新单 / 全不选 / 反选）
                                         │
                                         ▼
                              点「导入勾选的 N 单」按钮
                                         │
                              后端为每条 Order + OrderItem 写库（单事务批量）
                                         │
                              成功页：4 stat 网格（新增/跳过重复/手动跳过/SKU匹配率）+ 前 5 单 ID + 「前往订单管理」CTA
                                         │
                                         ▼
                              prd-001 「待处理」Tab 已立即可见新订单
```

## 串行扫描约定（CUJ-1 / CUJ-2 共用）

两个平台**不并发**：

- LLM 限流（DashScope 单 key 并发上限有限，并发跑两端易触发 429）
- 用户预览页面同一时间只能看一批
- 用户也无法真同时盯两个平台

工程上保证：当一个平台扫描进行中（CUJ-1 或 CUJ-2 任一），切到另一个 tab 时其「开始扫描」按钮 disabled，hover tooltip「上一个扫描尚未完成，请等待或先取消」。

## 置信度阈值（CUJ-3 决策依据）

LLM 匹配每条原文标题 → catalog SKU 时返回 0~1 浮点置信度，按三档分类：

| 档位 | 阈值 | 视觉 | 默认勾选 | 用户处理 |
|---|---|---|---|---|
| 高 | ≥ 0.85 | 绿 ✓，行白底 | 是 | 通常无需处理；快速过目即可 |
| 中 | 0.55 ~ 0.84 | 黄 ?，行浅黄底 `#fffbe6` | 是 | 提示校对（picker 内可换 SKU） |
| 低 | < 0.55 | 红 !，行浅红底 `#fff1f0` | **否** | 必须手动指 SKU 或删行；不指就无法勾选 |

阈值固定（MVP 不开放给用户调）；未来若有调优需求走「系统设置 → LLM 配置」（与 prd-005 共用入口）。

## LLM 提供商

DashScope（Qwen3-omni-flash，**假设 — 待确认**），API key 走 `.env`（`DASHSCOPE_API_KEY`），前端不输。CUJ-4 设置页只链到「系统设置 → LLM 配置」，不在本页配 key。

---

## CUJ-1：扫描小红书千帆订单

**Dependencies**: CUJ-4（前置：Chrome 扩展已装 + infill 检测到扩展就绪）；下游：CUJ-3 接收 batch 进入预览
**Priority**: P0（小红书是作坊主主要销售渠道之一；本 CUJ 是该渠道的录单自动化入口）

### Context

作坊主在自己 PC 上长期登录千帆后台浏览订单。每次盘点时他需要把当天订单逐条抄进 infill — 这是 50 单 / 天里第一个被自动化的环节。技术约束：千帆没有开放 API、登录态绑定 cookie 不便服务器侧抓取，故采用 Chrome 扩展从用户已打开的千帆 tab 的 DOM 抓取订单结构 — 既不需要用户提供登录凭证、也不需要服务器代理流量、由用户已经存在的浏览器会话完成抓取。

### Preconditions

- CUJ-4 设置页已检测到 Chrome 扩展就绪（扩展已装、版本号匹配）。
- 作坊主已在 Chrome 中打开千帆后台订单列表 tab 且已登录（cookie 有效）。
- 后端已配置 `DASHSCOPE_API_KEY`（否则 LLM 匹配步骤会失败，归入错误状态）。
- 后端已实现 `POST /api/orders/import/xhs/scan` 端点（接收扩展回传的 DOM 结构化数据 + 触发 LLM 匹配 + 返回 batch 预览结构）。

### Journey Steps

1. **User action**: 点击左侧导航菜单「订单管理 → 自动导入」（或顶部导航的「自动导入」入口）。
   - **System response**: 路由切换到 `/orders/import`，渲染 tab 容器（默认小红书 tab）。前端并发：① 调用 `chrome.runtime.sendMessage(<EXT_ID>, {action:"ping"})` 探活扩展；② 调用 `POST /api/orders/import/xhs/probe` 探查扩展能否找到千帆 tab。
   - **User sees**: 页面顶部面包屑「订单管理 / 自动导入」，标题「自动导入」+ 副标题「从小红书千帆与闲鱼自动抓取订单，LLM 匹配 catalog SKU，校对后一键导入待处理队列」。下方两个 tab「**小红书千帆**」（默认选中、小红书红 `#ff2442` 强调色）/「闲鱼」（默认未选中、闲鱼橙 `#ff7a00` 强调色）。tab 切换条下方主区显示小红书内容：左侧 sticky 控制栏（360px 宽）+ 右侧扫描状态/历史卡片。
   - **Details**: tab 切换不丢前端态（小红书 tab 进了扫描中，切到闲鱼再切回来还是扫描中）。

2. **User action**: 浏览左侧控制栏初始状态。
   - **System response**: 无；纯展示。
   - **User sees**: 控制栏标题「小红书千帆」+ 一个状态指示器三态之一：
     - **● 就绪**（绿点 + 文字「扩展已装 v0.1.x · 千帆 tab 已发现」）：可点「开始扫描」
     - **● 扩展未装**（蓝点 + 文字「Chrome 扩展未检测到」）：见 Edge Cases
     - **● 未发现千帆 tab**（黄点 + 文字「扩展已装但未发现千帆订单 tab」）：见 Edge Cases
   - **Details**: 状态探活在进入页面 / 切换 tab / 用户手动点「重新检测」时触发，~500ms 完成。

3. **User action**: 状态为「● 就绪」时点击「开始扫描」主按钮（小红书红，居控制栏中央）。
   - **System response**: 控制栏切换为「扫描进度」面板，主区右侧显示扫描进度卡片。前端按顺序触发 5 步：
     1. **连接扩展**（前端调用 `chrome.runtime.sendMessage(<EXT_ID>, {action:"scrape_xhs", batch_id})`）
     2. **定位千帆 tab**（扩展在用户已打开的 Chrome tabs 中找匹配 `*qianfan.xiaohongshu.com/*` 的 tab）
     3. **抓取 DOM**（扩展在该 tab 注入脚本提取订单列表 DOM 节点 → 结构化为 `{ external_order_id, listing_title, quantity, buyer_nickname, external_created_at }` 数组）
     4. **解析订单**（扩展把结构化数据 POST 给 `POST /api/orders/import/xhs/scan`，后端做字段标准化 + 去重查询）
     5. **LLM 匹配 SKU**（后端把每条 `listing_title` 提交 DashScope，返回 catalog SKU 候选 + 置信度。MVP 采用「全量 catalog 注入 prompt」的方式：把当前 catalog 全部 SKU（名称 + code）拼进 system prompt，让 LLM 在已知集合内挑。当前 catalog ≈ 50 SKU、几 KB 量级可行；当 catalog 涨到 200+ SKU 后再切 RAG / embedding 检索）
   - **User sees**: 5 步纵向列表，每步行首带状态图标（✓ 已完成 / 🔄 进行中带 pulse 动画 / ○ 等待）+ 步骤名 + 副文案（如「定位千帆 tab... 已找到 1 个匹配 tab」）。整体进度条在底部（线性渐变小红书红）。当前进行步骤的副文案动态更新（如第 4 步显示「正在解析 42 条订单」、第 5 步显示「正在匹配第 18/42 条」）。下方一个「取消」secondary 按钮。
   - **Details**: 进度图标用粗粒度（不打实时百分比，避免误导），但当前进行步骤可以有局部计数（如 LLM 匹配步骤显示「18/42」）。

4. **User action**: 等待 5 步走完（通常 30~60 秒，主要耗时在第 5 步 LLM 匹配）。
   - **System response**: 后端汇总匹配结果，返回 `{ ok: true, batch_id, items: [...] }`。前端把 batch 落到 CUJ-3 状态，**自动跳转**到 CUJ-3 预览页。
   - **User sees**: 主区切换到 CUJ-3 预览表格（详见 CUJ-3）。控制栏与扫描卡片消失；面包屑变为「订单管理 / 自动导入 / 预览批次」。
   - **Details**: 无独立 success 中间帧（用户已等了 30+ 秒，不需要再点「下一步」）。

5. **User action**:（取消分支）扫描中点「取消」按钮。
   - **System response**: 前端 abort fetch + 给扩展发 `{action:"abort_scrape", batch_id}` 消息（扩展尽力中止注入脚本但不保证立即停）；前端退回 CUJ-1 初始状态。
   - **User sees**: 控制栏回到初始「● 就绪」态；右侧扫描卡片消失；底部出现灰色 inline 提示「已取消上次扫描，未导入任何订单」（5 秒后自动消失）。
   - **Details**: 已发出但未完成的 LLM 调用 token 不退（DashScope 不支持中途退费）— 与 prd-005 CUJ-2 一致，文案不强调以免吓阻用户随手取消。

### Edge Cases & Error States

- **扩展未装**（`chrome.runtime.sendMessage` 抛 `Could not establish connection`）：控制栏状态为「● 扩展未装」（蓝点 + 蓝色 setup 块）；主区右侧显示蓝色引导卡片「请先安装 infill Chrome 扩展」+ 安装步骤 4 行：① 下载扩展压缩包链接（指向 `/static/extensions/infill-xhs-scraper-v0.1.x.zip`） ② 解压到任意目录 ③ Chrome 打开 `chrome://extensions/` 开「开发者模式」+ 「加载已解压的扩展程序」选解压目录 ④ 重新打开本页（系统会自动检测）。底部「我已安装，重新检测」secondary 按钮。
- **扩展已装但未发现千帆 tab**（扩展回报 `no_xhs_tab`）：控制栏状态为「● 未发现千帆 tab」（黄点）；主区右侧黄色 warning 块「未发现千帆订单 tab」+ 描述「请先在 Chrome 中打开千帆后台订单页（`https://qianfan.xiaohongshu.com/...`）并保持登录态」+ 底部「重新检测」按钮。
- **扩展已装但 ping 超时**（5 秒内无响应）：归入「扩展未装」错误态（用户处理路径同上 — 用户多半是没装、或装了但没启用）。
- **扩展回报 DOM 抓取失败**（千帆改版导致选择器失效）：扫描进度卡片在第 3 步报错，行内状态变 ✗（红色），副文案显示「DOM 抓取失败 — 千帆页面结构可能已变更（请联系开发者更新扩展选择器）」。底部「取消」按钮 + 「重新尝试」secondary 按钮。
- **扩展抓到的订单缺必填字段**（千帆改版部分选择器失效但整体没崩，导致某些订单缺 `external_order_id` 或 `buyer_nickname`）：必填三件套是 `platform / external_order_id / buyer_nickname`；任一缺失的订单**不入预览表**，由后端在「解析订单」步骤直接丢弃。扫描进度第 4 步完成后，扫描汇总（进入 CUJ-3 前的最终汇总 toast / 卡片）内显示「抓取到 N 单，其中 X 单跳过：扩展抓取格式异常，infill 需要更新（联系开发者）」。被丢弃的订单不计入 CUJ-3 表格、不影响其他订单进预览。
- **扩展抓到的订单数为 0**（千帆 tab 在用户筛选过的非订单页 / 当天无订单）：扫描进度走完 5 步，最终跳到 CUJ-3 但表格为空 — CUJ-3 空态显示「未抓取到任何订单 — 请确认千帆 tab 当前显示的是订单列表，或当天确无新订单」+ 「返回扫描页」按钮。
- **LLM 匹配步骤超时**（90 秒未完成）：前端主动 abort，主区切换到错误卡片：红 ! 图标 + 标题「SKU 匹配失败」+ 等宽字体错误详情「LLM 调用超时 — 90 秒未收到响应」+ 底部「重试」/「跳过 SKU 匹配，直接进预览（所有行红色低置信度）」/「返回」三个按钮。第 2 个按钮允许用户在 LLM 故障时仍录入订单（手动指 SKU）。
- **LLM 返回 HTTP 4xx/5xx**：错误详情等宽字体显示原始 HTTP 状态码 + 错误体（与 prd-005 CUJ-2 一致策略，技术作坊主自用产品，原始错误更有价值）。
- **闲鱼扫描进行中切到本 tab 点「开始扫描」**：按钮 disabled，hover tooltip「闲鱼扫描进行中，请等待或先取消」。
- **页面刷新 / 关闭**：扫描状态丢失（前端态）；已写入数据库的订单（CUJ-3 已点过导入的）不受影响。再次进入页面是 CUJ-1 初始态。
- **同一千帆 tab 内有多个订单列表标签页**（如「全部 / 待发货 / 已发货」子 tab）：扩展按当前激活子 tab 抓取（用户主动选择展示的视图）；不抓未激活的隐藏标签。

### Mocks / Reference Designs

- `docs/ux/prd-006-auto-import-orders/cuj-1-initial.html` — 小红书 tab 初始就绪态（控制栏「● 就绪」+ 「开始扫描」点亮）
- `docs/ux/prd-006-auto-import-orders/cuj-1-scanning.html` — 扫描中（5 步进度条，第 4 步「解析订单」进行中）
- `docs/ux/prd-006-auto-import-orders/cuj-1-no-xhs-tab.html` — 黄色 warn 块「未发现千帆 tab」+ 「重新检测」按钮
- `docs/ux/prd-006-auto-import-orders/cuj-1-no-extension.html` — 蓝色 setup 块「扩展未装」+ 4 步安装引导

### Acceptance Criteria

- 左侧菜单「订单管理」下存在子项「自动导入」（或顶部一级导航有「自动导入」），点击后 URL 为 `/orders/import`，菜单项高亮。
- 页面顶部面包屑「订单管理 / 自动导入」，标题「自动导入」+ 副标题描述用途。
- 双 tab 切换栏「小红书千帆」（默认选中、红 `#ff2442`）/「闲鱼」（橙 `#ff7a00`）；切 tab 不丢前端态。
- 进入页面 / 切 tab / 点「重新检测」时调用 `chrome.runtime.sendMessage(<EXT_ID>, {action:"ping"})` 探活扩展 + 调 `POST /api/orders/import/xhs/probe` 探查千帆 tab。
- 左侧控制栏（360px sticky）显示三态之一：● 就绪 / ● 扩展未装 / ● 未发现千帆 tab，每态有对应文字与颜色（绿 / 蓝 / 黄）。
- 「就绪」态下「开始扫描」按钮为小红书红 primary；其他态下 disabled。
- 点击「开始扫描」后主区切换为 5 步纵向进度列表（① 连接扩展 ② 定位千帆 tab ③ 抓取 DOM ④ 解析订单 ⑤ LLM 匹配 SKU），每步行首带状态图标（✓ / 🔄 pulse / ○）+ 副文案；当前进行步骤的副文案可显示子计数（如「正在匹配第 18/42 条」）。
- 进度卡片底部有「取消」secondary 按钮 + 整体进度条（小红书红线性渐变）。
- 5 步全部完成后前端自动跳转 CUJ-3 预览页（无独立 success 中间帧），面包屑变为「订单管理 / 自动导入 / 预览批次」。
- 「取消」点击后退回 CUJ-1 初始态，所有 batch 数据丢弃；底部 5 秒灰色提示「已取消上次扫描」。
- 扩展未装时显示蓝色 setup 块 + 4 步安装引导（下载链接 → 解压 → `chrome://extensions/` 加载 → 重新检测）。
- 未发现千帆 tab 时显示黄色 warning 块 + 「重新检测」按钮。
- 抓取到 0 条订单时仍跳到 CUJ-3，CUJ-3 显示「未抓取到任何订单」空态 + 「返回扫描页」按钮。
- 扩展抓到的单条订单缺失必填三件套（`platform / external_order_id / buyer_nickname`）任一项时，后端在「解析订单」步骤丢弃该单（不入预览表）；扫描汇总中显示丢弃单数及原因（如「抓取到 N 单，其中 X 单跳过：扩展抓取格式异常，infill 需要更新（联系开发者）」）。
- LLM 匹配步骤超时（90 秒）后端 abort，错误卡片显示「SKU 匹配失败」+ 错误详情 + 三个按钮（重试 / 跳过 SKU 匹配 / 返回）。
- 「跳过 SKU 匹配」按钮允许 LLM 故障时仍进 CUJ-3，所有行视为低置信度（红 !）。
- 闲鱼扫描进行中时本 tab「开始扫描」disabled + hover tooltip「闲鱼扫描进行中，请等待或先取消」。

---

## CUJ-2：扫描闲鱼订单

**Dependencies**: CUJ-4（前置：ADB 设备 endpoint 已配置且连通测试通过）；下游：CUJ-3 接收 batch 进入预览
**Priority**: P0（闲鱼是作坊主另一主销售渠道；本 CUJ 是该渠道的录单自动化入口）

### Context

闲鱼没有 Web 端订单列表（只有 app），也没有开放 API。作坊主在 PC 上用 MuMu 模拟器（默认）/蓝叠/雷电运行闲鱼 app，或用 USB 真机。infill 服务跑在 Mac mini，与 PC 同局域网。技术约束：闲鱼 app 的订单列表是动态滚动渲染，没有稳定 accessibility node；最朴素也最稳定的抓取方式是 **ADB 截屏整页 PNG + LLM 视觉解析**。屏幕截图能避免 UI Automator 兼容问题（闲鱼频繁改版、模拟器对 UI Automator 支持差异大），代价是 LLM token 消耗（~每张 0.01 元，单次扫描 5~10 张可控）。

### Preconditions

- CUJ-4 设置页配置了 ADB 设备类型 + PC IP + 端口（默认 MuMu / `<PC_IP>:7555`）且「测试 ADB 连接」绿勾通过。
- PC 上闲鱼 app 已运行、已登录、已停在「我的-订单列表」页（用户操作的最后一屏）。
- Mac mini 上 `adb` CLI 已安装且 infill 后端进程能调用。
- 后端已配置 `DASHSCOPE_API_KEY`。
- 后端已实现 `POST /api/orders/import/xianyu/scan` 端点（触发 ADB 截屏循环 + LLM 解析 + 返回 batch 预览结构）。

### Journey Steps

1. **User action**: 在 `/orders/import` 页面切到「闲鱼」tab。
   - **System response**: 主区切换为闲鱼布局（与小红书 tab 视觉结构镜像，但用闲鱼橙 `#ff7a00` 强调色）。前端调 `POST /api/orders/import/xianyu/probe`（后端跑一次 `adb connect <配置的 endpoint>` + `adb devices` 探活）。
   - **User sees**: 左侧 sticky 控制栏（360px 宽）顶部状态指示器：「● ADB 就绪」绿点 / 「● ADB 错」红点（详见 Edge Cases）。下方一组选项：「设备类型」下拉（默认 MuMu，可选 蓝叠/雷电/USB真机）+ 「PC IP / endpoint」只读显示（来自 CUJ-4 设置），右侧「编辑」link 跳设置页。再下方一段操作说明（灰底圆角块）：「① 在 PC 的模拟器里手动打开闲鱼「我的-订单」页 → ② 点下方「截屏」按钮触发一张截屏 → ③ 在模拟器里手动向下滚一屏 → 重复 ②③，直到你看到本页已重复出现旧订单（或抓到第一单）→ ④ 点「完成截屏，开始解析」」。底部两按钮：「截屏」橙色 secondary（disabled 直到 ADB 就绪）+ 「完成截屏，开始解析」橙色 primary（disabled 直到至少有 1 张截屏）。
   - **Details**: 状态指示器点亮逻辑：① probe 探到 `adb devices` 输出含目标 endpoint 且状态 `device`（非 `offline`）→ 绿；② 任意失败 → 红 + 展示 Edge Cases 块。MVP 不做自动滚动 — 模拟器内的滚动手势由用户在 PC 上手动操作；infill 只负责按下「截屏」按钮时触发一次 ADB screencap。理由：自动滚动需要按设备分辨率适配 swipe 距离 / 速度，易在不同模拟器与改版后失效；让用户控制滚动节奏更稳，且用户自己看着模拟器更容易判断何时已经回到第一单。

2. **User action**: 状态「● ADB 就绪」后，用户在 PC 的模拟器内手动停在闲鱼「我的-订单」页顶部，回到 infill 页面点一次「截屏」按钮。
   - **System response**: 后端执行 ① `adb connect <endpoint>`（已连则 idempotent） ② `adb shell screencap -p /sdcard/infill_xy_<seq>.png` ③ `adb pull` 到后端临时目录 ④ 立即把 PNG 提交 DashScope 解析订单结构（buyer_nickname / external_order_id / 下单时间 / listing_title / quantity）。本次截屏的解析任务独立异步跑（不阻塞用户下一次「截屏」点击）。
   - **User sees**: 主区右侧出现扫描卡片：顶部一行「已截屏 1 张」（黑字大号，随用户每次点击递增）。下方**截屏缩略图条**（横向 4 列 N 行网格，每张缩略图 120×80px，按截屏顺序排列），刚截的这张缩略图状态徽章 🔄 橙 pulse（解析中），解析完成后变 ● 绿（已解析）。缩略图条下方文案「正在解析第 1 张」。再下方实时显示已解析订单 mini 卡片列表（每条订单一行 mini 摘要：买家昵称 + 商品标题 + 数量），随解析进度增长。底部三按钮：「截屏」橙色 secondary（可继续点）+ 「完成截屏，开始解析」橙色 primary（点击进入 Step 4）+ 「取消」灰色 secondary。
   - **Details**: 「截屏」按钮在 ADB 命令进行中（通常 < 1 秒）短暂 disabled + spinner，命令返回后立即重新 enabled，用户可连续点。截屏 / 解析重叠跑：第 1 张正在 LLM 解析时，用户可以马上点第 2 张截屏 → 第 2 张排队等 LLM，不阻塞。

3. **User action**: 用户在模拟器内手动向下滚动一屏 → 回到 infill 点「截屏」→ 重复多次。每次点击之间用户自己判断是否需要继续（已抓到第一单 / 已看到重复出现的订单 = 应该停了）。
   - **System response**: 与 Step 2 相同：每次点「截屏」触发一次 ADB screencap + LLM 解析；缩略图条追加。
   - **User sees**: 截屏卡片顶部计数随点击递增（「已截屏 5 张」），缩略图条向右下方滚动累积；已解析订单 mini 卡片列表随后端解析完成不断追加新订单（同一 external_order_id 由后端去重，避免横跨两张 PNG 的重复行）。
   - **Details**: 没有自动止损 — 用户自己看模拟器判断停点。infill 不展示「检测到重复，是否停止？」之类提示（MVP 保持简单；多截 1~2 张的 token 成本 < 0.05 元，不值得做检测）。

4. **User action**: 用户判断已截够（看到第一单 / 已重复出现旧订单），点「完成截屏，开始解析」primary。
   - **System response**: 按钮 loading；后端等待所有进行中的 LLM 解析任务完成 → 汇总去重（同一 external_order_id 多次出现取一次）→ 二次 LLM 调用做 catalog SKU 匹配（MVP 采用「全量 catalog 注入 prompt」方式：把当前 catalog 全部 SKU 拼进 system prompt；catalog ≈ 50 SKU、几 KB 量级可行，超 200 SKU 后切 RAG）→ 返回 `{ ok: true, batch_id, items: [...] }`。前端自动跳转 CUJ-3。
   - **User sees**: 「完成截屏，开始解析」按钮转圈；其他按钮 disabled；缩略图条 / 已解析 mini 卡片列表保持可见。等所有 LLM 任务跑完后主区切到 CUJ-3 预览表格。
   - **Details**: 等待时间取决于剩余未完成的 LLM 解析任务数（典型 5~10 张已边截边解析，最终等待 10~30 秒）。

5. **User action**:（取消分支）截屏过程中或解析过程中点「取消」。
   - **System response**: 后端 abort 所有未完成 LLM 调用 + 标记 batch 废弃。前端退回 CUJ-2 初始态。
   - **User sees**: 控制栏回到「● ADB 就绪」态；扫描卡片消失；灰色 inline 提示「已取消上次扫描，未导入任何订单」（5 秒后消失）。
   - **Details**: 已截屏的 PNG 临时文件在 10 分钟内由后端清理（避免磁盘膨胀，本 PRD 不展开）。已发出的 LLM 调用 token 不退（与 CUJ-1 一致）。

### Edge Cases & Error States

- **ADB 连不上**（`adb connect` 抛 `connection refused` 或 `adb devices` 不含目标 endpoint）：状态指示器变红「● ADB 错」；主区右侧显示红色 err 块「无法连接到 ADB 设备」+ **三项检查清单**：① ADB 客户端是否已装在 Mac mini（`which adb` 给出路径） ② PC IP 是否可 ping 通（`ping <PC_IP>` 5 秒结果） ③ 模拟器 ADB 端口是否开（`nc -zv <PC_IP> <port>` 结果）。每项行首带 ✓/✗ 实时检查结果（在 probe 时一并返回）。底部「重新测试 ADB」橙色 secondary 按钮 + 「打开设置页修改 endpoint」灰色 link。
- **`adb` CLI 未安装**（后端 spawn `adb` 抛 `ENOENT`）：归入「ADB 错」，第一项「ADB 客户端是否已装」✗ + 安装提示（Mac mini: `brew install --cask android-platform-tools`）。
- **设备状态为 `offline`**（出现在 `adb devices` 但状态非 `device`）：归入「ADB 错」，三项检查后追加第 4 项「设备状态：offline — 请在 PC 上点模拟器内 ADB 调试授权弹窗，或重启模拟器」。
- **某次「截屏」点击 / pull 失败**（如 IO 错、设备临时无响应）：扫描卡片对应缩略图位置显示红色边框 + ✗ 徽章 + tooltip「截屏失败」；该张不参与最终 batch。「截屏」按钮立即重新 enabled — 用户可以再点一次（或先在模拟器内检查后重试）。infill 不阻塞用户继续扫描。
- **空订单页**（用户在没有任何订单的页面截屏，LLM 解析返回 0 条订单）：该张缩略图状态变 ● 绿但 tooltip「解析到 0 条订单」；mini 卡片列表不追加。用户可继续滚动 / 截屏，不强制中止。若用户最后点「完成截屏，开始解析」时整批 0 单，跳到 CUJ-3 空态（「未抓取到任何订单 — 请确认模拟器停在闲鱼"我的-订单"页」+ 「返回扫描页」）。
- **LLM 解析某张 PNG 失败**（响应解析错 / 超时）：该张缩略图徽章变红 ! + tooltip「解析失败」；最终 batch 不含该张的订单。「完成截屏，开始解析」点击时若整批 LLM 解析失败率超过 30% 弹 warning「本批 LLM 解析成功率较低（成功 X/N 张），建议补几张截屏后再点完成」（用户可选择仍进 CUJ-3 或继续补截屏）。
- **「完成截屏，开始解析」后 LLM 二次匹配整体超时**（端到端 5 分钟未完成）：后端 abort，错误卡片显示「SKU 匹配超时（5 分钟）」+ 已解析订单数 + 「带这些进预览（所有行红色低置信度）」/「丢弃重试」两个按钮。
- **小红书扫描进行中切到本 tab 点「开始扫描」**：按钮 disabled + hover tooltip「小红书扫描进行中，请等待或先取消」。
- **页面刷新 / 关闭**：扫描状态丢失；已写入数据库的订单不受影响。

### Mocks / Reference Designs

- `docs/ux/prd-006-auto-import-orders/cuj-2-initial.html` — 闲鱼 tab 初始就绪态
- `docs/ux/prd-006-auto-import-orders/cuj-2-captured.html` — 截屏已抓取（缩略图网格滚动累积）
- `docs/ux/prd-006-auto-import-orders/cuj-2-parsing.html` — 缩略图条 done/processing/queued + 右侧已解析订单 mini 卡片
- `docs/ux/prd-006-auto-import-orders/cuj-2-no-adb.html` — 红色 err 块 + 三项检查清单 + 「重新测试 ADB」按钮

### Acceptance Criteria

- 在 `/orders/import` 页切「闲鱼」tab 后，主区切换为闲鱼布局（橙 `#ff7a00` 强调色），并触发 `POST /api/orders/import/xianyu/probe` 探活 ADB。
- 左侧控制栏（360px sticky）显示状态指示器「● ADB 就绪」（绿）/「● ADB 错」（红）。
- 控制栏含「设备类型」下拉（默认 MuMu，可选蓝叠/雷电/USB真机）、「PC IP / endpoint」只读显示 + 「编辑」link 跳设置页、灰底操作说明块（4 步引导：手动滚动 + 逐次点截屏 + 手动判断停点 + 点完成）。
- 控制栏底部两按钮：「截屏」橙色 secondary（disabled 直到 ADB 就绪）+ 「完成截屏，开始解析」橙色 primary（disabled 直到至少有 1 张截屏）。
- 「ADB 错」态下两按钮都 disabled。
- MVP 不自动滚动 — 模拟器内的滚动手势由用户手动操作；infill 仅在「截屏」按钮被点击时触发一次 ADB screencap。后端不发任何 `adb shell input swipe` 命令。
- 用户每次点「截屏」触发独立的 ADB screencap + 异步 LLM 解析，主区扫描卡片顶部计数递增（「已截屏 N 张」），缩略图条追加一张（120×80px，状态徽章 🔄 解析中 / ● 已解析 / ! 解析失败 / ✗ 截屏失败）。
- 截屏与解析重叠跑（异步队列）；「截屏」按钮在 ADB 命令进行中短暂 disabled + spinner，命令返回后立即重新 enabled。
- 已解析订单 mini 卡片列表随后端解析完成不断追加；同一 external_order_id 由后端去重。
- 用户点「完成截屏，开始解析」后按钮 loading，后端等所有进行中 LLM 解析完成 + 跑二次 LLM SKU 匹配 + 返回 batch；前端自动跳转 CUJ-3。
- 「取消」点击后退回 CUJ-2 初始态，所有 batch 与临时截屏 PNG 丢弃；底部 5 秒灰色提示「已取消」。
- ADB 连不上时状态变红「● ADB 错」，主区显示红色 err 块「无法连接到 ADB 设备」+ 三项实时检查（ADB 客户端 / PC IP ping / 端口 nc）+ 每项 ✓/✗ 结果 + 「重新测试 ADB」橙色按钮 + 「打开设置页修改 endpoint」link。
- `adb` 未装时第 1 项 ✗ 并显示安装命令；设备 `offline` 时追加第 4 项检查。
- 单次「截屏」失败时该缩略图红边 + ✗ 徽章，扫描可继续（用户再点截屏即可）。
- 单张 LLM 解析失败时该缩略图红 ! 徽章；点「完成截屏，开始解析」时若整体失败率 > 30% 弹 warning「成功率较低，建议补几张截屏」+ 用户选择继续或继续补截屏。
- 「完成截屏，开始解析」后 LLM 二次匹配端到端 5 分钟未完成时 abort，错误卡片显示「SKU 匹配超时」+ 已解析订单数 + 「带这些进预览（所有行红色低置信度）」/「丢弃重试」两个按钮。
- 整批 0 单（用户始终在空页截屏）时跳 CUJ-3 空态。
- 小红书扫描进行中时本 tab「截屏」+「完成截屏，开始解析」均 disabled + hover tooltip「小红书扫描进行中，请等待或先取消」。

---

## CUJ-3：预览校对 + 一键导入

**Dependencies**: CUJ-1 或 CUJ-2（输入：扫描后的 batch — N 个订单 × M 个商品，含 LLM 匹配的 SKU + 置信度 + 去重查询结果）；复用 [PRD-001 订单管理](prd-001-orders.md) 的 `Order` / `OrderItem` 数据模型
**Priority**: P0（自动导入链路的最后护栏；用户在这里把 LLM 错的地方改对，把不该录的剔除，然后一键落库）

### Context

LLM 不可靠 — 标题里的「龙猫摆件 大号 灰白」也可能是「龙猫摆件 中号 灰白」的笔误版本；当一个商品标题与 catalog 任何 SKU 的语义距离都模糊时（confidence < 0.55），不能默认勾选 — 必须用户手指。同时一个外部订单常含 N 个商品（如买家一次下单 3 个不同款摆件），每项独立校对件数。重复单（用户上次扫描已导入过同一 external_order_id）必须默认不勾选，但要留 override 入口（罕见但真实场景：用户在数据库里手动删了订单想重导入，或导入了测试数据想换正式数据）。这个 CUJ 决定 batch 里到底哪些订单进 prd-001 待处理队列。

### Preconditions

- CUJ-1 或 CUJ-2 扫描完成，前端持有 batch `{ batch_id, source: 'xiaohongshu' | 'xianyu', items: [...] }`。每条 `item` 含 `external_order_id, buyer_nickname, external_created_at, is_duplicate: bool, existing_order_id: int | null, products: [...]`，每个 `product` 含 `listing_title, matched_sku_code: string | null, matched_sku_name: string | null, confidence: float, quantity: int`。
- 后端已暴露：① `POST /api/orders/import/sku-search?q=` SKU 搜索接口（picker 用）；② `POST /api/orders/import/commit` 批量导入端点（接收用户勾选的 items）。

### Journey Steps

1. **User action**: CUJ-1 / CUJ-2 扫描完成后自动进入本页（无主动操作）。
   - **System response**: 主区渲染预览表格。前端不再调任何接口（batch 数据已在前端态）。
   - **User sees**:
     - **顶部页面头**：面包屑「订单管理 / 自动导入 / 预览批次」+ 标题「预览批次」+ 右侧来源标签（小红书红 chip「来源：小红书千帆 · 42 单」/ 闲鱼橙 chip「来源：闲鱼 · 38 单」）+ 副标题「校对低置信度行的 SKU，处理重复单，一键导入到待处理队列」。
     - **顶部 chips 行**：4 个 chip 横排（淡色背景）显示批次概览：「✓ 高置信度 32 行」绿 / 「? 中置信度 8 行」黄 / 「! 低置信度 5 行」红 / 「↻ 重复订单 3 单」灰。chip 可点击切「只看本类」筛选（再点取消筛选）。
     - **主表格**：标题栏 `[全选 checkbox] 平台 / 外部订单号 / 买家+下单时间 / 商品`，第 4 列「商品」最宽。每行是一个外部订单：
       - 第 1 列 checkbox：高/中置信度且非重复 → 默认勾选；低置信度 / 重复 → 默认不勾选。
       - 第 2 列「平台」：小红书红 chip「小红书」/ 闲鱼橙 chip「闲鱼」。
       - 第 3 列「外部订单号」：等宽字体显示 external_order_id；重复行下方额外灰色 `重复` tag + 元信息「于 06-17 已导入 · order #87」+ 蓝色「改判为新单 →」link。
       - 第 4 列「买家 + 下单时间」：买家昵称（默认字号）+ 下方灰色小字下单时间（如「06-18 21:35」）。
       - 第 5 列「商品」：N 个商品子行纵向排列，每子行 inline 结构：`[置信度 emoji 圆形 badge ✓/?/!] [SKU picker 框] [× 件数 number input] [✕ 删除按钮]`。SKU picker 框显示「SKU 名 · CODE · 置信度数值」，整框可点击展开 picker 浮窗（详见 Step 3）。子行末尾「+ 添加商品」虚线按钮。
     - **行底色**：
       - 白底：行内所有商品均为高置信度 + 非重复
       - 浅黄 `#fffbe6`：行内**至少一个**商品为中置信度 + 非重复
       - 浅红 `#fff1f0`：行内**至少一个**商品为低置信度（**且**全行未勾选）
       - 灰底 `#f0f0f0`：重复订单（默认不勾选）
     - **底部 sticky 工具栏**：左侧文案 `将导入 X 单 · 共 Y 件商品`（X 为当前勾选订单数，Y 为这些订单的商品件数总和）+ bulk actions「全选新单 / 全不选 / 反选」 link 群。右侧两按钮：「取消，丢弃本批」（secondary，灰）/ 「导入勾选的 X 单」（primary，蓝）。
   - **Details**: 表格行数 50+ 时上方表头 sticky，底部工具栏 sticky；中间表格内部滚动。

2. **User action**: 浏览高置信度行 — 跳过（默认对的就不动）。
   - **System response**: 无；纯展示。
   - **User sees**: 32 行白底高置信度行，每行勾选框已勾、每个商品左侧绿 ✓ badge + SKU picker 框显示「龙猫-大号 · TOTORO-L · 0.92」，右侧件数 input 显示数字 1 / 2 等。
   - **Details**: 高置信度行用户通常一眼扫过，不点开 picker。

3. **User action**: 浏览中置信度行 — 点其中一个商品的 SKU picker 框校对。
   - **System response**: 浮窗（AntD `Popover`，宽 360px）从 picker 框下方弹出。
   - **User sees**: 浮窗内三段纵向：① 顶部当前匹配项（带置信度数值）+ 「原文标题」展示框（灰底等宽字体显示该商品在平台上的原文，如「龙猫摆件 大号 灰白款」） ② 中部 LLM 推荐前 3 个候选（每行：✓/?/! badge + SKU 名 + CODE + 置信度数值，按 confidence 降序） ③ 底部搜索框「搜索其他 SKU…」，输入文字后实时调用 `POST /api/orders/import/sku-search` 返回 catalog 全集匹配（按汉字 / 拼音 / code 模糊匹配，前 10 条）。每行点击即应用到当前 picker 并关闭浮窗。
   - **Details**: 浮窗下方一行小字「找不到对应 SKU？请先到 [产品录入](/intake) 录入新产品后再回来扫描」（自动连接到 prd-005 入口，避免用户卡死）。

4. **User action**: 中置信度行的某商品被换 SKU 后浮窗关闭。
   - **System response**: 前端态更新该商品的 `matched_sku_code` / `matched_sku_name` / `confidence = 1.0`（手选视为最高置信度）。
   - **User sees**: 该商品 badge 变绿 ✓；若该行所有商品都已是高置信度，行底色从浅黄变白。
   - **Details**: 手选后置信度数值显示为 `手选` 字样（不显示 `1.00`，避免误以为是 LLM 高置信度）。

5. **User action**: 浏览低置信度行 — 必须处理。
   - **System response**: 无；纯展示。
   - **User sees**: 5 行浅红底，每行勾选框未勾且**禁用 disabled**（hover tooltip「请先指定所有商品的 SKU 或删除商品，再勾选」）。每个低置信度商品左侧红 ! badge + SKU picker 框文案「⚠ 未匹配（原文：龙猫小号灰白）」红字 + 件数 input 红边。
   - **Details**: 低置信度商品的件数 input 默认为 LLM 抓到的件数（如 1），用户改 SKU 后红边消除。

6. **User action**: 用户点低置信度商品的 picker 框 → 浮窗内选「龙猫-小号 - 灰白」（实际对应的 catalog SKU）。
   - **System response**: 前端态更新；该商品 badge 变绿 ✓，picker 框正常显示。若该行所有商品都已指 SKU，行勾选框 enabled 且自动勾上、行底色从浅红变白。
   - **Details**: 这是低置信度行最常见的处理路径 — 用户花 3 秒指对 SKU，行就「转正」自动勾选。

7. **User action**:（删商品分支）某商品确实不应在订单中（如平台的赠品行 / 平台广告位被误抓）。点该子行末尾「✕ 删除按钮」。
   - **System response**: 前端态从该订单的 products 数组移除该项。
   - **User sees**: 该商品子行从订单行内消失，订单行的商品数量减 1。若删完订单内所有商品，订单行整行从表格消失 + 顶部 chip 计数同步。
   - **Details**: 删除不二次确认（误删可手动「+ 添加商品」补回，代价低）。

8. **User action**:（添加商品分支）订单缺一个商品（极罕见，如 LLM 漏识别 / 用户主动想搭单）。点订单行的「+ 添加商品」虚线按钮。
   - **System response**: 订单行末尾追加一个空商品子行，picker 框为「请选择 SKU」状态。
   - **User sees**: 空子行出现，用户点 picker → 浮窗 → 选 SKU → 件数 input 默认 1 可改。
   - **Details**: 添加的商品默认高置信度（手指视为可信）。

9. **User action**:（改件数分支）LLM 把件数识错（小红书标题里「龙猫摆件 ×3」被识成数量 1）。点商品子行的件数 number input 改为 3。
   - **System response**: 前端态更新；底部「共 Y 件商品」计数实时变。
   - **User sees**: 件数 input 显示 3。
   - **Details**: 件数 input 接受 1~999，0 / 负数显示红边 + tooltip「件数必须大于 0」。

10. **User action**:（重复单改判分支）某行是灰底重复单，但用户知道这是测试数据要重导入。点行内「改判为新单 →」link。
    - **System response**: 二次确认 `Modal`：「确认改判为新单？」+ 描述「订单 `<external_order_id>` 已于 06-17 导入（DB 中是 order #87）。改判后会作为新订单再次写入 — 这会在数据库内创建一条独立的 `Order` 记录，原 order #87 仍保留」。Modal 内两按钮「取消」/「确认改判」。
    - **User sees**: Modal 弹出；用户点「确认改判」。
    - **System response**: 前端态把该订单 `is_duplicate` 设为 false（但保留 `was_duplicate_overridden = true` 用于 stat 统计）；行底色从灰变白；勾选框 enabled 并自动勾上。
    - **Details**: override 后端的写入逻辑会**绕过** `(platform, external_order_id)` 唯一约束 — 这是真正的 override 路径（**假设 — 待确认**：DB schema 是否支持非唯一插入；MVP 简化方案是把 external_order_id 末尾追加 `-redo<N>` 后缀，仍满足唯一约束但保留可追溯。择一在数据模型阶段定）。

11. **User action**:（bulk actions 分支）用户想快速反选（先全不选再勾几个）。点底部「全不选」link。
    - **System response**: 所有订单 checkbox 变为未勾。
    - **User sees**: 工具栏文案变「将导入 0 单 · 共 0 件商品」+「导入勾选的 0 单」按钮 disabled。
    - **Details**: 「全选新单」= 勾选所有非重复 + 所有商品已有 SKU 的订单；「反选」= 翻转每行勾选状态（重复 / 含未匹配商品的行不能被强制勾选）。

12. **User action**: 校对完成，点底部「导入勾选的 X 单」primary 按钮。
    - **System response**: 按钮进入 loading。前端 `POST /api/orders/import/commit`，请求体含 `{ batch_id, items: [选中的 items] }`。后端**单事务批量**（**假设 — 待确认**）：① 对每条 item 校验 `(platform, external_order_id)` 唯一（未 override 且已存在 → 静默跳过该单计入「重复跳过」统计）② 校验所有 product_id 存在于 catalog ③ 创建 `Order(status='pending', created_at=now, external_created_at=item.external_created_at, platform=..., external_order_id=..., buyer_nickname=...)` 一对多 `OrderItem(product_id, quantity)` ④ 返回 `{ ok: true, stats: { 新增: a, 重复跳过: b, 手动跳过: c, SKU匹配率: 0.xx }, created_order_ids: [...], total_ms: ... }`。
    - **User sees**: 按钮转圈；其他按钮 disabled。
    - **Details**: 「手动跳过」= 用户未勾选的非重复订单数；「重复跳过」= 数据库唯一约束兜底命中的订单数（理论上为 0，因为前端已 surface 重复）。

13. **User action**:（成功分支）等到后端返回 `{ ok: true, ... }`。
    - **System response**: 主区切换为成功页。
    - **User sees**:
      - 中央大卡片：绿色大 `✓` 图标 + 标题「N 单已入待处理队列」+ 副标题（如「42 单已成功导入，正在 prd-001 待处理 Tab 等待你今晚排班」）。
      - **4 stat 网格**（2×2，每格白底圆角带数字）：① 新增 42 ② 跳过重复 3 ③ 手动跳过 4 ④ SKU 匹配率 0.91（最近 100 批的滑动平均，**假设 — 待确认**：是否本批 vs 历史，MVP 只展示本批）
      - **批次详情**（灰底说明条）：来源平台（小红书千帆 / 闲鱼）/ 扫描时间（如 `2026-06-18 21:35:12`）/ 扫描方式（Chrome 扩展 / ADB 截屏 8 张）/ 总耗时（如 `47.2s`）/ 批次号 batch_id（等宽字体）/ 平均置信度（0.81）
      - **前 5 单 ID 列表**：「新建 order #88 #89 #90 #91 #92 …」+ 蓝色「查看全部 →」link 跳到 `/orders` 待处理 Tab
      - 底部两按钮：「前往订单管理」primary（跳 `/orders` 待处理 Tab）/ 「继续导入闲鱼」secondary（自动切到另一平台 tab，回到该 tab 初始就绪态 — 若本批是闲鱼则按钮变「继续导入小红书」）
   - **Details**: 成功页**清空**前端 batch 态；用户回退到 `/orders/import` 也是初始扫描态。

14. **User action**:（失败分支）后端返回 `{ ok: false, error: "..." }`（罕见：catalog 中某 SKU 在校验阶段被删了 / DB 事务异常）。
    - **System response**: 主区切换为失败页，**保留** batch 前端态（用户可回上一步调整）。
    - **User sees**: 中央卡片红 ! + 标题「导入失败 — 未写入任何订单」+ 等宽字体错误详情 + 底部两按钮「返回预览继续校对」（primary）/「丢弃本批」（secondary，二次确认）。
    - **Details**: 单事务回滚保证「全成功 or 全不写」语义。

### Edge Cases & Error States

- **batch 为空**（扫描完没抓到任何订单）：主区不渲染表格，居中显示空态卡片「未抓取到任何订单」+ 描述「请确认 [平台具体说明] 或当天确无新订单」+ 「返回扫描页」按钮。
- **batch 全是重复单**（用户连扫两次没新单）：表格正常渲染但全是灰底；顶部 chip 显示「↻ 重复订单 N 单」+ 其他 chip 为 0；底部按钮文案「将导入 0 单」disabled；顶部黄色 alert「本批所有订单都已导入过，无新单可处理」+ 「返回扫描页」/「逐行 override」两个 link。
- **同一 batch 内两条订单 external_order_id 重复**（理论上扫描端去重，但兜底）：第二条显示橙色 inline tag「批次内重复」+ 默认不勾选；用户可选中其一勾选导入。
- **用户改 SKU 时 picker 浮窗搜索结果为空**（输入了 catalog 不存在的关键词）：浮窗中部显示「无匹配结果」灰字 + 底部 link「找不到对应 SKU？请先到 [产品录入](/intake) 录入新产品」。
- **catalog 在用户校对期间被改了**（用户开着预览页，去 prd-005 录入了新 SKU，回来想用）：用户点 picker 重新搜索可立即看到新 SKU（SKU 搜索接口每次实时查后端 → 后端读 DB → DB 由 prd-005 CUJ-5 写入后已同步），不需要刷新页面。
- **commit 阶段 catalog SKU 已被删**：返回 `{ ok: false, error: "product_id=NN 已不存在于 catalog" }`，失败页错误详情指出具体哪个 product_id；用户回预览删该商品后重试。
- **commit 网络超时**（30 秒）：前端 abort，失败页错误详情「连接超时」+「返回预览继续校对」；后端可能已写入部分订单（事务未关闭异常的小概率），用户回 prd-001 待处理 Tab 自查（极小概率事件，文案中不强调以免吓阻）。
- **行数 > 100**（罕见但合理）：表格内部独立滚动 + 表头 sticky + 底部工具栏 sticky；批次 chip 计数始终可见。
- **商品子行 > 20**（一单买太多）：订单行内部不滚动（垂直展开整页）；用户可手动折叠（点击订单号左侧 chevron `▾` 折叠 / 展开）（**假设 — 待确认**：MVP 是否实现折叠 — 50 单 / 天体量下罕见，可推迟）。
- **buyer_nickname 缺失**（LLM 没抓到）：第 4 列显示灰字「未知买家」+ tooltip「LLM 未识别到买家昵称 — 不影响导入，但 prd-001 列表会显示空」。
- **external_created_at 缺失**（LLM 没抓到）：第 4 列下方时间显示「时间未知」灰字；后端导入时用 `now()` 作为 `external_created_at` fallback。
- **用户在改 picker 时关浮窗（点页面其他位置）**：浮窗丢弃未选择项（与改前一致）。
- **预览批次不持久化**：预览批次只存内存（前端态 + 一份 mini 后端临时态），不进 DB。用户浏览器刷新 / 关 tab / 切到 infill 其他路由（如 `/orders` / `/intake`）→ 当前批次 in-memory 状态丢弃，需要重新扫描。前端不展示「未保存内容，确认离开？」之类拦截 prompt（MVP 简化；用户重扫成本 1~2 分钟，可接受）。只有用户点「导入勾选的 N 单」后，订单才真正落 `Order` / `OrderItem` 表；落库后这些订单不受预览页生命周期影响。后端临时 batch 缓存的 TTL 为 30 分钟（防止用户长时间不点导入后内存膨胀；TTL 到期则用户回到预览页时表格为空，等价于刷新）。

### Mocks / Reference Designs

- `docs/ux/prd-006-auto-import-orders/cuj-3-initial.html` — 预览表（12 代表性行：含高/中/低置信度、2 个重复行、2 个多商品行；第 3 行打开 picker 浮窗示意）
- `docs/ux/prd-006-auto-import-orders/cuj-3-success.html` — 导入成功页（绿✓ + 4 stat 网格 + 批次详情 + 前 5 单 ID + 两个 CTA）

### Acceptance Criteria

- CUJ-1 / CUJ-2 扫描成功后自动进入 `/orders/import` 的预览状态，面包屑「订单管理 / 自动导入 / 预览批次」。
- 顶部页面头含标题「预览批次」+ 来源 chip（小红书红 `#ff2442` / 闲鱼橙 `#ff7a00`）+ 订单总数 + 副标题。
- 顶部 4 chips 横排：✓ 高置信度（绿）/ ? 中置信度（黄）/ ! 低置信度（红）/ ↻ 重复订单（灰）；每个 chip 可点击切「只看本类」筛选（再点取消）。
- 主表格表头 sticky；列：[checkbox] 平台 / 外部订单号 / 买家+下单时间 / 商品。
- 行底色规则：白（全高置信度+非重复）/ 浅黄 `#fffbe6`（至少一中置信度+非重复）/ 浅红 `#fff1f0`（至少一低置信度，全行未勾选）/ 灰 `#f0f0f0`（重复订单）。
- 默认勾选规则：高/中置信度+非重复 → 勾；低置信度 / 重复 → 不勾。
- 低置信度行勾选框 disabled，hover tooltip「请先指定所有商品的 SKU 或删除商品，再勾选」；所有商品指对 SKU 后自动勾上 + 行底色变白。
- 商品列每子行结构：[置信度 emoji 圆形 badge ✓/?/!] [SKU picker 框：SKU 名 · CODE · 置信度数值] [× 件数 number input] [✕ 删除按钮]；子行末尾「+ 添加商品」虚线按钮。
- 手指 SKU 后 badge 变绿 ✓，picker 框置信度显示文案「手选」（不显示数值 1.00）。
- 点击 SKU picker 框弹出宽 360px 浮窗，三段：① 当前匹配 + 原文标题灰底等宽字体框 ② LLM 推荐前 3 个候选 ③ 底部搜索框（实时调 `POST /api/orders/import/sku-search` 返回前 10 条匹配）；浮窗底部 link「找不到对应 SKU？请先到产品录入录入新产品」跳 `/intake`。
- 搜索结果为空时浮窗中部「无匹配结果」灰字。
- 重复订单第 3 列含灰色 `重复` tag + 元信息「于 MM-DD 已导入 · order #N」+ 蓝色「改判为新单 →」link；点击后弹 Modal 二次确认「确认改判为新单？」+ 详细描述 + 「取消」/「确认改判」按钮。
- 「改判为新单」确认后行变白 + 勾选框 enabled 自动勾上；导入端点对该订单绕过唯一约束（按定的方案：直接绕过 or `-redoN` 后缀）。
- 件数 input 接受 1~999；0/负数红边 + tooltip「件数必须大于 0」。
- 「+ 添加商品」/「✕ 删除商品」即时改前端态；删空一单的所有商品时整行从表格消失。
- 底部 sticky 工具栏：左侧文案 `将导入 X 单 · 共 Y 件商品`（X / Y 实时随勾选变化）+ bulk actions「全选新单 / 全不选 / 反选」link 群 + 右侧「取消，丢弃本批」secondary + 「导入勾选的 X 单」primary。
- 「全选新单」勾所有非重复且所有商品已指 SKU 的行；「全不选」清空；「反选」翻转每行勾选状态（重复 / 含未匹配商品的行无法被强制勾上）。
- 「导入勾选的 X 单」点击调 `POST /api/orders/import/commit`，请求体含 `{ batch_id, items: [选中项] }`；按钮 loading 期间所有交互 disabled。
- 后端单事务批量创建 `Order(status='pending', created_at=now, external_created_at=item.external_created_at, platform=..., external_order_id=..., buyer_nickname=...)` + `OrderItem(product_id, quantity)`；任一失败整批回滚。
- 成功后主区切换为成功页：绿 `✓` + 标题「N 单已入待处理队列」+ 4 stat 网格（新增 / 跳过重复 / 手动跳过 / SKU 匹配率）+ 灰底批次详情（来源平台 / 扫描时间 / 扫描方式 / 总耗时 / 批次号 / 平均置信度）+ 前 5 单 ID 列表 + 蓝色「查看全部 →」link 跳 `/orders` 待处理 Tab。
- 成功页底部两按钮：「前往订单管理」primary 跳 `/orders` 待处理 Tab / 「继续导入<另一平台>」secondary 切到另一 tab。
- 失败时主区切换为失败页：红 ! + 标题「导入失败 — 未写入任何订单」+ 等宽字体错误详情 + 底部「返回预览继续校对」primary / 「丢弃本批」secondary（二次确认）。
- batch 为空时居中空态「未抓取到任何订单」+ 「返回扫描页」按钮；batch 全是重复时顶部黄色 alert「本批所有订单都已导入过」+ 两个 link。

---

## CUJ-4：自动导入设置

**Dependencies**: 无（设置是 CUJ-1 / CUJ-2 的前置；可不依赖其他 CUJ 独立配置）
**Priority**: P0（CUJ-1 / CUJ-2 都依赖此页配置的连通性）

### Context

CUJ-1 需要 Chrome 扩展、CUJ-2 需要 ADB endpoint。两者都是用户在自己环境里维护的配置（扩展装在自己 Chrome、ADB endpoint 是自己 PC 的 IP / 端口），infill 不能假设它们就绪。本 CUJ 提供一个**统一的设置中心**让用户在首次使用前 / 故障排查时检查两端的就绪状态，并配置 ADB endpoint。LLM key（DASHSCOPE_API_KEY）走 `.env` 是产品级配置，不在本页配（只链接到 prd-005 已有的 LLM 配置入口，**假设 — 待确认**：prd-005 是否已暴露"LLM 配置"页 — 如未，则本页直接显示一行说明「LLM key 配置走 `.env`，参见 `.env.example`」）。

### Preconditions

- 后端已实现 `GET /api/orders/import/xhs/extension-status` 检测扩展是否装（Chrome 扩展 ID 内置）。
- 后端已实现 `POST /api/orders/import/xianyu/test-adb` 测试 ADB 连通（输入：device_type / pc_ip / port，输出：连通状态 + 详细诊断）。
- 后端已实现 `GET /api/orders/import/xianyu/config` + `PUT` 持久化 ADB 配置（单用户系统，存到 settings 表）。

### Journey Steps

1. **User action**: 进入「系统设置 → 自动导入」（左侧导航 / 顶部导航 → 系统设置子菜单 → 自动导入）。或从 CUJ-1 / CUJ-2 故障状态点「打开设置页」link 进入。
   - **System response**: 路由切换到 `/settings/auto-import`，渲染设置页。前端并发：① `GET /api/orders/import/xhs/extension-status` ② `GET /api/orders/import/xianyu/config`。
   - **User sees**: 面包屑「系统设置 / 自动导入」+ 标题「自动导入设置」+ 副标题「配置两个平台的扫描通道。每个平台独立，可单独启用」。下方两张并列卡片（左右各占 50% 宽，间距 24px）。

2. **User action**: 浏览左卡片「小红书千帆 · Chrome 扩展」。
   - **System response**: 无；纯展示。
   - **User sees**: 卡片顶部 logo + 标题「小红书千帆」+ 来源 chip（小红书红）。下方状态行：
     - **已装态**：绿点「● 扩展已检测到 · v0.1.2」+ 灰字「最近探活时间：2 秒前」+ 「重新检测」secondary 按钮
     - **未装态**：蓝点「● 扩展未检测到」+ 蓝色 link「下载扩展（infill-xhs-scraper-v0.1.x.zip）」+ 4 步安装引导（与 CUJ-1 蓝色 setup 块同文案）+ 「我已安装，重新检测」secondary 按钮
   - **Details**: 「下载扩展」link 指向后端 `/static/extensions/infill-xhs-scraper-v0.1.x.zip`（**假设 — 待确认**：扩展打包与版本分发由 prd-006 的实现端配套）。

3. **User action**: 浏览右卡片「闲鱼 · Android ADB」。
   - **System response**: 无；纯展示。
   - **User sees**: 卡片顶部 logo + 标题「闲鱼」+ 来源 chip（闲鱼橙）。下方配置表单：
     - **「设备类型」下拉**：默认 MuMu（其他可选项：蓝叠 / 雷电 / USB 真机）。选项变化时下方「端口号」自动填默认（MuMu→7555 / 蓝叠→5555 / 雷电→5555 / USB真机→5037）。
     - **「PC IP」text input**：placeholder「如：192.168.1.100」+ inline 提示「跑模拟器的 PC 的局域网 IP；USB 真机也写 PC IP（adb 在 PC 上）」
     - **「端口号」number input**：按设备类型自动填默认，可改
     - **「测试 ADB 连接」橙色 primary 按钮**：点击触发 `POST /api/orders/import/xianyu/test-adb`，按钮 loading；结果回显在按钮下方：① ✓ 绿色「连接成功 · 设备序列号：MuMu-1080P · 系统：Android 9」 ② ✗ 红色「连接失败」+ 三项诊断同 CUJ-2 错误块（ADB 客户端 / PC IP ping / 端口 nc）
     - **「保存配置」secondary 按钮**：保存到后端 settings 表，弹绿色 message「已保存」（仅在表单字段被改后才点亮）

4. **User action**: 选择设备类型（如默认 MuMu）→ 填 PC IP（如 `192.168.1.100`）→ 端口已默认填好 7555 → 点「测试 ADB 连接」。
   - **System response**: 按钮转圈，后端执行 `adb connect 192.168.1.100:7555` + `adb devices` + 检查输出。
   - **User sees**: 按钮下方出现结果块：
     - 成功：绿框「✓ 连接成功 · 设备序列号：192.168.1.100:7555 · 系统：Android 9 (SDK 28)」
     - 失败：红框「✗ 连接失败」+ 三项检查清单（详见 CUJ-2 Edge Cases）+ 底部 link「[查看完整 ADB 错误日志]」（点击展开等宽字体后端日志）
   - **Details**: 测试连接不持久化配置（避免误操作下不可达的 endpoint 落库）；用户测试成功后须点「保存配置」才落库。

5. **User action**: 测试通过后点「保存配置」。
   - **System response**: 前端 `PUT /api/orders/import/xianyu/config`，请求体含 `{ device_type, pc_ip, port }`。
   - **User sees**: 弹顶部绿色 message「已保存自动导入配置」；保存按钮回到 disabled 态（表单未再变更）。
   - **Details**: 已保存的配置在 CUJ-2 进入时被读取（control bar 左上「PC IP / endpoint」只读显示用的是这份配置）。

6. **User action**: 浏览页底部「LLM 配置」说明区。
   - **System response**: 无；纯展示。
   - **User sees**: 卡片下方一行灰底说明条（圆角）：「LLM 匹配置信度阈值由系统固定（≥0.85 高 / 0.55~0.84 中 / <0.55 低）。LLM API key 走 `.env` 配置，参见 [LLM 配置](/settings/llm)（**假设 — 待确认**：此路由是否存在）或项目根目录 `.env.example`」。
   - **Details**: 如 prd-005 / 其他 PRD 已有 LLM 配置页则此 link 跳那里；如无则只显示文字「.env 配置 DASHSCOPE_API_KEY，参见 .env.example」。

### Edge Cases & Error States

- **`.env` 未配 `DASHSCOPE_API_KEY`**：本页不阻塞配置保存（ADB / 扩展配置仍可填），但页底说明条变红「⚠ 后端未检测到 LLM API key，扫描时 SKU 匹配会失败 — 请先在 `.env` 配置 DASHSCOPE_API_KEY 并重启后端」。
- **PC IP 留空但点「测试 ADB 连接」**：按钮 disabled + hover tooltip「请先填写 PC IP」。
- **端口非数字 / 超出 1~65535**：input 红边 + tooltip「端口应为 1~65535 的整数」。
- **测试 ADB 连接时端口被防火墙拦**（典型：MuMu 装在 PC 但 PC 防火墙拒绝外部连接 7555）：失败块中第 3 项「端口 nc」✗ + tooltip「PC 防火墙可能拦截了入站连接 — 请在 PC 添加规则放行 `<port>` 端口（或临时关闭防火墙测试）」。
- **「重新检测」扩展时浏览器还在打开本页但 Chrome 扩展进程刚卸载**：状态从「已装」变「未装」实时更新（探活每次都查实时）。
- **保存配置时后端返回 5xx**：弹红色 message「保存失败，请重试」+ 表单不重置（保留用户输入）。
- **用户在 CUJ-2 故障态点「打开设置页」跳过来**：从 CUJ-2 跳本页时高亮闲鱼卡片（橙边框 pulse 一次）+ 自动滚到该卡片位置，让用户知道焦点在这。

### Mocks / Reference Designs

- `docs/ux/prd-006-auto-import-orders/cuj-4-initial.html` — 两张并列卡片（小红书：扩展已装态 / 闲鱼：表单 + 测试按钮）+ 页底 LLM 配置链接

### Acceptance Criteria

- 左侧导航「系统设置」下存在子项「自动导入」，点击后 URL 为 `/settings/auto-import`。
- 进入页面时并发调用 `GET /api/orders/import/xhs/extension-status` + `GET /api/orders/import/xianyu/config`。
- 主区面包屑「系统设置 / 自动导入」+ 标题「自动导入设置」+ 副标题描述。
- 两张并列卡片，左「小红书千帆 · Chrome 扩展」（红 chip）/ 右「闲鱼 · Android ADB」（橙 chip）。
- 小红书卡片：扩展已装态显示绿点「● 扩展已检测到 · v0.1.x」+ 「重新检测」secondary 按钮；未装态显示蓝点「● 扩展未检测到」+ 下载 link + 4 步安装引导 + 「我已安装，重新检测」按钮。
- 闲鱼卡片含表单：「设备类型」下拉（默认 MuMu）/ 「PC IP」text input / 「端口号」number input / 「测试 ADB 连接」橙 primary 按钮 / 「保存配置」secondary 按钮。
- 设备类型变化时「端口号」自动填默认值（MuMu→7555 / 蓝叠→5555 / 雷电→5555 / USB真机→5037）。
- 「测试 ADB 连接」点击触发 `POST /api/orders/import/xianyu/test-adb`，按钮 loading；结果回显在按钮下方（绿框「连接成功 · 设备序列号 · 系统」/ 红框「连接失败」+ 三项诊断）。
- 「保存配置」仅在表单字段被改后点亮；点击触发 `PUT /api/orders/import/xianyu/config` 持久化；成功后顶部绿色 message「已保存自动导入配置」。
- 测试连接不持久化（必须点保存才落库）。
- PC IP 空 → 「测试 ADB 连接」disabled + tooltip「请先填写 PC IP」；端口非数字 / 超出 1~65535 → input 红边 + tooltip「端口应为 1~65535 的整数」。
- 页底有 LLM 配置说明条：「LLM 匹配置信度阈值由系统固定（≥0.85 高 / 0.55~0.84 中 / <0.55 低）。LLM API key 走 `.env` 配置」+ link 跳 LLM 配置页（如存在）或 `.env.example` 说明。
- `.env` 未配 `DASHSCOPE_API_KEY` 时页底说明条变红「⚠ 后端未检测到 LLM API key，扫描时 SKU 匹配会失败 — 请先配置」。
- 从 CUJ-1 / CUJ-2 故障态点「打开设置页」跳到本页时，自动滚动到对应平台卡片 + 该卡片橙 / 红边框 pulse 一次。
