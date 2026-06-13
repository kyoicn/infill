# PM Review

Last updated: 2026-06-13 14:05:00 (UTC+8)
Iteration: 1（排班算法重构 Task-A/B/C/F）
Scope: prd-003-schedule CUJ-1（唯一被本轮代码路径实际触及的 CUJ）；prd-003 CUJ-2/3/4/5 与 prd-000/001/002/004 仅冒烟回归

## Overall Assessment

本轮迭代严格在「后端算法不动 UI / 不动 DB schema / 不动富余量级」红线内完成了三件事：(1) 把 product_first / utilization / two_phase 三策略统一收口到 `scheduler_core.schedule_greedy`（+ `schedule_tasks` 给 two_phase 用），(2) 把同步惩罚从旧版 multiplicative（会前缀支配 prod_score 的 bug）改写为 §9.3 描述的加法等效空闲分钟（`score = (prod_score, idle + sync_penalty, dur)`），(3) 在 two_phase 的 Phase 1 引入「凑整放弃」避免孤儿盘。74/74 单测 + 两轮独立 UI walk 在策略×同步强度网格上完全可复现，新 spec §9.2 的正交承诺（sync 不支配 prod_score）首次同时被代码、单测、活文档与真实浏览器行为四方对齐。这是一次收敛性迭代——没有引入新表面、没有触及用户旅程结构，唯一可见变化是 sync 滑块的「体感曲线」从陡峭跳变变为平滑可预测。

**Per-verdict 计数**：Satisfied = 1（in-scope: CUJ-1）；Caveats = 0；Not done = 0。其余 CUJ（prd-003 的 CUJ-2/3/4/5，prd-000/001/002/004 全部）本轮未进行完整产品判读，列在「未在本轮产品评审范围」段落。

## Per-CUJ Verdict

### CUJ-1（prd-003）: 生成排班表 — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定的三条产品判读维度逐条 walk：

1. **§9.2 正交承诺（product_first 的「凑齐优先」不被 sync 支配）**：
   - 活文档已重写：schedule_specs.md §9.3 明确 `score = (prod_score, idle + sync_penalty, dur)`，并加注「sync_penalty 加在 idle 上，绝不支配 prod_score → prod_score 不同时永远先按 prod_score 选（实现 §9.2 的正交承诺）」。
   - 代码已对齐：`scheduler_core.py:223` 实现的就是这个三元组；`_sync_penalty()` 仅返回一个等效空闲分钟标量，加进第二槽位，不可能反转 prod_score 排序。
   - 行为已验证：QA 的 `test_product_first_bottleneck_over_sync` 单测显式锁定「sync 高时仍按 prod_score 选瓶颈件」；两轮 manual walk 观察到 product_first sync=100 下前 14 批依旧稳定打高优先级瓶颈 config 5（棕色），未被对齐机制截断。
   - **结论：spec 文字 ↔ 代码实现 ↔ 单测 ↔ 浏览器观察行为，四方首次完全一致。** Journey Step 3「切换策略」与 Step 5「拖动同步强度」的承诺（schedule_specs.md §5.4「三策略与同步强度、产品过滤完全正交可自由组合」）首次有可观察证据支撑。

2. **加法软体感符合 user 设计意图（"更愿意为对齐推迟" 而非 "强制对齐"）**：
   - schedule_specs.md §9.3 的等效空闲分钟示例（anchor=100, candidate=150, changeover=15, mult=4 → sync25/50/75/100 分别对应 1.9 / 7.5 / 16.9 / 30 分钟）显示惩罚是「可感但有上限」的——sync=100 下时长偏差 50% 的候选任务背一个 30 分钟等效空闲，足以排到后面，但绝不像旧版 multiplicative 那样直接前缀支配。
   - `schedule_tasks`（two_phase Phase 2）的 `wait_time = t_earliest + (t_latest - t_earliest) * sync_strength / 100`（`scheduler_core.py:425`）是「为对齐推迟批次启动」这条 user intent 的显式杠杆——sync=100 时新批次直接等到最慢打印机就位。
   - QA 双轮 walk：product_first 下 sync 0→50→100 对应批次数 20→19→19，单调 ≤ 成立，没有跳变；用户拖动滑块感受到的是「松/紧」连续光谱，与 Journey Step 5 灰字提示「平衡最优任务和同批次打印机完成时间对齐」的语义一致。
   - **结论：体感曲线与 user intent 匹配——可感、可控、不暴力。**

3. **从旧 multiplicative 到新 additive 是改进而非回归**：
   - 旧 multiplicative 同步惩罚（被审计标为 P0-2）有两个产品级问题：(a) 同步因子作为元组前置可支配 prod_score（违反 §9.2）；(b) 三策略实现不一致（scheduler.py 走 multiplicative、scheduler_core.py 走 additive），同 sync 值在不同策略下不可预测。
   - 新 additive 一次性解决两个问题：单一公式 `_sync_penalty()` 被 `pick_task` 和 `schedule_tasks` 共用（`scheduler_core.py:152, 221, 404`），三策略行为可解释；惩罚作为标量加进 idle 维度，元组结构保证 prod_score 优先。
   - 从用户视角，sync 滑块的「体感」从「拉到 80 突然行为大变」（旧版高强度下惩罚指数级膨胀）变为「拖动哪里都有可预测的渐进效果」（新版二次方缩放，低强度近无惩罚、高强度可控制最大值约 30 分钟等效空闲）。
   - **结论：改进，无可察觉回归。**

**Acceptance Criteria 核对**（按 prd-003 CUJ-1 的 6 条）：
- AC1（生成卡片字段集）：PASS（manual walk 两轮一致）
- AC2（初值：明天 / 00:00 / 24h / product_first / 富余开 / 指定产品空 / sync 50）：PASS
- AC3（切换策略/富余/sync 时灰字说明随之变化）：PASS
- AC4（指定产品过滤的清除按钮 + 提示）：NOT_RUN — **PM 接受 QA 的非阻塞理由**：本轮重构未触碰前端过滤 UI 与 `target_product_ids` 后端代码路径，回归风险极低；建议在「Recommended Next-Iteration Priorities」中收录补测。
- AC5（成功 toast / 列表加入并展开详情；重叠时红 toast 不新增）：PASS（overlap 边界两轮一致：HTTP 400 + 「与已有排班（2026-06-14 00:00，24h）时间重叠」）
- AC6（草稿橙 Tag）：PASS

**Caveats / gaps**: 无。AC4 NOT_RUN 不是缺陷，是本轮 scope 的合理裁剪。

**Spec gap**: 本轮反而**收紧**了 spec gap——schedule_specs.md §9.3 重写后从「描述已过时 multiplicative」变为「与代码一致的 additive 活文档」。`SURPLUS_TARGET_PRODUCTS` 也已被 Task-C 归一到单一常量（scheduler_core.py:25 = 20），与 §7.3「当前值 20」一致。prd-003 § 「已知问题」#1（双实现分歧）与 #2（口径不一）应在下次 backfill PRD 时移除——但**本轮不动 PRD**（按 Section 6 规则，PM review 只能动 PRD frontmatter status，不动正文）。

### CUJ-2 / CUJ-3 / CUJ-4 / CUJ-5（prd-003） — 未在本轮产品评审范围

按本轮 scope（仅算法重构），这 4 个 CUJ 的代码路径未被触及，QA 仅作冒烟回归（页面可达、详情卡片渲染正常、利用率表/甘特图/批次卡片结构性元素未崩）。**首轮 PM Review 不对其做完整产品判读**——它们仍带有 prd-003 backfill 时记录的「已知问题」（任务 pending 文案「进行中」歧义、完成入库无库存行时数字不符、specs.md §7 编辑能力未完全落地、跨夜时间解析、闹钟前端单实例丢失等），但本轮未引入新问题、也未修复旧问题。下次产品评审会对这些 CUJ 做首次判读。

### prd-000 / prd-001 / prd-002 / prd-004 — 未在本轮产品评审范围

冒烟回归确认页面可达且数据正常加载（QA 截图：orders / inventory / settings / dashboard）。本轮无任何代码改动接触这些 PRD，无新产品判读。

## PRD Lifecycle Changes

- **无 PRD frontmatter 翻转**：prd-003 仍保持 `status: active`（CUJ-1 单条 Satisfied 不足以让整份 PRD 完结，CUJ-2/3/4/5 尚未做首次 PM 判读）。其余 PRD 同理。
- 本轮算法收敛是底层质量提升，不构成「功能完结」信号——保留 active 是正确的状态表达。

## Recommended Next-Iteration Priorities

按对 user-perceived 产品质量的边际收益排序，供下一轮 planner 直接消费：

1. **修复任务 pending 文案歧义（prd-003 CUJ-3 已知问题 #6）** — 当前任务 `status=pending` 被前端 `taskStatusTag` 渲染为「进行中」（绿色暗示已启动），而批次 pending 渲染为「待开始」。同一字段在任务和批次上语义反向，是真实用户在执行期最容易误读的 UI 点。一处前端文案改动 + 颜色 Tag 调整即可。零算法风险，高产品收益。

2. **修复完成入库无库存行时数字不符（prd-003 CUJ-3 已知问题 #7）** — 当 `(component_id, color)` 在 `Inventory` 表无对应行时，`POST /tasks/{id}/complete` 接口返回 `added_quantity=盘产量`、toast 显示「库存 +N」，但库存实际未增加。这是典型「UI 撒谎」型缺陷，必现且会造成用户对库存数字失去信任。修复路径明确：要么后端先 `get_or_create` 库存行再 +quantity，要么 `added_quantity=0` 不显示 toast 数字。建议前者（与 §10 滚动供给池语义一致）。

3. **补做 prd-003 CUJ-1 AC4 端到端回归（指定产品过滤的清除按钮 + 提示）** — 本轮非阻塞遗留。建议放入下一轮 manual walk 清单，无需新代码。

4. **prd-003 CUJ-2/3/4/5 首次 PM Review** — 本轮跳过的 4 个 CUJ 需在不引入新代码改动的情况下，做一次纯产品判读（基于现状代码 vs PRD 正文）。预期产出：这 4 个 CUJ 的 Satisfied / Caveats / Not done 三色标，作为下一次决定「prd-003 是否可 active→completed」的依据。

5. **（非阻塞）下沉 `changeover=15` 内联默认值** — QA Recommendation #2 指出 `routers/schedule.py.start_batch` 与 frontend `Schedule.tsx.changeoverMin` 仍是内联 `15`，仅 `scheduler_core.DEFAULT_CHANGEOVER_MINUTES` 集中。当前不影响行为（值一致），但属于「常量漂移」隐患。建议在下次轻量重构窗口顺手归一。

6. **（非阻塞）prd-003「已知问题」段落更新** — Task-A/C 已修复其中两条（multiplicative 不一致、SURPLUS_TARGET_PRODUCTS 口径不一），但 prd-003 正文仍引用旧状态。建议在下次 prd-003 编辑窗口顺手清理这两条历史标注（本轮 PM Review 不直接编辑 PRD 正文）。

7. **（非阻塞）antd 5 弃用警告清理** — QA Recommendation #3。纯前端打扫，与产品行为无关，可累积到「技术债清扫」迭代。

**优先级理由**：1 和 2 是真实用户每天会接触并误读 / 误信任的 UI 点，是「product-level bug」而非「engineering edge case」。3 和 4 是评审债务清理。5/6/7 是低风险技术债。
