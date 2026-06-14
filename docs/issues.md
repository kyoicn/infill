# Issues

> 待办池 — 按用户找时间挑做。新条目追加到 ## Open 段；做完移到 ## Closed。

## Open

### prd-005-followup-1: 补 12 个 Playwright E2E 覆盖 9 个 manual NOT_RUN 场景

**来源**: iter3 PM review（建议优先级 #1）
**优先级**: P1（高价值 / 估 1 开发日）
**Scope**:

iter3 QA 的 5 个 CUJ 都过了视觉手测和 happy-path 端到端，但有 9 个具体场景被标 `manual NOT_RUN`（人工没走完）。这些场景实际是覆盖空白，下次回归看不到：

- CUJ-1 拖动跨栏调整分类（DnD reclassify）
- CUJ-1 文件类型校验 reject 非图片
- CUJ-1 超大文件 reject（> 10 MB）
- CUJ-1 多文件混合 mime（PNG + JPEG + WebP 同批上传）
- CUJ-2 进度条 30 → 95 渐进 + 90 秒 frontend timeout 真触发
- CUJ-3 耗时输入框非法格式（如 "abc"）红边 + tooltip
- CUJ-3 增删 BOM 行 + 增删打印盘行 + 「所属组件」下拉同步
- CUJ-4 复制此列 + 删除某列 + 变体名重复红边
- CUJ-5 4 个 rollback 路径在浏览器侧的表现（backup_failed / write_failed / yaml_invalid / load_failed）

**Fix path**: 在 `frontend/` 下新增 Playwright 配置（`playwright.config.ts` + `npx playwright install`）+ `tests/e2e/` 目录写 9 个测试文件，每个 mock backend 响应 + 驱动 UI + 断言。可参考已有的 QA agent 的 Playwright 用法（`docs/qa-artifacts/iter3-*` 截图就是 Playwright 截的）。CI 接入可选。

---

### prd-005-followup-2: 修 4 处 AntD deprecation prop 改名

**来源**: iter3 QA 报告（LOW × 4）
**优先级**: P3（30 分钟 / 控制台噪声 / 不影响功能）
**Scope**:

升级到 AntD 6 后，几处旧 props 已 deprecated，浏览器控制台报 warning：

- `Alert.message` → 用 `message` 仍 OK 但官方建议改 `<Alert />` children pattern
- `Drawer.width` → `Drawer.size` （或 styles.body）
- `Spin.tip` → 需要被 `<Spin>` 包裹其它内容才显示 `tip`（独立用要带 children）
- `Statistic.valueStyle` → `Statistic.styles.value`

**Fix path**: grep `Alert\|Drawer\|Spin\|Statistic` in `frontend/src/pages/intake/*.tsx` 找全部用法，按 AntD 6 文档改。Build clean + 浏览器控制台无 warning 即闭环。

---

### prd-005-followup-3: 统一 MergeStats schema 中英文键 + 显式设 `response_model`

**来源**: iter3 TL code review（MEDIUM 漂移；当前 frontend fallback 兜住，functional 但 schema 是 dead docs）
**优先级**: P2（1 小时 / 契约清晰度）
**Scope**:

`backend/app/schemas_intake.py:MergeStats` 声明的是中文键（`新增组件 / 新增打印盘 / 新增产品变体`），但 `do_merge` 返回的是英文键（`components_added / plates_added / products_added`）。前端 `Success.tsx` 用中文优先 / 英文 fallback unwrap 兜住，所以端到端工作。但：

- Pydantic schema 是 lie — 谁照 schema 写客户端会读不到字段
- `merge` 端点没设 `response_model`，FastAPI 不强制校验响应 → drift 不会被自动发现

**Fix path**: 二选一对齐：
- (a) 把 `MergeStats` 改成英文键 + 同步改 `design-intake.md §1` 段（推荐 — 匹配代码实际值，最少改动）
- (b) 把 `do_merge` 返回值改成中文键 + 移除 frontend fallback（语义更接近 user-facing — 但要改代码 3 处 + 测试）

无论哪个：在 `routers/intake.py::merge` 加 `response_model=MergeResponse` 显式校验，防止未来再 drift。

---

### prd-005-followup-4: 用户决定 — 识别历史 / 草稿持久化

**来源**: iter3 PM review（建议 #4）
**优先级**: 待决（看实际使用反馈）
**Scope**:

MVP 故意不做草稿持久化（design-feature Q5 用户选了 (a)：关页面就丢，重做不痛）。但如果实际使用发现：
- 颜色矩阵填到一半浏览器崩了 / 误关 → 重传 + 重识别要花 30 秒 + DeepSeek API token
- 用户经常要回头看历史导入了什么（虽然 catalog 里能看产品名，但看不到当时的截图）

→ 立 prd-006「intake 草稿持久化 / 识别历史」。先收集 user 实际使用 N 次后再决定要不要立项。

---

### prd-005-followup-5: 用户决定 — 成组改色快捷操作

**来源**: iter3 PM review（建议 #5）
**优先级**: 待决（看实际变体数）
**Scope**:

design-feature Q6 用户选了「成组改色不做」（MVP 简化）。但如果 user 实际录入产品时变体数频繁 ≥ 5（如 10 种配色），每个变体改 6 个组件颜色 = 60 次点击。这时候「把这一列里所有灰色一起改成黄色」就有价值。

→ 立 prd-006 子 CUJ 或独立 prd「颜色矩阵成组操作」。同样先收集使用频率再立项。

---

### prd-005-followup-6: 跨 PRD 回归 — intake 产物在排班链路无副作用

**来源**: iter3 PM review（建议 #6）
**优先级**: P3（30 分钟 / 极低成本）
**Scope**:

intake 写入 catalog.yaml 后产生的 `床头柜 - 配色 1 / 黑白 / 黑粉` 三种 catalog 产品，理论上应该能立刻被 prd-001 (订单) / prd-002 (库存) / prd-003 (排班) 消费。但 iter3 QA 没做跨 PRD 验证。

**Fix path**: 手测一遍：
1. /products 看到 3 个新产品 + 6 个新组件 + 8 个新打印盘 ✓（QA 已确认）
2. /orders 录一个含「床头柜 - 灰白」的订单 → 看到 BOM 正确折算到 6 个组件需求
3. /inventory 看到 6 个新组件 × 3 种颜色 = 多条新库存行（按 catalog.yaml 的可选颜色生成）
4. /schedule 生成排班 → 8 张打印盘进入候选 → 可被算法选中

如有问题立 fix 任务。

---

## Closed

（空）
