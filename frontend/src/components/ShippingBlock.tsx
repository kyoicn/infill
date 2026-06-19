import { Button, Tooltip, message } from 'antd';
import { CopyOutlined } from '@ant-design/icons';

interface ShippingBlockProps {
  name?: string | null;
  phone?: string | null;
  address?: string | null;
  /** 紧凑模式：单行展示，省略折行 */
  compact?: boolean;
}

/**
 * 收货信息共享展示组件——用于自动导入预览页 + 订单浏览页。
 * 复制格式（两行）:
 *   张三 18888888888
 *   湖南省...
 */
export default function ShippingBlock({ name, phone, address, compact }: ShippingBlockProps) {
  if (!name && !phone && !address) {
    return (
      <span style={{ color: 'rgba(0,0,0,0.25)', fontSize: 12, fontStyle: 'italic' }}>
        无收货信息
      </span>
    );
  }

  const line1 = [name, phone].filter(Boolean).join(' ');
  const clipText = [line1, address].filter(Boolean).join('\n');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(clipText);
      message.success('已复制收货信息');
    } catch {
      message.error('复制失败');
    }
  };

  if (compact) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span
          style={{
            fontSize: 12,
            color: 'rgba(0,0,0,0.85)',
            maxWidth: 240,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={clipText}
        >
          {line1 || '(无姓名/电话)'} · {address || '(无地址)'}
        </span>
        <Tooltip title="复制姓名+电话+地址">
          <Button size="small" type="text" icon={<CopyOutlined />} onClick={handleCopy} />
        </Tooltip>
      </span>
    );
  }

  return (
    <div
      style={{
        background: '#fafafa',
        border: '1px solid #f0f0f0',
        borderRadius: 6,
        padding: '8px 10px',
        position: 'relative',
        fontSize: 12,
        lineHeight: 1.6,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: 'rgba(0,0,0,0.85)' }}>
            <b>{name || '(无姓名)'}</b>
            {phone && (
              <span style={{ marginLeft: 8, fontFamily: 'monospace', color: 'rgba(0,0,0,0.65)' }}>
                {phone}
              </span>
            )}
          </div>
          <div style={{ color: 'rgba(0,0,0,0.65)', marginTop: 2, wordBreak: 'break-all' }}>
            {address || '(无地址)'}
          </div>
        </div>
        <Tooltip title="复制姓名+电话+地址">
          <Button
            size="small"
            type="text"
            icon={<CopyOutlined />}
            onClick={handleCopy}
            style={{ flexShrink: 0 }}
          />
        </Tooltip>
      </div>
    </div>
  );
}
