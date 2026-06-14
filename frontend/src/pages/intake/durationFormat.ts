// 把整数分钟数格式化为 "2h43m" / "30m" / "0m" 等显示文本。
export function formatDuration(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return '0m';
  const m = Math.floor(minutes);
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  if (h === 0) return `${rem}m`;
  if (rem === 0) return `${h}h`;
  return `${h}h${rem}m`;
}

// 把用户输入的 "2h43m" / "17m45s" / "30m" / "1h" 解析为整数分钟数；非法返回 null。
// 秒数四舍五入归并到分钟（17m45s -> 18m）。
export function parseDuration(text: string): number | null {
  if (typeof text !== 'string') return null;
  const s = text.trim().toLowerCase();
  if (!s) return null;
  const re = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/;
  const match = s.match(re);
  if (!match) return null;
  const [, hStr, mStr, sStr] = match;
  if (hStr === undefined && mStr === undefined && sStr === undefined) return null;
  const h = hStr ? parseInt(hStr, 10) : 0;
  const m = mStr ? parseInt(mStr, 10) : 0;
  const sec = sStr ? parseInt(sStr, 10) : 0;
  if (!Number.isFinite(h) || !Number.isFinite(m) || !Number.isFinite(sec)) return null;
  return h * 60 + m + Math.round(sec / 60);
}
