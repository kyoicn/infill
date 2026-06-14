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

### Issue 2026-06-14-23-24-08: intake 识别失败 — DeepSeek 不接受 OpenAI `image_url` 多模态格式

- **Filed**: 2026-06-14 23:24:08 (UTC+8)
- **Description**: 在 /intake 触发 LLM 识别时，DeepSeek API 直接返回 HTTP 400 并拒绝请求 body。错误明确指向 `messages[1]` 包含 `type: "image_url"` 内容块，但 DeepSeek 期望 `type: "text"` — 即 DeepSeek vision API 不接受 OpenAI 标准的 `image_url` 多模态消息格式（`backend/app/services/intake_llm.py` 当前按 OpenAI Chat Completions 多模态约定构造消息）。前端只看到泛化错误页「DeepSeek 服务暂时不可用 — 请稍后重试」，实际是契约不匹配，不是限流/可用性问题。
- **CUJ**: CUJ-2 (docs/prd/prd-005-intake.md) — 触发 LLM 识别
- **Expected**: 上传 assembly + produce 截图后点「开始识别」→ 进度条 → 1-2 分钟内得到草稿（BOM + 打印盘）
- **Observed**: 几秒内显示错误页「DeepSeek 服务暂时不可用 — 请稍后重试」。后端日志：DeepSeek 返回 HTTP 400，body：`{"error":{"message":"Failed to deserialize the JSON body into the target type: messages[1]: unknown variant 'image_url', expected 'text' at line 1 column 4011663","type":"invalid_request_error",...}}`
- **Repro**:
  1. 进入 http://192.168.31.80:8000/intake
  2. 上传 1 张 assembly + ≥1 张 produce 截图
  3. 自动分类完成 → 进入草稿前的识别步
  4. 点击「开始识别」
  5. 立刻得到错误页（无任何识别结果）
- **Triage** (2026-06-14 23:29:03 (UTC+8)):
  - **Scope**: medium（代码改动小但需要用户先做产品决策）
  - **Root cause**: `backend/app/services/intake_llm.py:170-178` 按 OpenAI 标准多模态构造 `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}` 内容块；但 DeepSeek 公网 API (`https://api.deepseek.com/v1/chat/completions`) 的 message content 只接受 `type: "text"` 一种 variant，没有 `image_url` 变体（这就是错误"unknown variant `image_url`, expected `text`"的字面含义）。**实测确认**（2026-06-14）：DeepSeek 公网 `/v1/models` 只返回 `deepseek-v4-pro` 和 `deepseek-v4-flash` 两个模型；对两者分别试 `type: image_url / image / input_image` 三种变体名，全部返回 "expected `text`" 错误。即 **DeepSeek 公网 API 在 v4 系列上完全没有 vision 能力**。代码里默认的 `deepseek-vl2-chat` 模型名也不存在于公网 API（DeepSeek-VL2 是开源模型，仅 HuggingFace 自托管或经第三方 inference 平台提供）。这不是 bug、不是限流、不是 key 问题，是 **DeepSeek 公网 API 产品层面就不支持 vision**。
  - **Files involved**: `backend/app/services/intake_llm.py`（核心）、mini 上 `.env`（base_url + model + 可能换 key）、`docs/design/design-intake.md` §4、`docs/prd/prd-005-intake.md`（"DeepSeek vision" 表述需重写）
  - **Recommended action**: `/quick-fix`，但用户必须先决策走哪条 provider：
    - **选项 A（推荐）**：换支持 vision 的 OpenAI 兼容 provider。候选：火山方舟 `doubao-1-5-vision-pro` / 硅基流动 `deepseek-ai/deepseek-vl2` / 阿里 dashscope `qwen-vl-max` / Anthropic `claude-opus-4-7` 或 OpenAI `gpt-4o`。改动：mini `.env` 改 3 值 + intake_llm.py 把 class 名/默认值/错误文案中性化（或保留 DeepSeek class 名作通用 OpenAI-Compatible Vision Provider）
    - **选项 B**：自托管 DeepSeek-VL2 — 需 GPU 基础设施，mini 无 GPU **不推荐**
    - **选项 C**：砍 provider 抽象绑死单一国际 SDK（如 Anthropic）— 违背 PRD 多供应商初衷 **不推荐**
  - **Risk**: 选 A 风险低（provider 抽象本就为此设计）；但每家 vision 模型对中文截图字段（特别是「总时间」位置）的识别质量需实测对比；文档（design-intake.md / prd-005-intake.md / .env.example）多处"DeepSeek"措辞需同步更新；环境变量名 `DEEPSEEK_*` 选 A 后变误导，长期应改成 `LLM_*` 中性名

---

### Issue 2026-06-15-FOLLOWUP-doc-cleanup: design-intake / prd-005 满天 "DeepSeek" 措辞需脱钩到多 provider 表述

- **Filed**: 2026-06-15（v0.2.2 release 时附带，作为 followup）
- **Description**: v0.2.2 已经把 `backend/app/services/intake_llm.py` 重构为 `OpenAICompatibleVisionProvider` + `PROVIDERS` 注册表 + `LLM_PROVIDER` env 切换，代码层已 provider-agnostic。但 `docs/design/design-intake.md` §4（LLM Provider 抽象）和 `docs/prd/prd-005-intake.md` 多处仍写「DeepSeek vision」「DEEPSEEK_API_KEY」「DeepSeek 单 provider」等历史措辞。
- **CUJ**: CUJ-2 (docs/prd/prd-005-intake.md) — 触发 LLM 识别（措辞层）
- **Expected**: 用 provider-agnostic 措辞（如「当前激活的 vision provider」「`<PROVIDER>_API_KEY`」），并在合适位置点出多 provider 切换由 `LLM_PROVIDER` env 控制
- **Observed**: 多处硬绑 "DeepSeek"，新用户读完会以为只能用 DeepSeek；对一个已经验证 DeepSeek 公网 API 不支持 vision 的项目尤其误导
- **Repro**: `grep -rn "DeepSeek\|DEEPSEEK" docs/design/design-intake.md docs/prd/prd-005-intake.md` 当前返回 20+ 行
- **Triage** (2026-06-15):
  - **Scope**: small（纯文档替换 + 一两段架构说明追加）
  - **Root cause**: 文档写于 v0.2.1 时代，那时 PROVIDERS 是规划中、DeepSeek 是 MVP 单 provider
  - **Files involved**: `docs/design/design-intake.md`、`docs/prd/prd-005-intake.md`
  - **Recommended action**: `/quick-fix` — 替换 + 在 design-intake.md §4 顶部加一段「多 provider 切换在 v0.2.2 落地」说明
  - **Risk**: 极低（纯文档）

---

## Closed

（空）
