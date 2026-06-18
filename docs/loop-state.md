# Dev Loop State

Last updated: 2026-06-18 22:55:00 (UTC+8)
Iteration: 4
Status: continue

## Last Cycle Summary
- Scope: 交付 prd-006「自动导入订单」全 4 CUJ（小红书 Chrome 扩展 DOM 抓取 + 闲鱼 ADB 截屏 LLM 解析 / preview 校对 inline 编辑 + 单事务 commit + `-redoN` override / 设置页：扩展状态 + ADB 配置 + 测试）
- Tasks executed: 14（5 组 G1=3 / G2=3 / G3=2 / G4=3 / G5=4 — 实操中 G5 因 session 限制由 main thread 直接落地）
- Tasks passed QA: 14 — 全部通过（initial QA FAIL → retry 1 fix 5 个 MEDIUM+ bug 后 PASS）
- Tasks rolled back by QA: 0（在 retry 1 inline 修复，未回滚到 in-progress）
- Tests passing: 344（baseline 286 → +58 新加 auto-import + adb + LLM + redoN + E2E + retry-1 gap tests）
- Tests failing: 0
- QA inner loops used: 1 of 2
- CUJs completed this cycle: prd-006 CUJ-1 扫描小红书千帆 + CUJ-2 扫描闲鱼 + CUJ-3 预览校对+导入 + CUJ-4 自动导入设置
- CUJs remaining: 0（prd-006 4 个 CUJ Impl=merged + QA=PASS；PM=Caveats × 4，PRD frontmatter status 保持 `active`）

## QA Gate
- Verdict: PASS（initial FAIL → retry 1 PASS）
- Fabrications found: 0
- HIGH bugs found: 2 → 全部闭环
  - `adb_connected = bool(list_devices())` 不校验配置 endpoint（router）→ commit `1b5f35f`
  - 前端 XianyuTab 只看 `adb_connected` 不参考 diagnostics → 同 commit
- MEDIUM bugs found: 3 → 全部闭环
  - XhsTab 扩展未装态缺 zip 下载主按钮 → commit `cce7b19`
  - 同上 mock visual deviation → 同 commit
  - PreviewTable items.length === 0 缺空态 UI → 同 commit
- LOW bugs forwarded: 占位 xhs/probe 端点、Spin tip deprecation、TL review 5 项 carry-over（N+1 重复查询、串行 LLM 调用、payload 无 max_length、CORS 全开、扩展硬编码 backend URL）
- Tasks rolled back: 0

## PM Gate
- Verdict: 0/4 Satisfied / 4 Caveats / 0 Not done
- prd-006 frontmatter 保持 `active`（未升 completed —— PM 判 4 CUJ 全部 Caveats）
- 关键 product 风险：CUJ-4 缺 LLM key 红色提示（违 AC #14）、CUJ-1/2 缺「跳过 SKU 匹配」escape hatch、跨 tab 互锁不对称、「继续导入<另一平台>」用 reload 而非 tab 切换、筛选 chips 取向差异、重复单元信息缺日期前缀

## Iter4 Implementation Commits（abe4f28 之后约 20 个）
`908f9f9` docs(prd-006) 设计 + 11 mocks → `ca8e847` G1 LLM chat_completion 抽 → `93227fd` G1 Order schema + partial unique index → `3dfc17d` G1 Chrome ext scaffold → G1 三 merge → `374587b` G2 ADB client + 诊断 → `1bc1e68` G2 LLM SKU + 闲鱼 OCR + sku-search → `9404e9f` G2 router + commit 原子性 + `-redoN` → G2 三 merge（手动解 schemas / services 三方冲突）→ `cbb8bf2` G3 frontend api.autoImport + extension.ts → `b211c94` G3 CUJ-4 AutoImportSettings + entry buttons → `0040d6d` G3 wiring local stub → 3.1 api → `90d20a6` G4 CUJ-1 AutoImport 父 + XhsTab + ScanningProgress + 6 stubs → `7dd8ead` G4 CUJ-2 XianyuTab + ScreencapGrid → `19ae738` G4 CUJ-3 PreviewTable + SkuPicker + Success/Failure → G4 三 merge（4.2/4.3 覆盖 4.1 stubs）→ `058aa88` G5 main.py lifespan + router + static mount + .env.example + .gitignore → `679d939` G5 build script 镜像到 static + README + checklist → `c73409f` G5 status.md prd-006 翻牌 + iter4 活动表 → `3882d5e` G5 E2E happy path + atomicity + xianyu screencap → `973fae5` Phase 1/2 design + planner 文档同步 → `258051d` Phase 3.6 TL fix（tuple/dict 解包 + dead TYPE_CHECKING 清理）→ `0b4436e` Phase 4 QA initial 文档 + gap tests → `5d10710` Phase 4 QA retry 1 fix HIGH adb_connected + `cce7b19` retry 1 fix MEDIUM xhs+preview ui

## Next Focus（PM 建议优先级 + carry-overs）

1. **加固回合（推荐独立 iter5）**：在升 prd-006 `active → completed` 之前结清 PM caveats + TL 5 项 carry-over，目标是把 4 CUJ 从 Caveats 拉到 Satisfied。
2. **HIGHEST**：CUJ-4 LLM key 未配置红色 alert（违 PRD AC #14） — 作坊主首次配置最容易踩的坑。
3. CUJ-1/2 LLM 服务降级 escape hatch「跳过 SKU 匹配进 CUJ-3 全红低置信度」（PRD AC #14/15/16）。
4. 跨 tab 互锁对称：XianyuTab 加 `otherInProgress` prop（小红书扫描时禁闲鱼）。
5. 「继续导入<另一平台>」改用父组件 tab 切换而非 `window.location.reload()`。
6. PRD vs impl 文案对齐（筛选 chips 取向、5 步进度文案、重复单元信息加日期前缀）。
7. TL 5 项 carry-over：N+1 重复查询批量化、串行 LLM 调用 → 批量或 asyncio.gather、payload Field max_length、CORS 收敛、扩展硬编码 backend URL → build-time template。
8. xhs/probe 占位实现 → 要么真做要么从 PRD 删 yellow 态。

非 prd-006：
- prd-005 已 PM Satisfied，frontmatter 已 completed。
- prd-003 CUJ-2/3/4/5 与 prd-000/001/002/004 全部 CUJ 仍待首次 PM Review。
