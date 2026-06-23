import { Card, Tag, Tooltip } from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import { STATE_COLORS, STATE_LABELS } from './constants';
import { Timeline24h } from './Timeline24h';
import type {
  HistoryDay,
  PrinterStateOrUnconfigured,
  PrinterStatusSnapshot,
} from '../../api/client';

interface PrinterCardProps {
  snapshot: PrinterStatusSnapshot;
  now: Date;
  history?: HistoryDay[];
}

const TAG_COLOR: Record<PrinterStateOrUnconfigured, string> = {
  running: 'success',
  pause: 'warning',
  idle: 'default',
  offline: 'error',
  unconfigured: 'default',
};

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function formatDuration(m: number): string {
  if (m < 60) return `${m} 分`;
  return `${Math.floor(m / 60)} 小时 ${pad2(m % 60)} 分`;
}

function utilizationPct(workingMinutes: number): string {
  // 1440 分 = 24 小时 → /14.4 得百分比；clamp 上限 100（防 sample 漂移越界）
  const raw = workingMinutes / 14.4;
  const clamped = Math.max(0, Math.min(100, raw));
  return clamped.toFixed(1);
}

// 「打印中」徽章左侧呼吸点动画 —— 用一次性 <style> 注入，避免外挂 CSS 文件
const PULSE_STYLE = `@keyframes printerStatusPulse { 0%,100%{opacity:1} 50%{opacity:0.4} }`;

function fmtHistoryDate(iso: string): string {
  // "2026-06-23" → "6月23日"
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${parseInt(m[1], 10)}月${parseInt(m[2], 10)}日`;
}

function HistoryMiniBars({ history }: { history: HistoryDay[] }) {
  if (history.length === 0) return null;
  const avg = Math.round(
    history.reduce((s, d) => s + d.working_minutes, 0) / history.length,
  );
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
        最近 {history.length} 天
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 32 }}>
        {history.map((d, i) => {
          const ratio = d.working_minutes / 1440;
          const h = Math.max(2, Math.round(ratio * 32)); // 至少 2px 让空日也可见
          const isToday = i === history.length - 1;
          return (
            <Tooltip
              key={d.date}
              title={`${fmtHistoryDate(d.date)} ${formatDuration(d.working_minutes)}`}
            >
              <div
                style={{
                  flex: 1,
                  height: h,
                  background: STATE_COLORS.running,
                  opacity: isToday ? 1 : 0.6,
                  borderRadius: 2,
                  cursor: 'default',
                }}
              />
            </Tooltip>
          );
        })}
      </div>
      <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
        {history.length} 天日均：{formatDuration(avg)}
      </div>
    </div>
  );
}

export function PrinterCard({ snapshot, now, history }: PrinterCardProps) {
  const { state } = snapshot;

  const titleNode = (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <strong style={{ fontSize: 16 }}>{snapshot.name}</strong>
      <Tag color={TAG_COLOR[state]} style={{ marginInlineEnd: 0 }}>
        {state === 'running' && (
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: STATE_COLORS.running,
              marginRight: 4,
              animation: 'printerStatusPulse 1.4s ease-in-out infinite',
              verticalAlign: 'middle',
            }}
          />
        )}
        {STATE_LABELS[state]}
      </Tag>
    </div>
  );

  return (
    <Card size="small" title={titleNode}>
      <style>{PULSE_STYLE}</style>
      {state === 'unconfigured' ? (
        <div style={{ padding: '8px 0 12px' }}>
          <Tooltip title="点右上角『设置』补填 IP / 序列号 / 访问码">
            <Tag icon={<SettingOutlined />} style={{ borderStyle: 'dashed' }}>
              未配置监测
            </Tag>
          </Tooltip>
        </div>
      ) : (
        <div style={{ padding: '4px 0 12px' }}>
          <div>今日已工作：{formatDuration(snapshot.today_working_minutes)} / 24 小时</div>
          <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>
            利用率 {utilizationPct(snapshot.today_working_minutes)}%
          </div>
        </div>
      )}
      <Timeline24h timeline={snapshot.timeline} state={state} now={now} />
      {state !== 'unconfigured' && history && <HistoryMiniBars history={history} />}
    </Card>
  );
}
