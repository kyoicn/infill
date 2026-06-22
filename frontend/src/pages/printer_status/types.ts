// TODO(T4.1 merge): T4.1 会把以下类型 export 到 ../../api/client.ts。
// merge 时主 session 删本文件，把所有 from './types' 改为 from '../../api/client'，
// 并补上 api.getPrinterStatusSnapshot()。
// 本地占位仅用于让 T4.3 worktree tsc 能通过。

export type PrinterState = 'running' | 'pause' | 'idle' | 'offline';
export type PrinterStateOrUnconfigured = PrinterState | 'unconfigured';

export interface TimelineSegment {
  start_minute: number;
  end_minute: number;
  state: PrinterState;
}

export interface PrinterStatusSnapshot {
  printer_id: number;
  name: string;
  state: PrinterStateOrUnconfigured;
  today_working_minutes: number;
  today_total_minutes: number;
  last_state_change_ts: string | null;
  timeline: TimelineSegment[];
}

export interface PrinterStatusEvent {
  type: 'state_change';
  printer_id: number;
  state: PrinterState;
  ts: string;
}

export interface PrinterStatusPing {
  type: 'ping';
  ts?: string;
}

export type PrinterWSMessage = PrinterStatusEvent | PrinterStatusPing;
