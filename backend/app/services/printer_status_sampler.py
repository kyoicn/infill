"""PRD-007 T2.2: Broadcaster + Sampler

Broadcaster：进程内 fanout，每个 WS 客户端独占 asyncio.Queue（maxsize=100），慢消费者
丢最旧（drop-oldest）。

Sampler：
- 同步入口 on_event(printer_id, state, ts) 给 MQTT daemon 在后台线程里调；线程安全用
  threading.Lock 保护内存映射。
- 双层写入：内层 _write_sample 只写库不广播；外层 on_event 在状态变化时调用 _write_sample
  并触发广播。心跳兜底直接调 _write_sample（同状态不广播）。
- 心跳循环（asyncio.Task）每 HEARTBEAT_INTERVAL_SEC 秒巡检：超过 OFFLINE_THRESHOLD_SEC
  无事件即转 offline（会广播）；否则补一条「状态不变」的兜底 sample（不广播）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Literal, Optional

from ..database import SessionLocal
from ..models import PrinterStatusSample

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SEC = 30
# Bambu 在 IDLE/RUNNING 稳态下的 MQTT 推送间隔实测可达 2-5 分钟。设计文档原
# 假设的 90s 阈值会把"懒推送"误判为离线，造成 idle/offline 锯齿。把阈值拉到
# 10 分钟覆盖 Bambu 真实推送间隔；真离线由 daemon 的 _on_disconnect 直接写
# 一条 offline sample（不依赖此 timeout）。
OFFLINE_THRESHOLD_SEC = 600

State = Literal["running", "pause", "idle", "offline"]


class Broadcaster:
    """进程内 fanout。每个 WS 客户端 register() 拿一个独占 queue；publish() 向全部
    queue put_nowait；满则 drop-oldest 后塞新。"""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.add(q)
        return q

    async def unregister(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._queues.discard(q)

    async def publish(self, event: dict) -> None:
        async with self._lock:
            targets = list(self._queues)
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass


class Sampler:
    """协调 MQTT 事件 → sample 落库 + Broadcaster 推送。

    线程模型：on_event / on_unconfigured 是同步入口，由 paho on_message 后台线程调用，
    用 threading.Lock 保护内存映射；广播协程通过 asyncio.run_coroutine_threadsafe 投递
    给主 event loop。心跳循环本身跑在主 loop 的 asyncio.Task 中。
    """

    def __init__(
        self,
        broadcaster: Broadcaster,
        db_session_factory=SessionLocal,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.broadcaster = broadcaster
        self._db_session_factory = db_session_factory
        self._loop: Optional[asyncio.AbstractEventLoop] = loop

        self._last_event_ts: dict[int, datetime] = {}
        self._last_state: dict[int, str] = {}
        self._lock = threading.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ---------- 同步入口（paho 后台线程） ----------

    def on_event(self, printer_id: int, state: State, ts: datetime) -> None:
        """MQTT 推送到达时调用：状态变化才写 sample + 广播；同状态只刷新 last_event_ts。"""
        with self._lock:
            prev = self._last_state.get(printer_id)
            self._last_event_ts[printer_id] = ts
            changed = prev != state
            if changed:
                self._last_state[printer_id] = state

        if changed:
            self._write_sample(printer_id, state, ts)
            self._dispatch_broadcast(printer_id, state, ts)

    def on_unconfigured(self, printer_id: int) -> None:
        """凭证被清空时调用：清掉内存状态 + 写一条 offline + 广播。"""
        with self._lock:
            self._last_state.pop(printer_id, None)
            self._last_event_ts.pop(printer_id, None)

        ts = datetime.now()
        self._write_sample(printer_id, "offline", ts)
        self._dispatch_broadcast(printer_id, "offline", ts)

    # ---------- 心跳循环（主 loop 的 asyncio.Task） ----------

    async def start_heartbeat_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def stop_heartbeat_loop(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
                await self._heartbeat_tick()
        except asyncio.CancelledError:
            raise

    async def _heartbeat_tick(self) -> None:
        """巡检：对每台机判断是否需要转 offline 或补「状态不变」兜底 sample。"""
        now = datetime.now()
        with self._lock:
            snapshot = [
                (pid, self._last_event_ts.get(pid), self._last_state.get(pid))
                for pid in list(self._last_event_ts.keys())
            ]

        for printer_id, last_ts, last_state in snapshot:
            if last_ts is None:
                continue
            elapsed = (now - last_ts).total_seconds()
            if elapsed > OFFLINE_THRESHOLD_SEC and last_state != "offline":
                # 走 on_event 路径：会写 sample + 广播 + 更新 _last_state
                self.on_event(printer_id, "offline", now)
            # 不再写"同状态"兜底 sample —— 利用率算法靠线段插值，相邻状态变化
            # 之间不需要重复 sample；旧逻辑每 30s 一条同状态行纯属噪声。

    # ---------- 内部：写库 / 广播 ----------

    def _write_sample(self, printer_id: int, state: str, ts: datetime) -> None:
        """只写库不广播。心跳兜底与状态变化都走这里。"""
        try:
            with self._db_session_factory() as session:
                session.add(
                    PrinterStatusSample(printer_id=printer_id, ts=ts, state=state)
                )
                session.commit()
        except Exception:
            logger.exception(
                "printer_status: failed to write sample printer_id=%s state=%s",
                printer_id,
                state,
            )

    def _dispatch_broadcast(self, printer_id: int, state: str, ts: datetime) -> None:
        """跨线程把广播协程投递到主 event loop。无 loop 时只 WARNING 不抛。"""
        if self._loop is None:
            logger.warning(
                "printer_status: no event loop bound; skipping broadcast "
                "printer_id=%s state=%s",
                printer_id,
                state,
            )
            return
        asyncio.run_coroutine_threadsafe(
            self._broadcast_state_change(printer_id, state, ts), self._loop
        )

    async def _broadcast_state_change(
        self, printer_id: int, state: str, ts: datetime
    ) -> None:
        event = {
            "type": "state_change",
            "printer_id": printer_id,
            "state": state,
            "ts": ts.isoformat(),
        }
        await self.broadcaster.publish(event)
