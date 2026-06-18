import { Button, Typography } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { AutoImportCommitResponse } from '../../api/client';

const { Text } = Typography;

interface SuccessPanelProps {
  response: AutoImportCommitResponse;
  sourcePlatform: 'xhs' | 'xianyu';
}

function formatMs(ms?: number): string {
  if (!ms || ms <= 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m ${r}s`;
}

function platformName(p: 'xhs' | 'xianyu'): string {
  return p === 'xhs' ? '小红书千帆' : '闲鱼';
}

export default function SuccessPanel(props: SuccessPanelProps) {
  const { response, sourcePlatform } = props;
  const navigate = useNavigate();
  const stats = response.stats;
  const added = stats?.['新增'] ?? 0;
  const skippedDup = stats?.['重复跳过'] ?? 0;
  const skippedManual = stats?.['手动跳过'] ?? 0;
  const matchRate = stats?.['SKU匹配率'] ?? 0;
  const orderIds = response.created_order_ids ?? [];
  const firstFive = orderIds.slice(0, 5);

  const otherPlatform: 'xhs' | 'xianyu' = sourcePlatform === 'xhs' ? 'xianyu' : 'xhs';

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 13, marginBottom: 6 }}>
        订单管理 · 自动导入 · 完成
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 600, margin: '0 0 24px' }}>导入完成</h1>

      <div
        style={{
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderRadius: 12,
          padding: '48px 32px',
          textAlign: 'center',
          marginBottom: 24,
        }}
      >
        <div
          style={{
            width: 72,
            height: 72,
            borderRadius: '50%',
            background: '#f6ffed',
            border: '3px solid #52c41a',
            color: '#52c41a',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 32,
            fontWeight: 700,
            marginBottom: 18,
          }}
        >
          <CheckOutlined />
        </div>
        <div style={{ fontSize: 22, fontWeight: 600, marginBottom: 8 }}>
          {added} 单已入待处理队列
        </div>
        <div style={{ fontSize: 14, color: 'rgba(0,0,0,0.65)', marginBottom: 28 }}>
          来自{platformName(sourcePlatform)} · 用时 {formatMs(response.total_ms)}
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 16,
            maxWidth: 720,
            margin: '0 auto 32px',
          }}
        >
          <StatBox label="新增订单" value={String(added)} highlight />
          <StatBox label="跳过重复" value={String(skippedDup)} />
          <StatBox label="手动跳过" value={String(skippedManual)} />
          <StatBox label="SKU 匹配率" value={`${Math.round(matchRate * 100)}%`} />
        </div>

        <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
          <Button type="primary" onClick={() => navigate('/orders')}>
            前往订单管理 →
          </Button>
          <Button onClick={() => window.location.reload()}>
            继续导入{platformName(otherPlatform)}
          </Button>
        </div>
      </div>

      <div
        style={{
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: '20px 24px',
        }}
      >
        <h2
          style={{
            margin: '0 0 12px',
            fontSize: 14,
            fontWeight: 600,
            color: 'rgba(0,0,0,0.65)',
          }}
        >
          本批详情
        </h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '8px 24px',
            fontSize: 13,
          }}
        >
          <DetailRow k="来源平台" v={platformName(sourcePlatform)} />
          <DetailRow k="耗时" v={formatMs(response.total_ms)} />
          <DetailRow k="新增订单" v={`${added} 单`} />
          <DetailRow k="跳过重复" v={`${skippedDup} 单`} />
          <DetailRow k="手动跳过" v={`${skippedManual} 单`} />
          <DetailRow k="SKU 匹配率" v={`${Math.round(matchRate * 100)}%`} />
        </div>

        {orderIds.length > 0 && (
          <div
            style={{
              marginTop: 16,
              background: '#fafafa',
              borderRadius: 6,
              padding: '12px 16px',
              fontSize: 12.5,
              color: 'rgba(0,0,0,0.65)',
              lineHeight: 1.7,
            }}
          >
            <b style={{ color: 'rgba(0,0,0,0.88)' }}>
              本次新增的订单 ID（前 {firstFive.length} 个）：
            </b>
            <br />
            {firstFive.map((id) => (
              <span
                key={id}
                style={{
                  fontFamily: 'monospace',
                  color: '#1677ff',
                  marginRight: 8,
                }}
              >
                #{id}
              </span>
            ))}
            {orderIds.length > firstFive.length && (
              <>
                ...{' '}
                <a onClick={() => navigate('/orders')}>
                  查看全部 {orderIds.length} 单 →
                </a>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StatBox({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div
      style={{
        background: highlight ? '#f6ffed' : '#fafafa',
        border: highlight ? '1px solid #b7eb8f' : 'none',
        borderRadius: 8,
        padding: '18px 16px',
      }}
    >
      <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.45)', marginBottom: 6 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 28,
          fontWeight: 600,
          color: highlight ? '#52c41a' : 'rgba(0,0,0,0.88)',
          fontFamily: 'monospace',
          lineHeight: 1,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function DetailRow({ k, v }: { k: string; v: string }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '6px 0',
        borderBottom: '1px solid #f0f0f0',
      }}
    >
      <Text type="secondary">{k}</Text>
      <span style={{ color: 'rgba(0,0,0,0.88)' }}>{v}</span>
    </div>
  );
}
