# Dev Loop State

Last updated: 2026-06-13 16:11:29 (UTC+8)
Iteration: 2
Status: done

## Last Cycle Summary
- Scope: 纯测试加固，0 行生产代码改动
- Tasks executed: 3 parallel (T1 invariants / T2 pure-function units / T3 behavior breadth + ordering)
- Tasks passed: 3 (+1 post-merge fix by orchestrator + 1 quality fix by TL review)
- Tests passing: 131 (iter1 基线 74 → iter2 收尾 131；新增 57，其中 worktree 提交 55 + 主线补 2）
- Tests failing: 0
- QA inner loops used: 0（无功能变化，pytest 即为 QA）
- CUJ behavior changes: 无
- PM review skipped: 无 CUJ 旅程改动

## Coverage Closed (against iter1's audit gaps)

| 原 P0 缺口 | 状态 | 测试类 |
|---|---|---|
| `product_completion_score` 零直接单测 | ✅ Closed | `TestProductCompletionScore` (9) — 覆盖所有分支与三级 tie-break |
| `compute_effective_capacity` 零直接单测 | ✅ Closed | `TestComputeEffectiveCapacity` (7) — 含跨天、gap_loss、安全系数 |
| `pick_task` 仅 2 处间接 | ✅ Closed | `TestPickTaskDirect` (6) — 含 `remaining` 不被修改的强断言 |
| 不变量 (property) 断言 0 覆盖 | ✅ 3/5 Closed，2/5 弱 | 5 helper + 8 cross-scenario，但 `_assert_no_negative_supply` 与 `_assert_batch_quantity_conservation` 在 try_assemble 路径下结构性不可能触发（TL 评审揭示） |
| schedule_greedy 跨天 / 富余池清空 / sync 边界 / 策略偏序 0 覆盖 | ✅ Closed | `TestScheduleGreedyCrossDay` (3) / `TestSurplusPoolClearance` (5，含 2 个直击 scheduler_core.py:547) / `TestBoundarySync` (4，已改为调真函数 `_sync_penalty`) / `TestStrategyOrdering` (4，强化 deadline=800 让 pf=5 vs ut=2) |

## Implementation Commits
`59bb9e6` (baseline) → `4f39ca6` T2 → `5dab483` T1 → `3325c82` T3 → `78797de`/`c33fbf1`/`3a90735` 顺序合并（含 orchestrator 修：去 T3 公式克隆改调真函数 `_sync_penalty`、补 2 个 schedule_greedy 富余池兜底测试；TL review 修：强化 1 个冗余偏序测试）

## Next Focus (TL 评审产出的 P1 真实空白)

1. **替换 `_assert_no_negative_supply` 为真实的产销守恒断言**：`Σ produced − Σ assembled_BOM_consumed == final_supply`，能抓到 quantity lookup 错、双重计数等真 bug
2. **`try_assemble` 同优先级竞争测试**：两个 `(0, pid)` 单元都能从供给凑齐，断言谁赢 + 供给被正确扣除（当前 `break` 后从 index 0 重启的微妙顺序未锁）
3. **`count_complete_products` 补 4 个分支**：多产品 bom_map / BOM 含 qty=0 / 空 BOM / 孤儿供给
4. **`schedule_greedy` 也跑一遍不变量 sweep**：目前 `TestInvariantsAcrossScenarios` 只过 schedule_tasks 路径，生产代码 product_first/utilization 走的是 schedule_greedy
5. **`compute_effective_capacity` 补边界**：custom_start > deadline、safety_margin > 1.0
6. iter1 遗留：prd-003 CUJ-3 task pending UI 文案歧义、无库存行时 `+N` toast 谎报
