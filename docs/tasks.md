# Task Plan

Last updated: 2026-06-22 22:42:26 (UTC+8)

## 本轮范围（Iter 5）

- **In scope（唯一）**：prd-007「打印机状态与每日利用率监测」全部 2 个 CUJ
  - CUJ-1：配置打印机网络凭证（PRD-004 打印机管理卡片每行加「编辑」按钮 + 弹窗内填 IP / serial / access_code 三字段；保存后守护进程重订阅）
  - CUJ-2：查看打印机状态页（新页面 `/printers/status`、4 卡片 = 名称 + 状态徽章 + 今日工时 / 24h + 24h 时间轴 bar；mount 先 snapshot 后 WS；右上角实时连接三态指示；WS 断线指数退避重连后再拉 snapshot 补齐；降级显示）
- **不做（明示推迟到下一轮）**：
  - prd-006 加固（PM caveats × 4 + TL 5 项 carry-over：CUJ-4 LLM key 红 alert、CUJ-1/2 跳过 SKU 匹配 escape hatch、跨 tab 互锁对称、「继续导入<另一平台>」改 tab 切换、文案对齐、N+1 / 串行 LLM / payload max_length / CORS / 扩展硬编码 backend URL 等）
  - prd-000/001/002/003/004 首次 PM Review
  - 任何其他 PRD 工作

## TL 硬性约束（编排已遵守）

1. G1 schema 必须先于 G2 守护进程：`Printer +3 列` + `printer_status_sample` 表 + `auto_migrate` 验证 → G2 才能起 MQTT 进程
2. G2 daemon + sampler + utilization 纯函数可并行
3. G3 routers（snapshot + WS）+ API client 扩展在 G2 之后
4. G4 前端（PrinterStatus 页面 + 4 卡片 + Settings 编辑弹窗）在 G3 之后
5. 依赖变化：`backend/requirements.txt` 加 `paho-mqtt>=2.1.0,<3.0`
6. API breaking：`PUT /api/printers/{id}` schema 从 `PrinterCreate` 切到 `PrinterUpdate`（Optional + exclude_unset）
7. 凭证日志硬禁项：MQTT client 日志只能打 `printer_id + IP + access_code_masked`；PR 内 grep 无原值
8. 守护进程挂 `app.state.printer_status_daemon / .sampler / .broadcaster`
9. `reconcile_one` 必须在 `db.commit()` 之后调
10. 凭证字段三件套全 nullable，任一为空跳过监测、徽章「未配置」

## Current State

iter4 已把 prd-006 自动导入订单全 4 CUJ 落地 + QA PASS（PM 4×Caveats，frontmatter 仍 `active`）；本轮起 iter5，scope 切到 prd-007。后端 `Printer` 当前只有 `id / name`（backend/app/models.py L117），`schemas.py` 仅有 `PrinterCreate / PrinterOut`（backend/app/schemas.py L127-130），`routers/printers.py` 的 `PUT` 现在用 `PrinterCreate`（backend/app/routers/printers.py L25-26），前端 `api.updatePrinter(id, data)` 当前用 any（frontend/src/api/client.ts L38）。完全干净起点。

依赖拓扑：

```
G1 (Schema + 依赖) ─┐
                   ├──► G2 (Daemon + Sampler + Utilization) ──► G3 (Routers + Update API) ──► G4 (前端 Client + Settings 弹窗 + PrinterStatus 页面)
                   └──►
```

---

## Parallel Group 1 — Schema + 依赖（2 task 并行）

### Task 1.1: Printer 加凭证三列 + 新增 PrinterStatusSample 表 + auto_migrate

- **PRD CUJ**: prd-007 CUJ-1（数据模型）
- **依赖**: 无
- **Do**:
  1. 改 `backend/app/models.py` 的 `Printer` 类（L117 起）加三列：`ip = Column(String(64), nullable=True, default=None)` / `serial = Column(String(32), nullable=True, default=None)` / `access_code = Column(String(16), nullable=True, default=None)`；加 `status_samples = relationship("PrinterStatusSample", back_populates="printer", cascade="all, delete-orphan", passive_deletes=True)`。
  2. 在 `backend/app/models.py` 末尾新增 `PrinterStatusSample` 类：`id` PK / `printer_id` FK `printers.id` `ondelete="CASCADE"` `index=True` `nullable=False` / `ts DateTime nullable=False index=True` / `state String(16) nullable=False`；`__table_args__ = (Index("ix_printer_status_sample_printer_ts", "printer_id", "ts"),)`；`printer = relationship("Printer", back_populates="status_samples")`。
  3. 在 `backend/app/database.py` 或 `backend/app/main.py` 现有 `auto_migrate(engine)` 路径（参考 docs/design/system.md §6.4）确认三个加列被自动 ALTER 覆盖；新表通过现有 `Base.metadata.create_all(bind=engine)` 一次性建好；如发现 `auto_migrate` 表已扫描的列集逻辑还需要补，把 Printer 三个新列名加进去。
  4. 写单测 `backend/tests/test_printer_status_schema.py`：(a) 模拟旧 DB（无三列、无新表）调 `auto_migrate(engine) + create_all` 后三列出现在 `PRAGMA table_info(printers)`；(b) 新表 `printer_status_sample` 存在；(c) 删 Printer 行后该机 sample 全部消失（FK 级联）；(d) 复合索引 `ix_printer_status_sample_printer_ts` 存在。
- **Files**:
  - `backend/app/models.py`（改 + 加）
  - `backend/app/database.py` 或 `backend/app/main.py`（核对 / 调整 auto_migrate 列集，如必要）
  - `backend/tests/test_printer_status_schema.py`（新）
- **Done when**:
  - `pytest backend/tests/test_printer_status_schema.py` 全部通过
  - 旧 `data.db` 模拟升级 + 新部署 create_all 均无破坏性变更
  - SQLite `PRAGMA foreign_keys=ON` 下删 Printer 自动级联删 sample
- **单测要求**:
  - 旧 DB 自动加三列
  - 新表 + 索引存在
  - FK CASCADE 验证
  - 新部署 create_all 一次性建好

### Task 1.2: requirements paho-mqtt + PrinterUpdate + PrinterStatusSnapshot/Event schema

- **PRD CUJ**: prd-007 CUJ-1（API）+ CUJ-2（snapshot/event）
- **依赖**: 无（与 1.1 文件无重叠）
- **Do**:
  1. `backend/requirements.txt` 加 `paho-mqtt>=2.1.0,<3.0`。
  2. `backend/app/schemas.py` 改 `PrinterBase` 不动 / `PrinterOut` 加 `ip: Optional[str]` `serial: Optional[str]` `access_code_masked: Optional[str]`（**不返回 access_code 原值**），并提供一个工厂方法或自定义 validator 把 ORM 对象的 `access_code` 转成 `access_code_masked`（形如 `****1234`：前面 `*` 占位 + 末 4 位明文；不足 4 位全 `*`；None → None）。
  3. `backend/app/schemas.py` 新增 `PrinterUpdate(BaseModel)`：`name / ip / serial / access_code` 全 `Optional[str] = None`；config `model_config = ConfigDict(extra="forbid")`（防字段拼错）。
  4. 新建 `backend/app/schemas_printer_status.py`：`TimelineSegment(BaseModel)` 含 `start_minute: int / end_minute: int / state: Literal["running","pause","idle","offline"]`；`PrinterStatusOut(BaseModel)` 含 `printer_id / name / state: Literal["running","pause","idle","offline","unconfigured"] / today_working_minutes: int / today_total_minutes: int = 1440 / last_state_change_ts: Optional[datetime] / timeline: list[TimelineSegment]`；`PrinterStatusEvent(BaseModel)` 含 `type: Literal["state_change"] = "state_change" / printer_id: int / state: Literal["running","pause","idle","offline"] / ts: datetime`。
  5. 写单测 `backend/tests/test_printer_status_schemas.py`：(a) `PrinterOut` 从 ORM 模拟对象（mock `Printer(ip="1.2.3.4", serial="x", access_code="12345678")` 等价 dict）round-trip → `access_code_masked == "****5678"`、None → None、短码（< 4 位）全 mask；(b) `PrinterUpdate.model_dump(exclude_unset=True)` 部分字段省略时不出现在 dump 结果；(c) `PrinterStatusOut` round-trip OK；(d) `PrinterStatusEvent.model_dump()` 类型形态符合 WS 推送格式。
- **Files**:
  - `backend/requirements.txt`
  - `backend/app/schemas.py`（改 PrinterOut + 加 PrinterUpdate）
  - `backend/app/schemas_printer_status.py`（新）
  - `backend/tests/test_printer_status_schemas.py`（新）
- **Done when**:
  - `pip install -r backend/requirements.txt` 成功
  - `pytest backend/tests/test_printer_status_schemas.py` 全部通过
  - `PrinterUpdate.model_dump(exclude_unset=True)` 严格忽略未传字段
- **单测要求**:
  - access_code 掩码逻辑（含 None / 短码 / 8 位）
  - exclude_unset 部分更新语义
  - snapshot / event schema round-trip

---

## Parallel Group 2 — 守护进程 + Sampler + Utilization 纯函数（3 task 并行）

依赖 G1 全部完成（模型 + schemas）。

### Task 2.1: MQTT 守护进程（printer_mqtt_daemon.py）

- **PRD CUJ**: prd-007 CUJ-1 / CUJ-2 后端链路
- **依赖**: 1.1（Printer 三列 + 模型）+ 1.2（paho-mqtt）+ 2.2 接口约定（先约定 `Sampler.on_event / on_unconfigured` 签名，2.2 实现兼容）
- **Do**:
  1. 新建 `backend/app/services/printer_mqtt_daemon.py`：单例 `MqttDaemon` 类（构造接受 `sampler` + `broadcaster` 或仅 sampler）；模块级日志器 `logger = logging.getLogger(__name__)`。
  2. `_build_client(printer)`：用 `paho.mqtt.client.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=f"infill-{printer.id}-{uuid4().hex[:8]}", clean_session=True)`；TLS 8883：`ssl.create_default_context()` + `check_hostname=False` + `verify_mode=ssl.CERT_NONE` + `client.tls_set_context(ctx)` + `client.tls_insecure_set(True)`；`client.username_pw_set(username="bblp", password=printer.access_code)`；`on_connect = lambda c,_,__,rc,___: c.subscribe(f"device/{printer.serial}/report")`；`on_message = _on_message`；`on_disconnect = _on_disconnect`；`connect_async(printer.ip, 8883, keepalive=60)`；`loop_start()`。
  3. `_normalize_gcode_state(raw: str) -> Literal["running","pause","idle"]`：`RUNNING→running` / `PAUSE→pause` / 其他 → `idle`（包括 `IDLE/PREPARE/FINISH/FAILED` 与未知 token）。
  4. `_on_message(client, userdata, msg)`：`json.loads` 失败 WARNING 记 `printer_id`（不记 payload）；payload `print.gcode_state` 若空 silent skip；否则 `_normalize_gcode_state` 后通过 `loop.call_soon_threadsafe` 或 `asyncio.run_coroutine_threadsafe` 切回主 loop 调 `sampler.on_event(printer_id, normalized, datetime.now())`。
  5. `startup() / shutdown() / reconcile_one(printer_id) / reconcile_all() / unsubscribe_one(printer_id)`：reconcile_one 先弹旧 client（`loop_stop + disconnect`），再读 DB；若三字段非空则起新 client；否则调 `sampler.on_unconfigured(printer_id)` 写一条 offline 切回。
  6. 凭证日志硬禁项：连接 INFO 日志格式严格 `"printer %s @ %s code=%s"` % (printer_id, ip, _mask_access_code(code))；`_mask_access_code` 算法：前 2 + `...` + 后 2（不足 4 位全 mask）；**任何日志路径都不能出现 access_code 原值**；PR 自查 `grep` 全文件确认无 `printer.access_code` 直接传给 logger。
  7. 单测 `backend/tests/test_printer_mqtt_daemon.py`（用 `unittest.mock` mock 整个 `paho.mqtt.client.Client`）：
     - 验证 `username_pw_set("bblp", access_code)` 被调
     - 验证 `tls_insecure_set(True)` + `verify_mode=CERT_NONE` 被设置
     - 验证 `connect_async(ip, 8883, keepalive=60)` 与 `loop_start()` 被调
     - 验证 `on_connect` 回调触发后 `subscribe("device/{serial}/report")` 被调
     - 验证 `_on_message` 收到 `{"print":{"gcode_state":"RUNNING"}}` 后会调 `sampler.on_event(pid, "running", ...)`；收到 `{"print":{"gcode_state":"PAUSE"}}` → `"pause"`；`{"print":{"gcode_state":"IDLE"}}` → `"idle"`；未知 token → `"idle"`；JSON parse 失败 → 不 raise
     - 验证 `reconcile_one` 凭证齐全 → 起 client、不齐 → 调 `sampler.on_unconfigured`
     - 验证日志输出（caplog）不含 `access_code` 原值字符串
- **Files**:
  - `backend/app/services/printer_mqtt_daemon.py`（新）
  - `backend/tests/test_printer_mqtt_daemon.py`（新）
- **Done when**:
  - 全部单测 PASS
  - caplog 断言无原值泄露
  - reconcile_one 切换语义正确
- **单测要求**: 认证参数 / 主题订阅 / 状态标准化 5 个 case / reconcile_one 两种分支 / 日志无原值

### Task 2.2: Broadcaster + Sampler（printer_status_sampler.py）

- **PRD CUJ**: prd-007 CUJ-2 实时通道 + 心跳兜底
- **依赖**: 1.1（PrinterStatusSample）+ 1.2（schemas_printer_status）；与 2.1 通过 `on_event / on_unconfigured` 函数签名解耦
- **Do**:
  1. 新建 `backend/app/services/printer_status_sampler.py`，含两类：
     - `Broadcaster`：`set[asyncio.Queue]` + `asyncio.Lock`；`async register() -> Queue(maxsize=100)`、`async unregister(q)`、`async publish(event: dict)` — 给每个 queue `put_nowait`；`QueueFull` → `get_nowait` 丢最旧再 `put_nowait`（drop-oldest）。
     - `Sampler`：常量 `HEARTBEAT_INTERVAL_SEC=30` / `OFFLINE_THRESHOLD_SEC=90`；内存 `_last_event_ts: dict[int, datetime]` / `_last_state: dict[int, str]`；`on_event(printer_id, state, ts)`（线程安全锁；状态变化才写 sample + 广播；同状态仅刷 `_last_event_ts`）；`on_unconfigured(printer_id)`（清掉内存 + 写一条 offline 并广播）；`async start_heartbeat_loop()` 起一个 asyncio.Task，每 30s 扫一次：若 `now - last_event_ts > 90s` 且 `_last_state != offline` → 标记 offline + 写 sample + 广播；否则若有 `_last_state` → 心跳兜底写一条 sample（**不广播**，避免噪声）；`stop_heartbeat_loop()`。
     - 写库辅助 `_write_sample(printer_id, state, ts)` 内部用 `SessionLocal()` 上下文管理器（与现有 `database.py` 一致）落 `PrinterStatusSample`。
  2. 提供 `async record_event(printer_id, state, ts)`（事件入口同步入口的异步等价，便于 daemon 跨线程使用）与 `broadcast_event(event: PrinterStatusEvent)`（构造 dict 然后 `await broadcaster.publish(event_dict)`）。
  3. 单测 `backend/tests/test_printer_status_sampler.py`：
     - Broadcaster fanout：注册 3 queue → `publish` 3 个 queue 都收到
     - 慢消费者 drop-oldest：注册 1 queue + 不消费 + publish 101 条 → queue 仍 100 长度、最旧丢失、最新保留
     - Sampler 状态变化广播：连续 `on_event(1, "running", t1) → on_event(1, "running", t2)` → 只写 1 条 sample + 1 次广播
     - Sampler 心跳兜底（mock 时间）：`on_event` 后等 30s → 写心跳 sample（state 不变、ts 更新）、不广播
     - Sampler 离线检测：`on_event` 后 91s 无事件 → 写一条 `state="offline"` sample + 广播
     - Sampler `on_unconfigured` → 写 offline + 广播 + 清掉 _last_state
- **Files**:
  - `backend/app/services/printer_status_sampler.py`（新；Broadcaster + Sampler 同文件，方便引用）
  - `backend/tests/test_printer_status_sampler.py`（新）
- **Done when**:
  - 全部单测 PASS
  - 心跳与离线阈值常量与 PRD 完全一致
- **单测要求**: fanout / drop-oldest / 状态变化 / 心跳兜底 / 离线检测 / on_unconfigured 6 类

### Task 2.3: Utilization 纯函数（printer_utilization.py）

- **PRD CUJ**: prd-007 CUJ-2 利用率
- **依赖**: 1.1（PrinterStatusSample 模型）+ 1.2（TimelineSegment schema）；与 2.1/2.2 完全无文件交集
- **Do**:
  1. 新建 `backend/app/services/printer_utilization.py`，纯函数无 DB 依赖：
     - `compute_today_snapshot(samples: list, now: datetime, today_start: datetime) -> tuple[int, list[TimelineSegment]]`
     - 输入 samples 严格升序、可能含一条 `today_start` 之前的最近样本（用于推断 today_start 时刻 state，参见 design §4.5.3）
     - 算法见 design §4.5.2：相邻样本对区间归属前一条 state；末段从最后样本延展到 now；working_minutes 累计 state ∈ {running, pause}；timeline 合并相邻同色段；working_minutes `min(_, 1440)` 截断。
  2. 暴露便利接口 `compute_today_snapshot_for_printer(db, printer_id, now)`：内部查「`ts <= today_start ORDER BY ts DESC LIMIT 1`」+「`today_start <= ts <= now ORDER BY ts ASC`」合并后调纯函数；返回 `(state, working_minutes, last_state_change_ts, timeline)`，state = `timeline[-1].state` 或 `"offline"` 兜底。
  3. 单测 `backend/tests/test_printer_utilization.py`：
     - 纯 IDLE 全天 → working_minutes = 0、timeline 单段 idle
     - 全天 RUNNING → working_minutes = 1440、timeline 单段 running
     - 跨午夜：last sample 昨天 23:00 running、今天无样本到 now=12:00 → working_minutes = 720（12 × 60）
     - 最后一段还在 RUNNING：今天 09:00 一条 running 样本、no later、now=10:00 → working_minutes 至少含 60
     - offline 不计入：今天 09:00 running、10:00 offline、now=12:00 → working_minutes = 60（仅 09:00~10:00）
     - pause 计入：今天 09:00 pause、10:00 idle、now=12:00 → working_minutes = 60
     - timeline 相邻同色合并：连续 3 条 running 样本 → 输出 1 段
     - working_minutes 截断不超 1440
- **Files**:
  - `backend/app/services/printer_utilization.py`（新）
  - `backend/tests/test_printer_utilization.py`（新）
- **Done when**:
  - 8 类用例全部 PASS
  - 纯函数无 DB / 无副作用
- **单测要求**: 上述 8 类断言全覆盖

---

## Parallel Group 3 — Routers + 守护进程 wiring（2 task 并行）

依赖 G2 全部完成。两 task 文件分明无重叠：T3.1 新建 `routers/printer_status.py` + 改 `main.py`；T3.2 仅改 `routers/printers.py`。

### Task 3.1: printer_status router（snapshot + WS）+ main.py lifespan wiring

- **PRD CUJ**: prd-007 CUJ-2
- **依赖**: G2 全部（daemon / sampler / broadcaster / utilization）
- **Do**:
  1. 新建 `backend/app/routers/printer_status.py`：
     - `GET /api/printers/status/snapshot` → `list[PrinterStatusOut]`：遍历 `db.query(Printer).all()`；每台机若三字段任一空 → `state="unconfigured" / today_working_minutes=0 / timeline=[]`；否则调 `compute_today_snapshot_for_printer(db, printer_id, datetime.now())` 拼装 `PrinterStatusOut`。
     - `WS /api/ws/printers/status`：`await ws.accept()` → `broadcaster = ws.app.state.printer_status_broadcaster` → `queue = await broadcaster.register()`；双协程 `_send_loop`（`asyncio.wait_for(queue.get(), 25)`，超时发服务端 `{"type":"ping","ts":...}`；正常则 `await ws.send_json(event)`）+ `_recv_loop`（`await ws.receive_json()`；客户端 `ping` 回 `pong`；其他忽略）；`asyncio.wait(FIRST_COMPLETED)`；finally `await broadcaster.unregister(queue)`；捕 `WebSocketDisconnect`。
  2. 改 `backend/app/main.py` lifespan：在现有 `auto_migrate / create_all / ensure_*` 之后、原有 yield 之前，依次：
     ```python
     broadcaster = Broadcaster()
     sampler = Sampler(broadcaster)
     daemon = MqttDaemon(sampler)
     app.state.printer_status_broadcaster = broadcaster
     app.state.printer_status_sampler = sampler
     app.state.printer_status_daemon = daemon
     await daemon.startup()
     await sampler.start_heartbeat_loop()
     ```
     finally 段反顺序 `await sampler.stop_heartbeat_loop()` + `await daemon.shutdown()`。
  3. `app.include_router(printer_status.router)` 加在已有 router 列表里。
  4. 单测 `backend/tests/test_printer_status_router.py`：
     - 用 FastAPI `TestClient`：`GET /api/printers/status/snapshot` 返回结构正确（mock 一台凭证齐全 + 一台未配置 → 一个 state ∈ 4 状态 + 一个 unconfigured）
     - 用 `TestClient.websocket_connect("/api/ws/printers/status")` 上下文：先连接、外部 `await broadcaster.publish({...})` → 客户端 `ws.receive_json()` 拿到事件
     - 关闭 WS → broadcaster._queues 集合长度回到先前值（unregister 验证）
     - 守护进程关闭后 snapshot 仍可返回最近 sample（降级验证）
- **Files**:
  - `backend/app/routers/printer_status.py`（新）
  - `backend/app/main.py`（改 lifespan + include_router）
  - `backend/tests/test_printer_status_router.py`（新）
- **Done when**:
  - 全部单测 PASS
  - lifespan 启动 / 关闭无残留 task
  - 三个对象稳定挂在 `app.state.printer_status_*`
- **单测要求**: snapshot 结构 / WS 推送 / WS 注销 / 降级 4 类

### Task 3.2: PUT /api/printers/{id} 切 PrinterUpdate + reconcile_one 钩子

- **PRD CUJ**: prd-007 CUJ-1
- **依赖**: 1.1（Printer 三列）+ 1.2（PrinterUpdate）+ 2.1（MqttDaemon.reconcile_one / unsubscribe_one）
- **Do**:
  1. 改 `backend/app/routers/printers.py`：
     - import 改 `from ..schemas import PrinterCreate, PrinterUpdate, PrinterOut`
     - `POST /api/printers`：保持 `PrinterCreate`；在 `db.commit() + db.refresh()` **之后** `request.app.state.printer_status_daemon.reconcile_one(printer.id)`；endpoint 注入 `request: Request`。
     - `PUT /api/printers/{id}`：签名改 `def update_printer(printer_id: int, data: PrinterUpdate, request: Request, db: Session = Depends(get_db))`；`partial = data.model_dump(exclude_unset=True)`；逐字段 `setattr(printer, k, v)`（含空串 → 清空语义 — 把 None / "" 都视为「用户传值」由 `exclude_unset=True` 区分；若 v == "" 则 setattr None，与 nullable 列一致）；`db.commit() + db.refresh()`；**commit 之后**调 `request.app.state.printer_status_daemon.reconcile_one(printer_id)`。
     - `DELETE /api/printers/{id}`：在 `db.delete + db.commit` **之前**调 `request.app.state.printer_status_daemon.unsubscribe_one(printer_id)`。
  2. 单测 `backend/tests/test_printers_router_credentials.py`（沿用 conftest 现有 TestClient + mock app.state.printer_status_daemon）：
     - PUT partial：只传 `{"ip": "1.2.3.4"}` 不影响 `name / serial / access_code`
     - PUT 空字符串 → setattr None 清空（验证 DB 行 ip 字段变 None）
     - PUT 未传 `access_code` key → DB 行 `access_code` 保持原值
     - PUT 成功后 mock daemon 的 `reconcile_one` 被调一次、参数为 printer_id、**调用时机在 `db.commit()` 之后**（用 mock 注入 sequence 验证）
     - DELETE 之前 `unsubscribe_one` 被调
     - POST 成功后 `reconcile_one` 被调
     - PrinterOut 响应里有 `access_code_masked` 而非原值（防止泄露回归）
- **Files**:
  - `backend/app/routers/printers.py`（改）
  - `backend/tests/test_printers_router_credentials.py`（新）
- **Done when**:
  - 全部单测 PASS
  - reconcile_one 严格在 commit 之后被调
  - 响应里无 access_code 原值
- **单测要求**: partial update / 清空 / 不传保留 / commit-then-reconcile 顺序 / unsubscribe / response masked 6 类

---

## Parallel Group 4 — 前端（3 task 并行）

依赖 G3 全部完成。T4.1 改 `api/client.ts` 与 T4.2 改 `Settings.tsx`、T4.3 新建 `PrinterStatus.tsx` + 路由文件分明。T4.2 / T4.3 都会 import T4.1 新增的 API 与类型 — 但只是只读引用，文件级无冲突。

### Task 4.1: api/client.ts 类型与方法扩展

- **PRD CUJ**: prd-007 CUJ-1 + CUJ-2 前端基础
- **依赖**: G3（确认后端契约稳定）
- **Do**:
  1. 改 `frontend/src/api/client.ts`：
     - 新增类型 `PrinterStatusSnapshot`（对齐 backend `PrinterStatusOut`）：`{ printer_id, name, state: "running"|"pause"|"idle"|"offline"|"unconfigured", today_working_minutes, today_total_minutes, last_state_change_ts: string|null, timeline: TimelineSegment[] }`；`TimelineSegment` = `{ start_minute, end_minute, state: "running"|"pause"|"idle"|"offline" }`。
     - 新增类型 `PrinterStatusEvent` = `{ type: "state_change", printer_id, state, ts: string }` + `PrinterStatusPing` = `{ type: "ping", ts?: string }` + 联合 `PrinterWSMessage`。
     - 新增类型 `Printer = { id, name, ip: string|null, serial: string|null, access_code_masked: string|null }`；`PrinterUpdateBody = Partial<{ name, ip, serial, access_code }>`（注意只有 `access_code` 一项是原值字段、其他凭证字段都是字符串可空可清）。
     - `api.getPrinterStatusSnapshot()` → `request<PrinterStatusSnapshot[]>('/printers/status/snapshot')`。
     - 改 `api.getPrinters()` 返回类型从 `any[]` 改为 `Printer[]`。
     - 改 `api.updatePrinter(id: number, body: PrinterUpdateBody)`（替代当前 `any` 签名）— 调用方 `Settings.tsx` 在 T4.2 同步改；若现有任何老调用方 `JSON.stringify(data)` 还在传旧 shape，保留向后兼容（接收 partial 不影响）。
  2. 单测（若项目无前端测试基础设施则跳过此 task 的测试要求，仅做 TypeScript 类型检查通过即可；与 iter4 现状保持一致）：
     - `npm run build` 在 frontend 目录通过、`tsc --noEmit` 无类型错误
- **Files**:
  - `frontend/src/api/client.ts`（改）
- **Done when**:
  - `cd frontend && npx tsc --noEmit` 无错
  - 新增 5 个类型 + 1 个新方法 + 1 个改方法签名
- **单测要求**: 沿用 iter4 现状（无前端测试基础设施时仅做类型检查）

### Task 4.2: Settings.tsx 「编辑」按钮 + EditPrinterModal

- **PRD CUJ**: prd-007 CUJ-1
- **依赖**: 4.1（`Printer` / `PrinterUpdateBody` 类型 + `api.updatePrinter` 新签名）
- **Do**:
  1. 新建 `frontend/src/pages/EditPrinterModal.tsx`（独立组件，预留未来 PrinterStatus 页 tooltip 引导复用）：
     - Props：`open: boolean / printer: Printer | null / onCancel() / onSaved()`
     - Form 四字段：`name`（必填 Input）/ `ip`（Input，placeholder「如：192.168.1.123」）/ `serial`（Input，placeholder「如：01P00A123456789」）/ `access_code`（`Input.Password` 带「眼睛图标」可显隐；预填模式下显示 `••••<末 4 位>`，等于 `printer.access_code_masked` 的可视化样式；用户**不修改密码框**点保存 → 前端构造 body 时 omit `access_code` key；用户清空再保存 → 传空串 → 后端清掉）
     - 字段下方灰色 `<Typography.Text type="secondary">`：「IP / 序列号 / 访问码三项全填才会启动监测；任一为空显示『未配置』。访问码勿外传。」
     - 「确定」点击 → `api.updatePrinter(printer.id, partial)` → `message.success('已保存')` → `onSaved()` → 关弹窗
     - 「取消」直接 onCancel
  2. 改 `frontend/src/pages/Settings.tsx` 「打印机管理」卡片：
     - 表格 `操作` 列在删除按钮**左侧**加「编辑」按钮（铅笔图标 `<EditOutlined />`）
     - 点击 → setState 选中行 printer + 打开 `EditPrinterModal`
     - 表格行新增一列或在名称右侧加徽标：若 `ip == null || serial == null || access_code_masked == null` → 显示灰色 `<Tag>未配置监测</Tag>`
     - 「新增打印机」批量弹窗**保持不变**（只填 name；prd-007 CUJ-1 Step 3 明示）
  3. 单测（沿用 iter4 现状，若项目有 RTL 基础则补，否则跳过）：
     - EditPrinterModal 打开预填、密码框预填态显示掩码、保存调 `api.updatePrinter` 携带正确 partial body、密码框未改时 body 无 access_code key
     - Settings 表格行未配置态显示灰色徽标
- **Files**:
  - `frontend/src/pages/EditPrinterModal.tsx`（新）
  - `frontend/src/pages/Settings.tsx`（改）
- **Done when**:
  - 编辑弹窗能打开、表单四字段渲染正确、密码框掩码 + 眼睛切换 OK
  - 保存调 `api.updatePrinter(id, partial)`、不修改密码框时不传 access_code
  - 表格未配置行有灰色徽标
- **单测要求**: 沿用 iter4 现状（无 RTL 时仅手测）

### Task 4.3: PrinterStatus 页 + 路由 + 菜单 + WS hook + Timeline24h

- **PRD CUJ**: prd-007 CUJ-2
- **依赖**: 4.1（`api.getPrinterStatusSnapshot / PrinterStatusSnapshot / PrinterStatusEvent` 类型）
- **Do**:
  1. 新建 `frontend/src/pages/printer_status/constants.ts`：
     - `STATE_COLORS = { running: '#52c41a', pause: '#faad14', idle: '#d9d9d9', offline: '#ff4d4f', unconfigured: '#f0f0f0' }`
     - `STATE_LABELS = { running: '打印中', pause: '暂停', idle: '空闲', offline: '离线', unconfigured: '未配置' }`
     - `BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000]`
  2. 新建 `frontend/src/pages/printer_status/usePrinterStatusWS.ts`（~80 行）：
     - 入参 `(onEvent: (e: PrinterStatusEvent) => void, onReconnect: () => void)`
     - 用 `useRef` 管 `WebSocket` 实例 + 重连退避索引；`useState` 管 `status: 'connecting'|'connected'|'reconnecting'|'disconnected'`
     - 路径用 `new WebSocket(((location.protocol === 'https:') ? 'wss' : 'ws') + '://' + location.host + '/api/ws/printers/status')`
     - `onopen` → status='connected' + 退避归零 + 调 `onReconnect()`（mount 首次也算「重连」无伤、PRD 允许）；`onmessage` → JSON.parse → 若 `type === 'state_change'` 调 `onEvent(e)`，若 `type === 'ping'` 回 `{"type":"pong"}`；`onclose / onerror` → status='reconnecting' → `setTimeout(reconnect, BACKOFF_MS[i] || 30000)`；多次失败仍持续重连，但若退避索引 >= 6 且累计失败 ≥ 6 次 → status='disconnected'（红色降级文案触发）
     - `useEffect` cleanup → ws.close()
  3. 新建 `frontend/src/pages/printer_status/Timeline24h.tsx`：
     - 入参 `(timeline: TimelineSegment[], state: PrinterStatus['state'], now: Date)`
     - 容器 `position: relative; width: 100%; height: 24px; background: '#f0f0f0'`
     - timeline 渲染 `<div>` 每段，`left: (start_minute/1440)*100%`, `width: ((end-start)/1440)*100%`, `minWidth: 1px`, `background: STATE_COLORS[seg.state]`；`offline` 用 CSS `repeating-linear-gradient(45deg, #ff4d4f, #ff4d4f 4px, #ffccc7 4px, #ffccc7 8px)` 红条纹
     - 「现在」深色竖线：`left: (currentMinute(now)/1440)*100%; top:0; bottom:0; width:2px; background:#000`
     - hover tooltip 用 `title={...}` 显示该段时间范围 + 状态
     - 底部 5 个刻度文字 `0 / 6 / 12 / 18 / 24`（绝对定位）
     - `state === 'unconfigured'` → 整条 bar `STATE_COLORS.unconfigured` + 1px 虚线 border + 居中文字「未配置」
  4. 新建 `frontend/src/pages/printer_status/PrinterCard.tsx`：
     - 入参 `(snapshot: PrinterStatusSnapshot, now: Date)`
     - 左上名称 16px 粗、右上状态徽章（圆角胶囊，配色按 `STATE_COLORS`，`running` 左侧呼吸点动画 `keyframes`）
     - 中部 `今日已工作：X 小时 YY 分 / 24 小时`（≥ 60 分按时分，< 60 分按 `X 分`）+ 灰色小字 `利用率 ZZ.Z%`（`min(today_working_minutes/14.4, 100.0).toFixed(1)`）
     - 底部 `<Timeline24h>`
     - `state === 'unconfigured'` → 徽章 `Tag` 灰底虚线 + 齿轮图标 + `<Tooltip>`「点右上角『设置』补填 IP / 序列号 / 访问码」
  5. 新建 `frontend/src/pages/PrinterStatus.tsx`：
     - mount：`getPrinterStatusSnapshot()` 拿初值 → setState；然后 `usePrinterStatusWS(onEvent, onReconnect)`；`onReconnect` = 再次拉 snapshot 替换 state
     - `onEvent` 收 `state_change` → 找到对应 `printer_id` 的卡片 → 更新 `state` + 给 `timeline` 末段「延展」/ 新增一段（简单做法：直接重新拉 snapshot；为了性能，本 MVP 在 `onEvent` 内只 patch `state` 字段，再用一个 1s setInterval 触发末段渲染延展；`last_state_change_ts` 用事件 ts 替换）
     - 右上角三态指示：`status==='connected'` → 绿点「实时连接中」/ `reconnecting` → 黄点「重连中…」/ `disconnected` → 红点「实时连接断开，X 秒前 snapshot」（X 由 last snapshot 时间差算）
     - 空打印机态：snapshot 长度 0 → 「暂无打印机，请先到 [系统设置 → 打印机管理](/settings) 添加。」
     - snapshot 拉取失败：整页空态 + 「重试」按钮
     - 4 列响应式网格：`Row gutter=[16,16]` + `Col xs=24 sm=12 lg=6`
  6. 路由 + 菜单：
     - 改 `frontend/src/App.tsx`：`<Route path="/printers/status" element={<PrinterStatus />} />` 加在 `/settings` 路由前
     - 改 `frontend/src/components/Layout.tsx`（或当前 nav 容器，按现存文件名为准）：菜单数组中在 Dashboard 与「系统设置」之间插入 `{ key: '/printers/status', label: '打印机状态', icon: <DesktopOutlined /> }`
  7. 单测（沿用 iter4 现状，若有 RTL 基础则补三态指示 + 徽章颜色映射 + 时间轴颜色映射 + 重连后再拉 snapshot 三类用例；否则手测）
- **Files**:
  - `frontend/src/pages/printer_status/constants.ts`（新）
  - `frontend/src/pages/printer_status/usePrinterStatusWS.ts`（新）
  - `frontend/src/pages/printer_status/Timeline24h.tsx`（新）
  - `frontend/src/pages/printer_status/PrinterCard.tsx`（新）
  - `frontend/src/pages/PrinterStatus.tsx`（新）
  - `frontend/src/App.tsx`（改 — 加 route）
  - `frontend/src/components/Layout.tsx`（改 — 加菜单项；若实际文件名不同按文件名为准）
- **Done when**:
  - `npm run build` 成功
  - 路由 + 菜单显示
  - 4 卡片渲染、徽章颜色、时间轴 bar 颜色映射、空态正确
  - WS 断线指数退避重连 + 重连后再拉 snapshot 行为正确（手测：杀 backend 重启）
- **单测要求**: 沿用 iter4 现状（无 RTL 时仅做 build 通过 + 手测验收）

---

## Conflict Risks

| 文件 | 涉及 task | 风险 | 缓解 |
|---|---|---|---|
| `backend/app/models.py` | T1.1 单独 | 无冲突 | — |
| `backend/app/schemas.py` | T1.2 单独 | 无冲突 | — |
| `backend/app/main.py` | T3.1 单独 | G3 内 T3.2 不碰 main.py | — |
| `backend/app/routers/printers.py` | T3.2 单独 | 无冲突 | — |
| `backend/app/routers/printer_status.py` | T3.1 单独（新建） | 无冲突 | — |
| `backend/app/services/printer_mqtt_daemon.py` | T2.1 单独（新建） | 无冲突 | — |
| `backend/app/services/printer_status_sampler.py` | T2.2 单独（新建） | 无冲突 | — |
| `backend/app/services/printer_utilization.py` | T2.3 单独（新建） | 无冲突 | — |
| `frontend/src/api/client.ts` | T4.1 单独 | T4.2 / T4.3 只 import 不改 | T4.1 先完成提前 merge，T4.2 / T4.3 再拉新基线（若强行并行，agent 各自拿到 T4.1 的 branch 起 worktree 也可） |
| `frontend/src/pages/Settings.tsx` | T4.2 单独 | 无冲突 | — |
| `frontend/src/pages/PrinterStatus.tsx` 系 | T4.3 单独（新建） | 无冲突 | — |
| `frontend/src/App.tsx` & `Layout.tsx` | T4.3 单独 | T4.2 不碰 | — |

**唯一软依赖**：T4.2 / T4.3 都用到 T4.1 的 `api.getPrinterStatusSnapshot` / `Printer` / `PrinterUpdateBody` 类型。三 task 同组并行时 T4.2 / T4.3 各自 worktree 内**可以先添加局部 type 占位**或**统一从 T4.1 分支拉取后再起 T4.2 / T4.3 worktree**（执行 agent 选其一即可，二者皆不会引入文件冲突）。

## 效率说明

- 4 组 × 共 10 task — 顺序执行预估 10 单位时间；并行后预估 4 单位时间（每组取最长）— **约 2.5× 加速**。
- G2 三 task 完全无文件交集（守护进程 / sampler / 纯函数），并行价值最高。
- G4 三 task 一软依赖 + 两完全独立，并行价值显著。

## Blocker

无需用户拍板。所有 PRD / 设计 / 验收都已落地、TL 硬性约束已编入任务粒度；执行可直接开始。
