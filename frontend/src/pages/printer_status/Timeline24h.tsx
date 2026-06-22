import { Tooltip } from 'antd';
import { STATE_COLORS, STATE_LABELS } from './constants';
import type { PrinterStateOrUnconfigured, TimelineSegment } from './types';

interface Timeline24hProps {
  timeline: TimelineSegment[];
  state: PrinterStateOrUnconfigured;
  now: Date;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function fmtHHMM(m: number): string {
  const clamped = Math.max(0, Math.min(1440, m));
  const h = Math.floor(clamped / 60);
  const mm = clamped % 60;
  return `${pad2(h === 24 ? 24 : h)}:${pad2(mm)}`;
}

// 离线段斜杠条纹底，与「实状态 offline」区分整体灰底（PRD 要求视觉可辨）
const OFFLINE_PATTERN =
  'repeating-linear-gradient(45deg, #ff4d4f, #ff4d4f 4px, #ffccc7 4px, #ffccc7 8px)';

const BAR_HEIGHT = 24;
const WRAPPER_HEIGHT = 40;

export function Timeline24h({ timeline, state, now }: Timeline24hProps) {
  const nowMinute = now.getHours() * 60 + now.getMinutes();
  const nowLeftPct = (nowMinute / 1440) * 100;

  const ticks: Array<{ label: string; style: React.CSSProperties }> = [
    { label: '0', style: { left: 0 } },
    { label: '6', style: { left: '25%', transform: 'translateX(-50%)' } },
    { label: '12', style: { left: '50%', transform: 'translateX(-50%)' } },
    { label: '18', style: { left: '75%', transform: 'translateX(-50%)' } },
    { label: '24', style: { right: 0 } },
  ];

  return (
    <div style={{ position: 'relative', width: '100%', height: WRAPPER_HEIGHT }}>
      <div
        style={{
          position: 'relative',
          width: '100%',
          height: BAR_HEIGHT,
          background: '#f0f0f0',
          borderRadius: 4,
          overflow: 'hidden',
          border: state === 'unconfigured' ? '1px dashed #bfbfbf' : undefined,
        }}
      >
        {state === 'unconfigured' ? (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: STATE_COLORS.unconfigured,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#999',
              fontSize: 12,
            }}
          >
            未配置
          </div>
        ) : (
          <>
            {timeline.map((seg, i) => {
              const left = (seg.start_minute / 1440) * 100;
              const width = ((seg.end_minute - seg.start_minute) / 1440) * 100;
              const isOffline = seg.state === 'offline';
              return (
                <Tooltip
                  key={`${i}-${seg.start_minute}`}
                  title={`${fmtHHMM(seg.start_minute)}-${fmtHHMM(seg.end_minute)} ${STATE_LABELS[seg.state]}`}
                >
                  <div
                    style={{
                      position: 'absolute',
                      left: `${left}%`,
                      width: `${width}%`,
                      top: 0,
                      bottom: 0,
                      minWidth: 1,
                      background: isOffline ? OFFLINE_PATTERN : STATE_COLORS[seg.state],
                    }}
                  />
                </Tooltip>
              );
            })}
            <div
              style={{
                position: 'absolute',
                left: `${nowLeftPct}%`,
                top: 0,
                bottom: 0,
                width: 2,
                background: '#000',
                zIndex: 2,
              }}
            />
          </>
        )}
      </div>
      {ticks.map((t) => (
        <span
          key={t.label}
          style={{
            position: 'absolute',
            top: BAR_HEIGHT + 4,
            fontSize: 10,
            color: '#999',
            ...t.style,
          }}
        >
          {t.label}
        </span>
      ))}
    </div>
  );
}
