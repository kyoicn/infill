"""PRD-007 T2.2 单测：Broadcaster fanout + Sampler 心跳兜底 + 离线检测 + 同状态去重。

不依赖 pytest-asyncio / freezegun：
- 异步用例用 asyncio.new_event_loop() + loop.run_until_complete() 跑
- 时间推进用 monkeypatch 替换 datetime.now（注入到 sampler 模块的 datetime 引用）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.services import printer_status_sampler as sampler_mod
from app.services.printer_status_sampler import (
    Broadcaster,
    HEARTBEAT_INTERVAL_SEC,
    OFFLINE_THRESHOLD_SEC,
    Sampler,
)


# ---------- helpers ----------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _drain():
    """让通过 run_coroutine_threadsafe 投递的协程被实际执行。

    单次 await sleep(0) 不一定能让 self-loop 的 threadsafe 投递跑完，给个非零等待
    确保 selector 有机会处理 self-pipe 唤醒。
    """
    await asyncio.sleep(0.01)
    await asyncio.sleep(0)


def _make_db_factory():
    """返回 (factory, written_samples_list)。

    factory() 返回一个 context manager，进入返回 MagicMock session（session.add 会把
    传入对象 append 到 written_samples_list；session.commit 也是 MagicMock，便于断言）。
    """
    written: list = []

    class _FakeSession:
        def __init__(self):
            self.add = MagicMock(side_effect=lambda obj: written.append(obj))
            self.commit = MagicMock()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    factory = MagicMock(side_effect=lambda: _FakeSession())
    return factory, written


# =========================================================================
# case 1: Broadcaster fanout — 3 个 queue 都收到事件
# =========================================================================

def test_broadcaster_fanout_delivers_to_every_registered_queue():
    async def go():
        b = Broadcaster()
        q1 = await b.register()
        q2 = await b.register()
        q3 = await b.register()

        event = {"x": 1}
        await b.publish(event)

        return q1.get_nowait(), q2.get_nowait(), q3.get_nowait()

    e1, e2, e3 = _run(go())
    assert e1 == {"x": 1}
    assert e2 == {"x": 1}
    assert e3 == {"x": 1}


# =========================================================================
# case 2: 慢消费者 drop-oldest — 连发 101 条后 queue 长度仍 100、最旧丢、最新在
# =========================================================================

def test_broadcaster_drops_oldest_when_slow_consumer_queue_full():
    async def go():
        b = Broadcaster()
        q = await b.register()
        for i in range(101):
            await b.publish({"i": i})
        return q

    q = _run(go())
    assert q.qsize() == 100

    # 全部抽出来看：应该没有 i=0（最旧丢了），最后一条是 i=100
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    indices = [e["i"] for e in items]
    assert 0 not in indices, "最旧的 i=0 应该被 drop"
    assert indices[-1] == 100, "最新 i=100 应该塞进去"
    # 也意味着保留的是 [1..100] 这 100 个
    assert indices == list(range(1, 101))


# =========================================================================
# case 3: Sampler 同状态去重 — 连续两次相同 state 只写一次 + 只广播一次
# =========================================================================

def test_sampler_on_event_only_writes_and_broadcasts_on_state_change():
    factory, written = _make_db_factory()

    # 用 MagicMock broadcaster，绕过真 asyncio.Queue；直接断言 publish 调用次数
    broadcaster = MagicMock(spec=Broadcaster)

    async def setup_and_drive():
        loop = asyncio.get_running_loop()
        s = Sampler(broadcaster, db_session_factory=factory, loop=loop)

        # publish 返回一个已完成的 future（让 run_coroutine_threadsafe 不报错）
        async def _fake_publish(event):
            return None

        broadcaster.publish = MagicMock(side_effect=_fake_publish)

        t1 = datetime(2026, 6, 22, 10, 0, 0)
        t2 = datetime(2026, 6, 22, 10, 0, 30)
        s.on_event(1, "running", t1)
        s.on_event(1, "running", t2)

        # run_coroutine_threadsafe 投递到本 loop 但需要让 loop 跑一下消费掉
        await _drain()

        return s

    _run(setup_and_drive())

    # 写库只发生一次（首次进入 running）
    assert len(written) == 1
    assert written[0].printer_id == 1
    assert written[0].state == "running"
    # 广播只发生一次
    assert broadcaster.publish.call_count == 1
    published = broadcaster.publish.call_args.args[0]
    assert published["type"] == "state_change"
    assert published["printer_id"] == 1
    assert published["state"] == "running"


# =========================================================================
# case 4: 心跳兜底 — 30s 后 state 仍 running 时再写一条 sample、广播不增加
# =========================================================================

def test_heartbeat_tick_writes_steady_state_sample_without_broadcasting(monkeypatch):
    factory, written = _make_db_factory()
    broadcaster = MagicMock(spec=Broadcaster)

    async def go():
        loop = asyncio.get_running_loop()
        s = Sampler(broadcaster, db_session_factory=factory, loop=loop)

        async def _fake_publish(event):
            return None

        broadcaster.publish = MagicMock(side_effect=_fake_publish)

        t0 = datetime(2026, 6, 22, 10, 0, 0)
        s.on_event(1, "running", t0)
        await _drain()  # 让 first broadcast 消费

        assert len(written) == 1
        assert broadcaster.publish.call_count == 1

        # 模拟时间推进 30s（< OFFLINE_THRESHOLD_SEC）
        t_now = t0 + timedelta(seconds=HEARTBEAT_INTERVAL_SEC)
        monkeypatch.setattr(
            sampler_mod, "datetime", _FrozenDatetime(t_now), raising=True
        )
        await s._heartbeat_tick()
        await _drain()

        return

    _run(go())

    # 写库 +1（心跳兜底），总计 2
    assert len(written) == 2
    # 第二条仍是 running
    assert written[1].state == "running"
    assert written[1].printer_id == 1
    # 广播不增加
    assert broadcaster.publish.call_count == 1


# =========================================================================
# case 5: 离线检测 — 91s 无事件 → 写 offline + 广播
# =========================================================================

def test_heartbeat_tick_detects_offline_and_broadcasts(monkeypatch):
    factory, written = _make_db_factory()
    broadcaster = MagicMock(spec=Broadcaster)

    async def go():
        loop = asyncio.get_running_loop()
        s = Sampler(broadcaster, db_session_factory=factory, loop=loop)

        async def _fake_publish(event):
            return None

        broadcaster.publish = MagicMock(side_effect=_fake_publish)

        t0 = datetime(2026, 6, 22, 10, 0, 0)
        s.on_event(1, "running", t0)
        await asyncio.sleep(0)

        # 推进 91s（> OFFLINE_THRESHOLD_SEC）
        t_now = t0 + timedelta(seconds=OFFLINE_THRESHOLD_SEC + 1)
        monkeypatch.setattr(
            sampler_mod, "datetime", _FrozenDatetime(t_now), raising=True
        )
        await s._heartbeat_tick()
        await _drain()

        return

    _run(go())

    # 写库 +1（offline），总计 2
    assert len(written) == 2
    assert written[1].state == "offline"
    assert written[1].printer_id == 1
    # 广播 +1（offline 事件）
    assert broadcaster.publish.call_count == 2
    last_event = broadcaster.publish.call_args.args[0]
    assert last_event["state"] == "offline"
    assert last_event["printer_id"] == 1
    assert last_event["type"] == "state_change"


# =========================================================================
# case 6: on_unconfigured — 写 offline + 广播 + 内存清空
# =========================================================================

def test_on_unconfigured_writes_offline_clears_memory_and_broadcasts():
    factory, written = _make_db_factory()
    broadcaster = MagicMock(spec=Broadcaster)

    async def go():
        loop = asyncio.get_running_loop()
        s = Sampler(broadcaster, db_session_factory=factory, loop=loop)

        async def _fake_publish(event):
            return None

        broadcaster.publish = MagicMock(side_effect=_fake_publish)

        t0 = datetime(2026, 6, 22, 10, 0, 0)
        s.on_event(1, "running", t0)
        await _drain()
        assert len(written) == 1
        assert broadcaster.publish.call_count == 1

        s.on_unconfigured(1)
        await _drain()

        return s

    s = _run(go())

    # 写库 +1（offline），总计 2
    assert len(written) == 2
    assert written[1].state == "offline"
    assert written[1].printer_id == 1
    # 广播 +1
    assert broadcaster.publish.call_count == 2
    last_event = broadcaster.publish.call_args.args[0]
    assert last_event["state"] == "offline"
    # 内存清空
    assert 1 not in s._last_state
    assert 1 not in s._last_event_ts


# =========================================================================
# helper: 用一个可被 sampler 模块取 .now() 的"冻结 datetime" 替身
# =========================================================================

class _FrozenDatetime:
    """模拟 datetime 模块对象：sampler 调用 sampler_mod.datetime.now() → 返回 frozen_now。

    其余 datetime 调用透传给真 datetime（构造时的 datetime() 调用、timedelta 加减等
    不走这里 — 测试中 t0 等都是测试模块的真 datetime 直接构造）。
    """

    def __init__(self, frozen_now: datetime):
        self._frozen = frozen_now

    def now(self):
        return self._frozen

    def __getattr__(self, name):
        # 让 sampler 内部还有别的 datetime.X 调用时能走真模块（防御性，目前无）
        import datetime as real_datetime_module
        return getattr(real_datetime_module.datetime, name)
