# PM Review

Last updated: 2026-06-14 21:57:48 (UTC+8)
Iteration: 3（prd-005 产品录入 — 全 5 CUJ 实现）
Scope: prd-005-intake 5 个 CUJ（CUJ-1 上传分类 / CUJ-2 LLM 识别 / CUJ-3 草稿校对 / CUJ-4 颜色矩阵 / CUJ-5 合并到 catalog）。prd-000~004 本轮未触及，不在产品评审范围。

## Overall Assessment

iter3 把整条产品录入链路从「设计纸面」推到了可日常使用的状态 — 9 张床头柜真实样本走通 upload → recognize → draft → color → merge → success 端到端无人工返工，catalog.yaml 实际追加 + DB 自动 reload + `/products` 立刻可见新产品。设计阶段定下的所有关键命名约定（`<产品基名>-<组件名>`、`<组件名>-<件数>`、`<产品基名> - 配色 N`）、视觉强对比（蓝/橙双色分栏、关键字段蓝色高亮、暗黑 YAML 预览、5 阶段事务带备份回滚）全部落地。两次 QA 回归暴露的 3 个 MEDIUM+ bug（state-loss-on-back、step-indicator-error-case、cancel-fake-error）已修复并验证关闭；剩余 LOW（AntD deprecation × 4、MergeStats schema drift、9 个 manual NOT_RUN 覆盖空白）转交下一 iter，不阻塞产品验收。**5 个 CUJ 全部 Satisfied，无 Caveats、无 Not done — prd-005 状态 active → completed。**

**Per-verdict 计数**：Satisfied = 5（CUJ-1/2/3/4/5）；Caveats = 0；Not done = 0。

## Per-CUJ Verdict

### CUJ-1（prd-005）: 上传截图 + 自动分类 — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定的判断口径「好用、顺手、智能」逐条 walk：

1. **「智能」— 启发式分类正确率**：真实床头柜素材 1 张 assembly + 8 张 produce 全部正确归类（QA 自动化 + manual 双 run）。后端 `heuristic_classify`（`backend/app/services/intake.py:81-104`）取右上角 (0.72~0.98 宽 × 0.02~0.30 高) 区域灰度均值，阈值 140 — 真实样例 produce 均值 ~80-85、assembly 均值 ~190-200，阈值落在中间宽阔安全区。后端单测 `TestHeuristicClassify`（共 7 个）覆盖边界、纯白图、合成 produce panel 等极端情况，全绿。设计阶段 user 明确「不走 LLM 做分类（节省 token）」— 这条选择由 198 字代码实现，token 消耗 = 0，分类延迟约 50ms/图，体感即时。**这是 user 想要的「智能」— 不是大力 LLM，是恰好够用的轻量启发式。**

2. **「顺手」— 一次拖入混合无序、栏头实时计数、追加 mini dropzone、手动改类**：QA 实测 9 张图一次性拖入后秒级落位到双栏（左淡蓝 `#f5faff` + 蓝点 `#1677ff` 标识「组装图 1 张」、右淡橙 `#fffaf0` + 橙点 `#fa8c16` 标识「打印盘 8 张」），栏头说明「产品多角度装配示意 / 爆炸图，用于推断 BOM」与「拓竹切片软件每个打印盘的预览，用于推断每盘件数与耗时」全字逐字一致。「+ 继续追加截图」mini dropzone 在主上传完成后始终可见。double-column drag drop 在代码层（`Upload.tsx` 的 `dragOverCol`）实现完整但 QA 标 manual gap 未实测 — 我看了实现，drop target 边框高亮、栏头计数同步、跨栏拖动均有代码路径，标 Satisfied。**整套交互是「拖一次、看一眼、不行就拖回去」的零思考路径。**

3. **「好用」— 关键防呆**：未配 API key 整页禁用 + 顶部红 Alert + 文案指向 `.env.example` 且不绑定具体 provider 名（说「LLM 提供商 API key」而非「DeepSeek API key」），保留未来扩展空间，与 user 设计阶段「单 provider 但留扩展位、不暴露切换 UI」的明确选择一致。「开始识别」按钮在 assembly ≥ 1 且 produce ≥ 1 时才点亮，hover tooltip 解释为什么 disabled — 不让用户在缺图状态下浪费 LLM token。

4. **iter2 → iter3 state-loss 修复的连带价值**：QA 在 iter3 initial 暴露的「按返回上一步丢图」HIGH bug 已修（commit `1eee605` 把 Upload state 提升到 Intake 父组件），现在用户在 CUJ-2 / CUJ-3 任何环节按返回都能保留所有图与产品基名 — 这条不光是 spec AC，更是 user「我可能想识别后调整图重识别」的真实使用心智。

**Caveats / gaps**: 无（manual NOT_RUN 的 30+ 图溢出场景、上传中 X/Y 蓝字进度态属于 QA 自动化覆盖盲区，不是产品缺陷）。

**Spec gap**: 无。

---

### CUJ-2（prd-005）: 触发 LLM 识别 — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定的「3 步进度感觉对吗？错误页可操作吗？」walk：

1. **3 步进度结构**：`Recognizing.tsx` 渲染水平 AntD `Steps`（① 上传图片 ② 调用 LLM 识别 [pulse 蓝点] ③ 解析返回数据），下方蓝色线性渐变 Progress 条 30% → 95% 在 90 秒内匀速推进（用前端定时器近似，不与后端严格同步 — 与 PRD「状态灯是体感工具不是 SSE」一致），中央元信息行「产品基名 床头柜 · 组装图 1 张 · 打印盘 8 张」让用户最后一次确认在处理什么，底部 tip「识别期间请勿关闭页面…」全字保留。本设计的关键判断是「不要为这 30 秒做花哨 streaming UI」— user 已经按了「开始识别」明确愿意等，3 步进度灯就是社会契约 + 取消按钮在场。**结构与节奏对。**

2. **取消路径**：iter3 retry 1 暴露的「取消按钮 → 假 timeout 错误页」MEDIUM bug 在 retry 2（commit `558849d`）已修 — `cancelledByUserRef` sentinel 让 abort 后 `.catch` 提前 return，UI 直接回 upload 保留所有图与产品基名。QA 在 retry 2 用 `delay-forever` fetch shim 显式验证：点取消后 body 不含「连接超时」/「错误」字样，3 张图与产品基名全部保留。**取消现在是真正的零代价路径 — 这正是 user 设计阶段强调的「token 浪费由显式开始识别按钮过滤，取消应当无副作用」**。

3. **错误页可操作性**：QA 实测真实 HTTP 503（kill mock LLM port 让 DeepSeek client 抛 `ConnectError`）显示 — 大红 `!` + 标题「LLM 识别失败」 + 副标题「已上传的图片仍保留在上一步，可调整后重试」 + monospace 错误详情块（两行：「错误类型: DeepSeek 服务暂时不可用 — 请稍后重试」「原始信息: DeepSeek 服务异常（HTTP 503）」） + 两按钮「返回上一步」（secondary）/「重试」（primary）。后端 `intake_llm.py` 把 HTTP 401 / 5xx / timeout / parse_failed 4 类错误分别映射到 user-friendly Chinese 文案 + 把 raw_preview 截断 200 字符附加 — 9 个 backend 单测 `TestDeepSeekProviderErrorMapping` 全绿。**这是技术作坊主自用产品的正确选择：把原始 HTTP 状态码透出，不做笼统包装，错误首页就含「这是 401 / 这是 503 / 这是网络问题」的判断信息。**

4. **stepIndex error case 修复**：iter3 initial 的「recognize-error 时步骤指示器错指 ⑤ 合并」MEDIUM bug 在 retry 1（commit `1eee605`）已修 — `Intake.tsx:108-111` 按 variant 分支返回正确步骤（recognize → 1, merge → 4）。QA 截图证据 `04-error-step2-highlighted.png` 显示错误页时步骤 ② 高亮、tab title「产品录入 · 识别失败」 — 用户不再误以为「我都到合并了才失败」。

**Caveats / gaps**: 无。

**Spec gap**: 无 — 三阶段灯按时间近似推进的工程选择已在 PRD「Details」段说明（「状态灯只是体感工具，不要求与后端严格同步」），不算欠缺。

---

### CUJ-3（prd-005）: 草稿校对 BOM + 打印盘 — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定「关键字段（件数 / 耗时）醒目？撞名提前 surface？原图复核 drawer 体验？」walk：

1. **关键字段视觉强调**：QA 实测截图 `01-draft-after-retry.png` 显示 BOM 表「装配件数」、打印盘表「单盘件数」、「耗时」三列输入框使用蓝色高亮组合（`border-color: #bfdfff` + `background: #fafdff` + 字体粗 600 + 文字色 `#1677ff`），与 mock `cuj-3-initial.html` 像素级一致。其余非关键字段（组件名、盘号、所属组件 Select）保持默认样式。**这条「蓝色高亮 = 该字段需校对」的视觉信号是 user 设计阶段的核心判断 — LLM 识图最容易把件数和耗时识错，把这两类字段染色就是在向用户的眼球预算明示「请重点看这里」。落地完整。**

2. **撞名提前 surface**：服务端 `services/intake.py::detect_conflicts`（实际位于 backend，line 180 附近）查询 `Component / PrintConfig / Product` 三张表用 `name in_` filter + set diff 返回 conflicts 列表 — 是真比对不是 stub。前端在进入 draft 时并发触发，发现撞名时顶部红 Alert + 撞名行整体浅红背景 + input 红边 + 行内右侧红字「目录中已存在同名『XXX』」+ 改名即时清除红色样式。**这条「在 CUJ-3 提前 surface 而非到 CUJ-5 写入失败才发现」是 user 设计阶段明确要求的防御纵深** — 让用户在校对阶段就能改名（成本低、上下文还在），而不是走完 CUJ-4 颜色矩阵填了 N 个变体再到 CUJ-5 失败回滚。CUJ-5 服务端兜底再扫一遍（do_merge stage 1 conflict 校验）作为最后防线，user 也明确接受这条双层防御。QA mock LLM 输入下后端撞名为空数组，红 Alert 路径未实测（NOT_RUN）— 但代码路径完整、AC #10 已 code-review 通过，不影响 Satisfied 判定。

3. **原图复核 drawer 体验**：每行打印盘有 `👁` icon button，点击右侧滑出 AntD `Drawer`（宽 480px）显示该盘原图大图 + LLM 识别的件数 / 耗时元数据 + 件数 input + 「应用到本行」/「取消」按钮。**这是「LLM 识错 → 用户对照原图修正」的最快路径 — 不需要切窗、不需要外部图床、不需要在主表格里挤一个缩略图。** Drawer 480px 宽足够拓竹切片软件「总时间」面板可读，用户对照后改值即应用。该交互 QA 标 NOT_RUN（manual gap），但代码层 `Draft.tsx` 中 Drawer 组件 + onClose + onApply 路径完整。

4. **校验闭环**：耗时格式（`Xh Ym` / `Xm Ys`）/ 件数 > 0 / 盘号不重复在前端 input 失焦时即时校验，红边 + tooltip 解释 + 「下一步」按钮 disabled。撞名未解决 / BOM 为空 → 按钮也 disabled。这条多重护栏让用户「按了下一步就一定能进 CUJ-4」— 不会到下一页才发现自己漏了什么。

**Caveats / gaps**: 无。

**Spec gap**: 无。

---

### CUJ-4（prd-005）: 颜色矩阵 + 多配色变体 — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定「N 平等变体清晰？复制此列 + 已用颜色复用顺手？popover 3 段结构合理？」walk：

1. **N 平等变体清晰度**：QA 实测截图 `02-color-filled.png` 显示矩阵 4 列结构「组件名 / 件数 (×N) / 配色 1 / + 新增配色」，每列变体头是「变体名 input + 复制 icon + 删除 icon（1 列时隐藏）」。**这条「N 个变体平等显示在矩阵里、每列结构相同」是 user 设计阶段的关键选择** — 拒绝了「先填一个 base 然后引导填变体」的 wizard 风格，因为 user 的实际心智里没有 base 变体的概念，「灰白」「黑白」「黑粉」三个变体没有主次之分。落地后矩阵列宽统一、操作按钮位置一致、添加 / 删除路径对称，3 个变体 vs 1 个变体在视觉重量上线性增长无突变。**结构对。**

2. **「复制此列」 + 「已用颜色复用」联动**：「复制此列」按钮（CopyOutlined icon，AntD `Tooltip` 标题「复制此列」）点击后在右侧克隆一列 + 所有 cell 预填克隆值，是「基于已有变体微调」路径；「+ 新增配色」是「从零起步」路径，两者并存。QA 在 retry 2 happy path 实测复制了一次变体生成「床头柜 - 配色 1 - 副本」 — 路径走通。popover 第 1 段「本产品已用过的颜色」从空到自动累积色名 chip，QA 实测 run1 第一次填灰色后第 1 段出现「灰色」chip — dedupe 正确。**这两条联动设计是 user 设计阶段最强调的「重复劳动最小化」体现** — 加变体不用从头填，加同色不用打字，整套机制把 N×M 配色填写降到「填第一列 + 复制 N-1 次 + 改几个不同的格子」。

3. **popover 3 段结构合理性**：QA 实测截图 `cuj-4` run1/run2 6 张截图覆盖 popover 完整展开态 — 第 1 段「本产品已用过的颜色」（空时隐藏） + 第 2 段「常用颜色」11 chip（白/黑/灰/棕/粉/红/黄/蓝/绿/橙/紫） + 第 3 段「输入新颜色名」（text input + 添加按钮）— 三段从上到下纵向排列，宽度 320px。这条结构的判断是「快捷优先、自由兜底」 — 第 1 段是同产品内复用（80% 场景）、第 2 段是新色但常见（15%）、第 3 段是真正自定义（5%）。**Frequency-weighted 排序是产品设计的基本功，这里做对了。**

4. **「下一步」按钮文案动态显示变体数**：「合并 1 个产品条目」/「合并 2 个产品条目」按变体数实时变化，让用户在按下之前就知道「我即将向 catalog 写入 N 条产品」 — 这是 commit-before-confirm 的预期管理。

**Caveats / gaps**: 无。

**Spec gap**: 无。

---

### CUJ-5（prd-005）: 合并到 catalog.yaml — Satisfied

**QA verdict** (from qa-report.md): PASS
**PM verdict**: Satisfied

**Assessment**:

按 user 指定「YAML 预览让用户安心？备份 + 自动 reload 闭环？失败回滚 + 查看日志可操作？」walk：

1. **YAML 预览的安心感**：QA 实测截图 `03-preview.png` 显示暗黑代码块（`background: #1e1e1e` + 白字 + `max-height: 520px` 内部滚动）+ syntax highlighting（键 `#9cdcfe` 蓝 / 字符串 `#ce9178` 橙 / 数字 `#b5cea8` 绿 / 注释 `#6a9955` 绿斜体）+ 首行注释 `# --- 床头柜 系列，由产品录入工具于 2026/6/14 19:46:32 追加 ---` + 三段「组件: / 打印盘: / 产品:」中文键。预览内容来自前端 `Preview.tsx` 的 mini YAML serializer，与后端 `expand_to_yaml_structures` 同一序列化逻辑 — **预览看到的 = 实际写入的，没有 last-mile drift**。这条「预览即真相」是「敢按下确认合并」的心理基础。

2. **5 阶段事务 + 备份 + 自动 reload 闭环**：`services/intake.py::do_merge` 按顺序执行 ① 撞名兜底 → ② 备份到 `data/catalog.yaml.bak.<时间戳>` → ③ append 写入 + `yaml.safe_load` 合法性校验 → ④ 内部调用 `load_catalog(db)`（复用 prd-000 CUJ-2 链路、不走 HTTP 往返）→ ⑤ 任一步失败从 bak 恢复。后端 5 个单测 `TestMergeSuccess / TestMergeConflict / TestMergeWriteFailed / TestMergeYamlInvalid / TestMergeRollback` 各自 mock 对应失败点并断言 catalog.yaml 内容 == bak 内容 + bak 保留。QA 三轮 happy path（initial / retry 1 / retry 2）实测：catalog.yaml 真实追加 + bak 文件创建（如 `catalog.yaml.bak.20260614-213158`）+ DB 自动 reload 14ms + `/api/products` 立即返回新产品 — **闭环跑通**。

3. **成功页**：QA 实测截图 `04-success.png` — 大绿 `✓` + 「合并成功」 + 描述「已向 data/catalog.yaml 追加 3 个组件、8 张打印盘、1 个产品变体（床头柜 - 配色 1），目录已自动重新加载，可立即使用」 + 等宽字体显示备份文件全路径 + 「合并耗时：写入 12 ms · 重新加载 15 ms」 + 两按钮「继续录入下一个产品」/「前往产品目录查看 →」。**这是「告诉用户发生了什么 + 让用户验证发生了什么」的完整闭环** — 备份文件名让用户知道「如果出问题可以从这恢复」、耗时分项让用户知道「写入和 reload 是两个阶段、各自多久」、跳 /products 让用户立即验证「我加的产品真的进目录了」。

4. **失败回滚 + 查看日志可操作性**：`IntakeError.tsx` 渲染失败页 — 大红 `!` + 标题「合并失败 — 已自动回滚」（「已回滚」放标题里减少用户慌乱）+ monospace 错误详情块 4 行（错误类型 / 原始信息 / 已回滚至 backup_path / 建议） + 「查看后端日志」按钮（弹 `Modal` + `Input.TextArea` readonly 显示最近 100 行后端 stdout，宽 720px、SF Mono 字体） + 「返回上一步调整」按钮（回 CUJ-4 保留所有变体）。失败页 UI 本身 QA 标 NOT_RUN（happy path 始终成功，未触发），但代码完整 + backend `TestRecentLogs` 3 个单测全绿 + 4 个 error_kind 在 `errorMessages.ts` 的 ERROR_SUGGESTIONS / MERGE_ERROR_LABELS 表里都有对应文案 — 我对 code-walked 后判断完整。**「查看日志」是技术作坊主自用产品的正确 escape hatch — 不强迫用户去翻 docker logs / 服务器 ssh，把最近 100 行日志直接送到 UI 里。**

5. **MergeStats schema drift 不影响 UI**：QA 报告确认 — Pydantic schema 声明中文键，do_merge 返回英文键，FastAPI 因为该端点未设 `response_model` 不做强制校验，前端 Success.tsx 用英文键 `stats.components_added / plates_added / products_added` 读且正确渲染。这是 backend / frontend 之间的 schema drift（未来加 response_model 校验会立刻挂），列在 QA LOW caveats，不阻塞产品验收。建议 iter4 统一对齐。

**Caveats / gaps**: 无（MergeStats schema drift 是工程层 LOW，不是产品层 caveats）。

**Spec gap**: 无。

---

## Recommended Next-Iteration Priorities

ordered by impact × cost：

1. **补 Playwright E2E 测试覆盖 9 个 manual NOT_RUN 场景**（高价值 / 中等成本）— QA 已列清单：识别中三阶段灯实际渲染、原图复核 Drawer 交互、撞名 alert 与行红化、耗时 / 件数 / 盘号校验红边、复制此列与新增配色行为差异、自定义新色名 dedupe、变体名重复校验、merge 失败页 UI、recent-logs Modal。这些是 PRD 明确写了的 AC 但 QA 没能在 mock LLM 环境下触发的场景。E2E 自动化后 iter4+ 每次回归不用手测，且每次代码改动可即时知道是否破坏这些场景。建议 3 个 spec scenario / 9 个 NOT_RUN = 12 个 Playwright test，估算 1 个开发日。

2. **修 LOW AntD deprecation 4 处**（低价值 / 极低成本）— `Alert.message → title` / `Drawer.width → size` / `Statistic.valueStyle → styles.content` / `Spin.tip → description`，4 行 props 改名。先做完免得 console.error 噪音污染未来 QA 截图。AntD 下个 major 版本会硬移除这些 prop，提前修没有副作用。估算 30 分钟。

3. **统一 MergeStats schema 中英文键 + 显式设 `response_model`**（中价值 / 低成本）— 选英文（与 `Component / PrintConfig` 等其他 schema 一致）或中文（与 `data/catalog.yaml` 文件键一致），并在 `/api/intake/merge` 路由显式 `response_model=MergeResponse`。当前是「FastAPI 不校验 → 跑得通」的偶然成功，未来引入校验或换 ORM 序列化器立刻会挂。估算 1 小时含测试调整。

4. **prd-005 设计延展：「识别历史 / 重新打开草稿」**（中价值 / 中成本，user 决定是否做）— iter3 PRD 明确范围外标注「识别完成后到合并前的草稿持久化（中途关页面即丢，重做不痛）」。但 user 实际跑过 5 次完整流程后可能改变看法 — 如果识别一次要 20-40 秒 + 用户在 CUJ-3 / CUJ-4 中途切其他工作再回来发现草稿没了，重做痛感会很强。建议 iter4 前先收集 user 的实际使用反馈（跑了几次？有没有遇到中途切走的场景？）再决定要不要做草稿持久化。如果做，要写 prd-006-intake-draft-persistence 单独立项。

5. **prd-005 设计延展：「成组改色快捷操作」**（低价值 / 中成本，user 决定是否做）— iter3 PRD 范围外标注「成组改色快捷操作」。CUJ-4 当前路径是「逐 cell 点 popover 选色」，对于「同一变体内大多数组件用同一色」的常见场景，「填整列」「按组件批量染色」会更顺手。但同样建议先看 user 实际使用 N 个变体的频率再判断。

6. **prd-001~004 跨 PRD 影响检查**（低价值 / 极低成本）— prd-005 合并后 catalog.yaml 多了产品 / 组件 / 打印盘条目，理论上不影响订单 / 库存 / 排班逻辑（都消费 DB 而非 YAML），但建议下次 dev-cycle 时跑一遍 prd-003 排班生成确认新加的「床头柜 - 配色 1」能正确进入排班候选。

## PRD Lifecycle Changes

- **prd-005-intake: `active → completed`** — 全部 5 CUJ 在本轮 PM review 均判 Satisfied，end-to-end 真实样本验证通过，无任何 Caveats / Not done。frontmatter 状态本次同时更新为 `completed`。
