"""PRD-007 T2.3：打印机今日利用率纯函数 + DB 便利接口。

`compute_today_snapshot` 是纯函数（无 DB / 无副作用）：
- 输入：升序样本（可能含一条 today_start 之前的最近 sample 用于推断起点 state）、now、today_start
- 输出：(today_working_minutes, timeline)

算法（线段插值）：
1. 截断到 [today_start, now]：忽略 ts > now 的样本；ts <= today_start 的样本仅保留最近一条
   作为 "pre-sample"，用其 state 给 today_start 时刻"虚拟补一条样本"。
   - 若无 pre-sample → today_start 时刻 state 默认 "offline"
2. 相邻样本对 (s_i, s_{i+1}) 的区间 [s_i.ts, s_{i+1}.ts) 归属 s_i.state
3. 末段从最后样本延展到 now，state 用最后样本的 state
4. start/end_minute 是自 today_start 起算的分钟数（向下取整），clamp 到 [0, 1440]
5. 相邻同 state 段合并 → timeline
6. working_minutes = state ∈ {"running","pause"} 的段累计分钟，再 min(_, 1440) 截断

`unconfigured` 状态不在本函数处理 —— 那是 router 层根据 Printer.ip/serial/access_code
是否齐全决定的；本函数只处理实际有 sample 的打印机或返回整段 offline 的空 sample 场景。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from ..models import PrinterStatusSample
from ..schemas_printer_status import TimelineSegment


_DAY_MINUTES = 1440
_WORKING_STATES = {"running", "pause"}
_State = Literal["running", "pause", "idle", "offline"]


def _minute_offset(ts: datetime, today_start: datetime) -> int:
    """从 today_start 起算的分钟数（向下取整）。可能为负 / > 1440，调用方负责 clamp。"""
    return int((ts - today_start).total_seconds() // 60)


def compute_today_snapshot(
    samples: list,
    now: datetime,
    today_start: datetime,
) -> tuple[int, list[TimelineSegment]]:
    """计算今日工作分钟数 + 24h 时间轴段列表。

    Args:
        samples: 按 ts 升序的样本列表。可包含一条 ts <= today_start 的 pre-sample
            （用于推断 today_start 时刻 state，实现跨午夜延展）；其余应在
            [today_start, now] 区间内。每个元素需有 `.ts` 和 `.state` 属性。
        now: 当前时刻（本地时区 naive datetime）。
        today_start: 今日 00:00（本地时区 naive datetime）。

    Returns:
        (today_working_minutes, timeline)
        - working_minutes 已 min(_, 1440) 截断
        - timeline 相邻同 state 段已合并
    """
    if now < today_start:
        return 0, []

    pre_state: _State = "offline"
    in_window: list = []
    for s in samples:
        if s.ts <= today_start:
            pre_state = s.state
        elif s.ts <= now:
            in_window.append(s)

    # 虚拟起点样本：从 today_start 开始，state 由 pre-sample 推断（无则 offline）
    class _VirtualSample:
        __slots__ = ("ts", "state")

        def __init__(self, ts: datetime, state: _State) -> None:
            self.ts = ts
            self.state = state

    timeline_samples: list = [_VirtualSample(today_start, pre_state)] + in_window

    # 构造原始段：相邻样本对归属前一条；末段延展到 now
    raw_segments: list[tuple[int, int, _State]] = []
    for i in range(len(timeline_samples)):
        seg_start = timeline_samples[i].ts
        seg_end = timeline_samples[i + 1].ts if i + 1 < len(timeline_samples) else now
        if seg_end <= seg_start:
            continue
        start_min = _minute_offset(seg_start, today_start)
        end_min = _minute_offset(seg_end, today_start)
        # clamp 到 [0, 1440]
        if start_min < 0:
            start_min = 0
        if end_min > _DAY_MINUTES:
            end_min = _DAY_MINUTES
        if end_min <= start_min:
            continue
        raw_segments.append((start_min, end_min, timeline_samples[i].state))

    # 合并相邻同 state 段
    merged: list[TimelineSegment] = []
    for start_min, end_min, state in raw_segments:
        if merged and merged[-1].state == state and merged[-1].end_minute == start_min:
            merged[-1] = TimelineSegment(
                start_minute=merged[-1].start_minute,
                end_minute=end_min,
                state=state,
            )
        else:
            merged.append(
                TimelineSegment(start_minute=start_min, end_minute=end_min, state=state)
            )

    working_minutes = sum(
        seg.end_minute - seg.start_minute for seg in merged if seg.state in _WORKING_STATES
    )
    working_minutes = min(working_minutes, _DAY_MINUTES)

    return working_minutes, merged


def compute_today_snapshot_for_printer(
    db: Session,
    printer_id: int,
    now: datetime,
) -> tuple[_State, int, datetime | None, list[TimelineSegment]]:
    """便利接口：查 DB → 调 compute_today_snapshot → 拼 4 元组。

    Returns:
        (current_state, today_working_minutes, last_state_change_ts, timeline)

        - current_state：timeline 最后一段的 state，整天无段时兜底 "offline"
        - last_state_change_ts："现在这段 state" 是从哪条样本开始的，整天无样本时为 None
    """
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)

    pre_sample = (
        db.query(PrinterStatusSample)
        .filter(
            PrinterStatusSample.printer_id == printer_id,
            PrinterStatusSample.ts <= today_start,
        )
        .order_by(PrinterStatusSample.ts.desc())
        .limit(1)
        .first()
    )
    today_samples = (
        db.query(PrinterStatusSample)
        .filter(
            PrinterStatusSample.printer_id == printer_id,
            PrinterStatusSample.ts > today_start,
            PrinterStatusSample.ts <= now,
        )
        .order_by(PrinterStatusSample.ts.asc())
        .all()
    )

    merged: list = ([pre_sample] if pre_sample is not None else []) + today_samples
    working_minutes, timeline = compute_today_snapshot(merged, now, today_start)

    current_state: _State = timeline[-1].state if timeline else "offline"

    # last_state_change_ts：找到"现在这段 state"最早的起点
    # 流程：从 today_samples 末尾倒着找，直到遇到 state 不同的样本（或越过 pre-sample）
    last_state_change_ts: datetime | None = None
    if today_samples:
        candidate_ts = today_samples[-1].ts
        for s in reversed(today_samples[:-1]):
            if s.state == current_state:
                candidate_ts = s.ts
            else:
                break
        else:
            # 全部 today_samples 与 current_state 同 state → 继续看 pre_sample
            if pre_sample is not None and pre_sample.state == current_state:
                candidate_ts = pre_sample.ts
        last_state_change_ts = candidate_ts
    elif pre_sample is not None and pre_sample.state == current_state:
        last_state_change_ts = pre_sample.ts

    return current_state, working_minutes, last_state_change_ts, timeline
