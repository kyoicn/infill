export const COLOR_PALETTE: Record<string, string> = {
  白色: '#ffffff',
  黑色: '#1f1f1f',
  灰色: '#8c8c8c',
  棕色: '#8b4513',
  粉色: '#ffc0cb',
  红色: '#f5222d',
  黄色: '#fadb14',
  蓝色: '#1677ff',
  绿色: '#52c41a',
  橙色: '#fa8c16',
  紫色: '#722ed1',
};

export const COMMON_COLOR_NAMES = [
  '白色',
  '黑色',
  '灰色',
  '棕色',
  '粉色',
  '红色',
  '黄色',
  '蓝色',
  '绿色',
  '橙色',
  '紫色',
];

/**
 * 把色名映射到 hex；未知色名返回 null（UI 用斜纹底纹表示）
 */
export function colorNameToSwatch(name: string): string | null {
  return COLOR_PALETTE[name] ?? null;
}
