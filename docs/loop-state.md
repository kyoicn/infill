# Dev Loop State

Last updated: 2026-06-23 00:28:12 (UTC+8)
Iteration: 5
Status: continue

## Last Cycle Summary
- Scope: 交付 prd-007「打印机状态与每日利用率监测」全 2 CUJ（CUJ-1 凭证编辑入口 + CUJ-2 状态页 / 4 卡片 + 24h 时间轴 + WS 实时通道 + utilization 自然日聚合）
- Tasks executed: 11（G1=2 / G2=3 / G3=2 / G4=3 + QA-retry-1=1 frontend fix）
- Tasks passed QA: 11（initial QA FAIL CUJ-2 → retry 1 fix HIGH×2 + LOW×3 后 PASS）
- Tasks rolled back by QA: 0（QA-fix inline 修补，未回滚至 in-progress）
- Tests passing: 416 backend（baseline 344 → +72 新加：T1.1 schema migration / T1.2 schemas round-trip / T2.1 MQTT daemon / T2.2 Broadcaster+Sampler / T2.3 utilization 纯函数 / T3.1 status router + WS / T3.2 PUT printer credentials + reconcile timing / QA Retry 1 加 3 E2E）
- Tests failing: 0
- QA inner loops used: 1 of 2
- CUJs completed this cycle: 0/2（impl + QA gate 全过，但 PM 判 2 CUJ 均 Caveats）
- CUJs remaining: 2/2 待 PM Satisfied + 真打印机硬件接受测试

## QA Gate
- Verdict: PASS（initial FAIL → retry 1 PASS）
- Fabrications found: 0
- HIGH bugs found: 2 → 全部闭环（commits `928020f` / `998db4a`）
  - `PrinterStatus.tsx` mount 不独立拉 snapshot（违反 PRD CUJ-2 Step 1）
  - `vite.config.ts` `/api` proxy 缺 `ws: true`（dev WS upgrade 失败）
- MEDIUM bugs found: 2（HIGH 修完后均通过、无独立修法）
- LOW bugs forwarded: 0（3 条 antd deprecation warning 全清，commit `998db4a`）
- TL P2 carry-over（已文档化，未阻塞）：
  - `main.py` lifespan startup race window（daemon.startup → sampler.start_heartbeat_loop 之间的窄毫秒级窗口可能丢首条 broadcast；sample 仍落库）
  - `routers/printer_status.py` snapshot N+1 query（4 台机 8 query 可接受；10+ 台需 join 优化）
- Tasks rolled back: 0
- E2E added by QA: `backend/tests/test_printer_status_e2e.py` 3 case（凭证生命周期 / utilization sample 联动 / WS broadcaster→client）— commit `f72a695`

## PM Gate
- Verdict: 0/2 Satisfied / 2 Caveats / 0 Not done
- prd-007 frontmatter 保持 `active`（未升 completed —— PM 判 2 CUJ 均 Caveats）
- 关键 product 风险：
  - CUJ-1：antd Cancel/OK 按钮英文化（缺 zh_CN locale）/ access_code placeholder 未给 LCD 引导 / 「清除访问码」link 太显眼无二次确认
  - CUJ-2：24h 时间轴凌晨视觉大段空白 / 无颜色 legend / 「未配置」tooltip 文案错位（指向不存在的「右上角设置」）/ 离线徽章无 actionable 指引 / snapshot 失败透 raw 错误码
  - 3 条 WAIVED AC（场景 B 真机离线 / AC #11 真机断电恢复 / Edge case WS 90s+ 屡次失败降级）需真打印机硬件做最终接受测试

## Iter5 Implementation Commits（约 30 个，按阶段）

**Phase 1/2 设计 + 任务分解**：
`9b82211` PRD-007 + index.md 翻 → `b8a330d` design-printer-status.md（11 节，paho-mqtt 2.x + asyncio Queue broadcaster + 实时聚合 utilization）+ system.md 2 行指针 → `9b73dc6` tasks.md 4 组 10 task

**Phase 3 G1（schema + deps，2 task）**：
`e1834e5` T1.1 Printer +3 列 + PrinterStatusSample 表 + auto_migrate（5 case，FK CASCADE 仅生产 engine） → `1d5dd95` T1.2 paho-mqtt deps + PrinterUpdate/Snapshot/Event schemas + access_code 掩码（18 case） → `fca9f66`/`6b77765` merge

**Phase 3 G2（守护进程 + sampler + utilization，3 task）**：
`e238b7f` T2.1 MQTT daemon paho VERSION2 + access_code 掩码日志（19 case） → `525fe47` T2.2 Broadcaster + Sampler 心跳兜底 + 离线检测（6 case） → `d49c14b` T2.3 利用率纯函数（10 case，含 DB 便利接口） → `5bff7a7`/`904731c`/`68eee3b` merge

**Phase 3 G3（routers + lifespan，2 task）**：
`dab859f` T3.1 snapshot REST + WS endpoint + lifespan 三对象挂 app.state（4 case） → `786b1d6` T3.2 PUT partial + commit-then-reconcile + DELETE-then-unsubscribe（7 case） → `0647c47`/`75e99cd` merge

**Phase 3 G4（前端，3 task）**：
`1dc6147` T4.1 client.ts 8 类型 + snapshot 方法 + updatePrinter partial 签名 → `83bbea4` T4.2 Settings 编辑按钮 + EditPrinterModal 4 字段（access_code 三态 unchanged/set/cleared） → `f58a639` T4.3 PrinterStatus 页 + WS hook 指数退避 + Timeline24h DOM 分段 + 路由 + 菜单 → `1330b59`/`33554ae`/`8a19414` merge → `f8e1f60` 占位类型清理（types.ts 删，import 切 ../api/client） → `ea725b9` gitignore root-level data.db

**Phase 3.6 TL code review**：
`c469c8f` TL fix 2 P1（DELETE printer unsubscribe try/except 容错 + EditPrinterModal catch e:any 类型守卫）

**Phase 4 QA initial → retry 1**：
QA initial 文档 → `928020f` QA retry 1 fix（PrinterStatus.tsx mount useEffect + vite proxy ws:true + 3 antd deprecation） → `998db4a` merge → `f72a695` QA Retry 1 加 3 E2E → `438c4ef` QA gate PASS 报告

**Phase 5/6 status + PM**：
`8f906ba` status.md 9 节重写（CUJ-2 标 in-progress 反映 3 WAIVED AC） → `0647091` PM review 双 Caveats verdict

## Next Focus（PM 建议优先级 + carry-overs）

**PM 推荐 iter6 三大方向**：

1. **CUJ-1 凭证编辑弹窗 v2 抛光（高价值/低成本，1-2h）**：
   - 引入 antd zh_CN locale → 「确定/取消」中文按钮
   - 三字段 placeholder 加 LCD 来源引导（「IP：路由器 / LCD → 网络」「序列号：LCD → 设置 → 关于」「访问码：LCD → 设置 → 网络 → 访问码」）
   - 「清除访问码」改 Popconfirm 二次确认（避免误点）

2. **CUJ-2 状态页 v2 体感抛光（高价值/中成本，半天）**：
   - 时间轴加 legend（绿=打印 / 黄=暂停 / 灰=空闲 / 红条=离线）
   - 「未配置」tooltip 改成「点这里去补填凭证」直跳链接
   - 离线徽章加 actionable tooltip（最后一次连接时间 + 重试建议）
   - snapshot 失败固定文案，不透 raw 错误码
   - 「现在」竖线右侧加淡条纹表示「未到达的时间」

3. **真打印机硬件接受测试（高价值/真机依赖）**：
   - AC #8 场景 B：真机断电后 daemon 写 offline sample，状态徽章变红
   - AC #11：真机重启后 daemon reconcile_one 自动重新订阅
   - Edge case WS 90s+ 屡次失败 → 三态指示降级到红色「实时连接断开」

**TL P2 carry-over（可顺手做或单开 ticket）**：
- snapshot N+1 优化（>10 台时 join + 缓存）
- lifespan race window（在 daemon.startup 之前 bind `sampler._loop = asyncio.get_running_loop()`）

**其他 PRD carry-over**（非本轮 scope，不阻塞）：
- prd-006 iter4 PM Caveats × 4 + TL 5 项 carry-over（LLM key alert / escape hatch / 跨 tab 互锁 / tab 切换 vs reload 等）
- prd-003 CUJ-2/3/4/5 + prd-000/001/002/004 全部 CUJ 仍待首次 PM Review
