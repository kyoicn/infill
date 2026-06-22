export const STATE_COLORS = {
  running: '#52c41a',
  pause: '#faad14',
  idle: '#d9d9d9',
  offline: '#ff4d4f',
  unconfigured: '#f0f0f0',
} as const;

export const STATE_LABELS = {
  running: '打印中',
  pause: '暂停',
  idle: '空闲',
  offline: '离线',
  unconfigured: '未配置',
} as const;

// 指数退避：1s/2s/4s/8s/16s/30s，之后稳定 30s 上限
export const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
