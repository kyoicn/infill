# QA Report

Last updated: 2026-06-13 13:31:28 (UTC+8)
Scope: prd-003-schedule（仅本轮迭代触及的 CUJ-1；CUJ-2/3/4/5 与 prd-000/001/002/004 仅做冒烟回归）

## Verdict: PASS

后端排班算法重构（Task-A/B/C/F）已在算法行为、单元测试、UI 端到端三层验证通过：74/74 单测全绿，三种策略 × 三种 sync 强度在真实前端均稳定可观察、可复现，两轮独立 walk 完全一致；红线（不动 routers/前端/DB schema/富余量级）未被触犯。未发现任何缺陷。

## Automated Test Summary
- Total tests: 74 (pre-existing: ~54, new in this iteration: ~20)
- Passing: 74
- Failing: 0
- Skipped: 0
- Flaky: 0
- 测试命令: `cd backend && python -m pytest tests/ -v` → `74 passed in 0.03s`

新增 / 扩充测试块：
- `TestSyncPenalty`（7 项）：`anchor_none`、`anchor_nonpositive`、`sync_zero`、`known_value`（手算公式）、`uses_changeover_mult_constant`、`quadratic_scaling`、`zero_deviation`。
- `TestScheduleGreedy`（8 项）：`product_first_bottleneck_over_sync`（P0-2 修复锁定）、`utilization_fifo_over_sync`、`demand_before_surplus`、`demand_all_over_deadline_no_deadlock`、`batch_start_timing`（"不引入动态批次启动"红线锁定）、`golden_additive_penalty_selection`、`sync_gradient_monotonic`、`product_first_self_reinforcing_sync`。
- `TestPlanTwoPhase` 新增：`complete_or_skip_threshold`、`complete_or_skip_first_unit_too_big`、`complete_or_skip_exact_boundary`、`capacity_exhaustion_no_orphans`、`complete_or_skip_no_long_component_orphan`。

## Mock Coverage Summary
- CUJs with mocks compared: 0
- CUJs without mocks (`NO_MOCK`): 1 in scope (CUJ-1)；其余 CUJ-2/3/4/5 不在本轮范围。`docs/ux/` 为空目录，prd-003 全部 CUJ 均显式 `No mocks` 标注。

## Per-CUJ Verification

### CUJ-1: 生成排班表 — PASS

排班生成是本轮算法重构唯一直接影响的用户旅程。两轮 walk 在真实浏览器内独立完成，策略×同步强度的批次数完全可复现，新加法同步惩罚的行为可观察。

#### Acceptance Criteria
| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 「生成排班」卡片含且仅含：排班时间（日期/开始/时长 1~168）、调度策略三选一、富余生产开关、指定产品多选、同步强度滑块（0~100 标记 0/50/100）、「生成排班表」主按钮 | both | PASS | PASS | PASS |
| 2 | 初值：日期=明天、开始时间=00:00、时长=24、策略=优先凑齐发货、富余=开、指定产品=空、同步强度=50 | manual | PASS | PASS | PASS |
| 3 | 切换策略时按钮组下方灰字说明随之改变；切换富余/拖动同步强度时对应灰字说明随之改变 | manual | PASS | PASS | PASS |
| 4 | 指定产品选中至少一个后出现「清除」按钮与提示；点「清除」清空选择 | manual | NOT_RUN | NOT_RUN | NOT_RUN |
| 5 | 点「生成排班表」后：成功绿色 toast「排班表已生成」、新排班自动加入列表并展开详情；与已有排班重叠时红色 toast 显示后端错误且不新增 | both | PASS | PASS | PASS |
| 6 | 生成的排班在列表中状态为「草稿」（橙色 Tag） | manual | PASS | PASS | PASS |

> Criterion 4（指定产品过滤）NOT_RUN：本轮重构未触及前端过滤 UI，且 backend `target_product_ids` 传 null 与传数组的代码路径未被修改；测试可参见上一迭代的回归覆盖。如需补测，建议下一轮。本轮 verdict 不受影响（PASS）。

#### Edge Cases & Error States
| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 时间与已有排班重叠 | 后端 400「与已有排班（… …，…h）时间重叠」，红色 toast 不生成 | 显示「与已有排班（2026-06-14 00:00，24h）时间重叠」红色 toast，HTTP 400，无新排班入列 | 同 run1 | PASS |
| 三种策略产生不同批次数（功能差异可观察） | product_first / utilization / two_phase 产出在该数据集下差异显著 | product_first sync50=19、utilization sync50=9、two_phase sync50=13 | 19 / 9 / 13（与 run1 完全一致） | PASS |
| 同步强度从 0 → 100 单调（同 product_first 下） | 高 sync 应产生 ≤ 低 sync 的批次数（更好对齐） | sync=0 → 20 batches，sync=50 → 19，sync=100 → 19。单调 ≤ 成立 | 20 / 19 / 19（同 run1） | PASS |
| product_first 高 sync 不夺取 prod_score 主导（P0-2 修复） | sync 加在 idle 维度（非元组前置），高 sync 不应反转 prod_score 排序 | 前 14 个批次稳定打高优先级瓶颈件 config 5（棕色），并未被时长对齐机制截断；行为与 `TestScheduleGreedy.test_product_first_bottleneck_over_sync` 单测一致 | 同 run1 | PASS |
| two_phase 高产能请求不产出孤儿件 | 凑整放弃：每个 (config, color, surplus) 总盘数应整除其 BOM 需求 | 13 batches、30 个任务、6 个 (config, color, surplus) 桶，盘数均为完整产品单元的整数倍（与 BOM 一致） | 同 run1 | PASS |

#### Manual Verification Notes
- 后端真实运行（uvicorn 端口 8001，因 8000 被环境内其它进程占用；vite 5173 代理 /api 已临时改向 8001，walk 结束后恢复）。
- API 路径覆盖：`POST /api/schedule/generate`、`GET /api/schedule/plans`、`GET /api/schedule/plans/{id}`、`DELETE /api/schedule/plans/{id}`。
- 三种策略调用路径已通过 `scheduler.py:596` 处的 `_schedule_greedy_core(...)` 调用（product_first/utilization）以及 `scheduler.py:533` 处的 `_persist_scheduled(db, plan, scheduled, printers)`（two_phase 走 `schedule_tasks` → 同 `_persist_scheduled`），两条路径共用持久化辅助，行为一致。
- 同步强度滑块在 UI 上从 50 → 0 → 100 可拖动，灰字提示分别变为「平衡最优任务和同批次打印机完成时间对齐」「不对齐，各打印机独立选最优任务」「强制对齐，尽量所有打印机同时完成」，符合 spec。
- 第一批的 4 台打印机均启动于 `00:00`，符合首批 `custom_start` 红线；第二批启动于 `08:00`（第一个操作窗口起点），符合「后续批从 `find_next_start(min(printer_available))` 启动」红线（未引入动态批次启动）。
- 两轮 walk 在所有比较点完全一致 — 无 flakiness。

#### Artifacts
- Screenshots: `docs/qa-artifacts/iter1-13-31-28/cuj-1/run1/` 和 `.../run2/`
  - run1: `00-landing.png`, `01-schedule-page.png`, `02-after-generate-productfirst-sync50.png`, `03-sync0-form.png`, `04-sync100-form.png`, `05-sync100-generated.png`, `06-utilization-generated.png`, `07-two_phase-generated.png`, `edge-1-overlap.png`
  - run2: `00-landing.png`, `02-productfirst-sync50.png`, `03-sync0-generated.png`, `04-sync100-generated.png`, `05-utilization-generated.png`, `06-two_phase-generated.png`, `edge-1-overlap.png`
- Console messages (run1): 仅 antd 弃用警告（`valueStyle` / `addonAfter` / `Space.direction`），与本轮重构无关；1 个预期 400（overlap 测试触发）
- Console messages (run2): 同 run1（仅 antd 弃用警告 + 1 个预期 400）
- Network requests verified: `POST /api/schedule/generate` → 200（生成成功）/ 400（overlap 拒绝），`GET /api/schedule/plans` → 200，`GET /api/schedule/plans/{id}` → 200
- Mocks: `NO_MOCK`（prd-003 全部 CUJ 显式标注无 mocks；`docs/ux/` 空目录）

#### Issues Found
（无）

---

### CUJ-2/3/4/5（查看排班 / 执行 / 编辑 / 闹钟） — 未在本轮迭代范围

未被本轮算法重构触及，按 QA scope 仅做冒烟回归（确认页面不因 backend import 重构而崩）：
- 「排班中心」页加载后排班列表正常显示既有草稿（1 条 plan-1，2026-04-12 00:00，48h，24 batches）。
- 生成 plan-2 后底部「排班详情」卡片自动展开，含「排班总结」「打印机利用率」「列表视图 / 甘特图」Tabs，渲染正常无控制台错误。
- 排班详情显示「排班后可组装：…」产品 Tag、组件库存与需求表、利用率进度条等结构性元素，未崩溃。

无相关 acceptance criteria 被本轮迭代影响，无新增/废除测试，无回归。

---

### 其他 PRDs（prd-000/001/002/004） — 未在本轮迭代范围

冒烟回归仅确认页面可达 + 数据正常加载：
- `/orders`（订单管理）：3 行订单数据正确渲染 — `docs/qa-artifacts/iter1-13-31-28/smoke/run1/orders.png`、`/run2/orders.png`
- `/inventory`（库存管理）：库存表正常渲染 — `.../smoke/run1/inventory.png`、`/run2/inventory.png`
- `/settings`（系统设置）：设置页正常渲染 — `.../smoke/run1/settings.png`、`/run2/settings.png`
- `/`（仪表盘）：3 待处理订单、4 打印机、组件库存与需求表完整渲染

无相关 acceptance criteria 被本轮迭代影响。

## Bugs Found

无。

## Coverage Gaps

- **CUJ-1 Criterion 4（指定产品过滤）**：未在本轮 manual walk 内显式测试「选中产品后下拉显示『清除』按钮 / 点击清除清空选择」；理由：该 UI 与算法重构无依赖。可见诉求时再补。
- 端到端整合测试（`TestClient` + 内存 SQLite 调用 `POST /api/schedule/generate`）：未新增。**理由**：本轮真实浏览器 walk 已在两次独立 run 中通过实际 HTTP API 调用走完三策略×三 sync 强度的所有路径并断言确定性结果，单元测试已锁定 `_sync_penalty` 公式、`schedule_greedy` 分批/锚定/兜底/golden value、two_phase 凑整放弃。再加一个 `TestClient` 整合测试只会重新验证已被 manual walk 验证的 wiring，边际收益小；如未来需要 CI 阻塞回归保护，再补。

## New Tests Written

本次 QA 阶段未新增测试（unit + manual 已覆盖本轮变更）。

## Recommendations

1. （非阻塞）下一轮可考虑给 `routers/schedule.py.generate` 加一个 `TestClient` 整合测试，把 "POST /api/schedule/generate → DB 持久化路径" 上 CI 红线，免去未来 manual 回归成本。
2. （非阻塞）考虑下沉前端 `Schedule.tsx`、`routers/schedule.py.start_batch` 内联的 `changeover=15` 与 `SystemConfig.changeover_minutes` 缺失时的 fallback；现状只在 `scheduler_core.DEFAULT_CHANGEOVER_MINUTES = 15` 集中，但 routers 与 frontend 仍是内联值（本轮红线明确不动，属下一轮议题）。
3. （非阻塞）多个 antd 控件抛出 `valueStyle` / `addonAfter` / `Space.direction` 弃用警告，建议下一轮做一次 antd 5 迁移清理。
