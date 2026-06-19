import { Empty, Spin, Tag } from 'antd';

export interface ScreenEntry {
  seq: number;
  status: 'captured' | 'parsing' | 'parsed' | 'failed';
  error: string | null;
}

interface ScreencapGridProps {
  batchId: string;
  screens: ScreenEntry[];
  parsedOrders: unknown[];
}

interface ParsedOrderHint {
  buyer_nickname?: string;
  external_order_id?: string;
  products?: Array<{ listing_title?: string; quantity?: number }>;
}

const STATUS_BADGE: Record<ScreenEntry['status'], { color: string; label: string; icon: string }> = {
  captured: { color: 'orange', label: '已截屏', icon: '●' },
  parsing: { color: 'blue', label: '解析中', icon: '↻' },
  parsed: { color: 'green', label: '已解析', icon: '✓' },
  failed: { color: 'red', label: '失败', icon: '!' },
};

export default function ScreencapGrid({ batchId, screens, parsedOrders }: ScreencapGridProps) {
  if (screens.length === 0) {
    return (
      <div style={{ padding: '64px 32px', textAlign: 'center' }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <div style={{ color: 'rgba(0,0,0,0.45)' }}>
              <div style={{ marginBottom: 6 }}>还没有捕获任何屏幕</div>
              <div style={{ fontSize: 12 }}>
                点击左侧「截屏 (+1)」开始捕获
              </div>
            </div>
          }
        />
      </div>
    );
  }

  const parsingCount = screens.filter((s) => s.status === 'parsing').length;
  const firstParsingSeq = screens.find((s) => s.status === 'parsing')?.seq;

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
          已截屏 {screens.length} 张
        </h3>
        {parsingCount > 0 && firstParsingSeq !== undefined && (
          <span style={{ color: '#ff7a00', fontSize: 12 }}>
            <Spin size="small" style={{ marginRight: 6 }} />
            正在解析第 {firstParsingSeq} 张（共 {parsingCount} 张待解析）
          </span>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, 110px)',
          gap: 10,
          maxHeight: 560,
          overflowY: 'auto',
          padding: 4,
        }}
      >
        {screens.map((s) => {
          const badge = STATUS_BADGE[s.status];
          const imgUrl = `/api/auto-import/xianyu/screen?batch_id=${encodeURIComponent(batchId)}&seq=${s.seq}`;
          return (
            <div
              key={s.seq}
              style={{
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                background: '#fafafa',
                padding: 10,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <div
                style={{
                  position: 'relative',
                  width: '100%',
                  aspectRatio: '9 / 19.5',
                  background: '#000',
                  borderRadius: 4,
                  overflow: 'hidden',
                  cursor: 'zoom-in',
                }}
                onClick={() => window.open(imgUrl, '_blank')}
                title="点击查看大图"
              >
                <img
                  src={imgUrl}
                  alt={`截屏 #${s.seq}`}
                  style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
                />
                <span
                  style={{
                    position: 'absolute',
                    top: 4,
                    left: 6,
                    background: '#ff7a00',
                    color: '#fff',
                    width: 22,
                    height: 22,
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  {s.seq}
                </span>
              </div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  fontSize: 12,
                }}
              >
                <Tag color={badge.color} style={{ margin: 0 }}>
                  {badge.icon} {badge.label}
                </Tag>
                {s.error && (
                  <span
                    style={{ color: '#ff4d4f', fontSize: 11 }}
                    title={s.error}
                  >
                    {s.error.length > 14 ? `${s.error.slice(0, 14)}…` : s.error}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {parsedOrders.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <h3 style={{ margin: '0 0 10px', fontSize: 13, fontWeight: 600 }}>
            已解析订单预览 ({parsedOrders.length})
          </h3>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
              gap: 8,
            }}
          >
            {parsedOrders.slice(0, 30).map((raw, idx) => {
              const o = raw as ParsedOrderHint;
              const buyer = o.buyer_nickname ?? '(未知买家)';
              const firstTitle =
                o.products?.[0]?.listing_title ?? '(待解析)';
              const totalQty =
                o.products?.reduce((sum, p) => sum + (p.quantity ?? 0), 0) ?? 0;
              return (
                <div
                  key={o.external_order_id ?? idx}
                  style={{
                    border: '1px solid #f0f0f0',
                    borderRadius: 6,
                    padding: 10,
                    fontSize: 12,
                    background: '#fff',
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{buyer}</div>
                  <div
                    style={{
                      color: 'rgba(0,0,0,0.65)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                    title={firstTitle}
                  >
                    {firstTitle}
                  </div>
                  <div style={{ color: 'rgba(0,0,0,0.45)', marginTop: 4 }}>
                    数量: {totalQty}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
