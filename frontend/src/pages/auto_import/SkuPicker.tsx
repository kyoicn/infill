import { useEffect, useMemo, useState } from 'react';
import { Input, Popover, Spin, Typography } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { AutoImportSkuSearchHit } from '../../api/client';

const { Text } = Typography;

interface SkuPickerProps {
  currentSku: string | null;
  currentName: string | null;
  confidence: number;
  rawTitle: string;
  onChange: (sku: string, name: string) => void;
}

function confTier(confidence: number): 'high' | 'mid' | 'low' {
  if (confidence >= 0.85) return 'high';
  if (confidence >= 0.55) return 'mid';
  return 'low';
}

function ConfIcon({ confidence }: { confidence: number }) {
  const tier = confTier(confidence);
  const bg = tier === 'high' ? '#52c41a' : tier === 'mid' ? '#faad14' : '#ff4d4f';
  const sym = tier === 'high' ? '✓' : tier === 'mid' ? '?' : '!';
  return (
    <span
      style={{
        width: 16,
        height: 16,
        borderRadius: '50%',
        background: bg,
        color: '#fff',
        fontSize: 11,
        fontWeight: 700,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      {sym}
    </span>
  );
}

export default function SkuPicker(props: SkuPickerProps) {
  const { currentSku, currentName, confidence, rawTitle, onChange } = props;
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<AutoImportSkuSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [recommended, setRecommended] = useState<AutoImportSkuSearchHit[]>([]);

  const defaultSeed = useMemo(() => {
    if (currentName) return currentName.split(/[\s\-_]/)[0] ?? '';
    return rawTitle.split(/[\s,，。·]/)[0] ?? '';
  }, [currentName, rawTitle]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      if (!defaultSeed.trim()) {
        setRecommended([]);
        return;
      }
      try {
        const res = await api.autoImport.skuSearch(defaultSeed.trim(), 3);
        if (cancelled) return;
        if (res.ok) setRecommended(res.hits);
      } catch {
        if (cancelled) return;
        setRecommended([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, defaultSeed]);

  useEffect(() => {
    if (!open) return;
    if (!query.trim()) {
      setHits([]);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api.autoImport.skuSearch(query.trim(), 10);
        if (cancelled) return;
        if (res.ok) setHits(res.hits);
        else setHits([]);
      } catch {
        if (cancelled) return;
        setHits([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, open]);

  function pick(hit: AutoImportSkuSearchHit) {
    onChange(hit.sku_code, hit.sku_name);
    setOpen(false);
    setQuery('');
  }

  const tier = confTier(confidence);
  const borderColor =
    tier === 'low' ? '#ff4d4f' : tier === 'mid' ? '#faad14' : '#d9d9d9';

  const triggerStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '5px 10px',
    border: `1px solid ${borderColor}`,
    borderRadius: 4,
    cursor: 'pointer',
    background: '#fff',
    maxWidth: 280,
  };

  const content = (
    <div style={{ width: 340 }}>
      {/* 1. Current + raw title */}
      <div
        style={{
          background: '#fafafa',
          padding: '8px 10px',
          borderRadius: 4,
          marginBottom: 8,
          fontFamily: 'monospace',
          fontSize: 12,
          color: 'rgba(0,0,0,0.65)',
        }}
      >
        <div style={{ marginBottom: 4 }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            当前匹配
          </Text>
          <div style={{ color: 'rgba(0,0,0,0.88)' }}>
            {currentName ? `${currentName} (${currentSku ?? '—'})` : '未匹配'}
          </div>
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            原始标题
          </Text>
          <div style={{ wordBreak: 'break-all' }}>{rawTitle}</div>
        </div>
      </div>

      {/* 2. LLM recommended */}
      {recommended.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div
            style={{
              fontSize: 11,
              color: 'rgba(0,0,0,0.45)',
              marginBottom: 4,
              padding: '0 4px',
            }}
          >
            推荐
          </div>
          {recommended.slice(0, 3).map((hit) => (
            <div
              key={`rec-${hit.sku_code}`}
              onClick={() => pick(hit)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 10px',
                borderRadius: 4,
                cursor: 'pointer',
                fontSize: 13,
                background: hit.sku_code === currentSku ? '#f6ffed' : 'transparent',
                border:
                  hit.sku_code === currentSku ? '1px solid #b7eb8f' : '1px solid transparent',
              }}
              onMouseEnter={(e) => {
                if (hit.sku_code !== currentSku)
                  (e.currentTarget as HTMLDivElement).style.background = '#e6f4ff';
              }}
              onMouseLeave={(e) => {
                if (hit.sku_code !== currentSku)
                  (e.currentTarget as HTMLDivElement).style.background = 'transparent';
              }}
            >
              <span style={{ flex: 1, color: 'rgba(0,0,0,0.88)' }}>{hit.sku_name}</span>
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 11,
                  background: '#fafafa',
                  padding: '1px 5px',
                  borderRadius: 3,
                  color: 'rgba(0,0,0,0.65)',
                }}
              >
                {hit.sku_code}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 3. Search */}
      <Input
        prefix={<SearchOutlined style={{ color: 'rgba(0,0,0,0.45)' }} />}
        placeholder="搜索 SKU 编号或名称"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        allowClear
        style={{ marginBottom: 6 }}
      />
      <div style={{ maxHeight: 220, overflowY: 'auto' }}>
        {loading && (
          <div style={{ textAlign: 'center', padding: 12 }}>
            <Spin size="small" />
          </div>
        )}
        {!loading && query.trim() && hits.length === 0 && (
          <div
            style={{
              padding: 12,
              textAlign: 'center',
              color: 'rgba(0,0,0,0.45)',
              fontSize: 12,
            }}
          >
            无匹配 SKU
          </div>
        )}
        {!loading &&
          hits.map((hit) => (
            <div
              key={hit.sku_code}
              onClick={() => pick(hit)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 10px',
                borderRadius: 4,
                cursor: 'pointer',
                fontSize: 13,
              }}
              onMouseEnter={(e) =>
                ((e.currentTarget as HTMLDivElement).style.background = '#e6f4ff')
              }
              onMouseLeave={(e) =>
                ((e.currentTarget as HTMLDivElement).style.background = 'transparent')
              }
            >
              <span style={{ flex: 1, color: 'rgba(0,0,0,0.88)' }}>{hit.sku_name}</span>
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 11,
                  background: '#fafafa',
                  padding: '1px 5px',
                  borderRadius: 3,
                  color: 'rgba(0,0,0,0.65)',
                }}
              >
                {hit.sku_code}
              </span>
            </div>
          ))}
      </div>

      {/* 4. Footer link */}
      <div
        style={{
          borderTop: '1px solid #f0f0f0',
          marginTop: 6,
          paddingTop: 8,
          textAlign: 'center',
        }}
      >
        <a
          onClick={() => {
            setOpen(false);
            navigate('/intake');
          }}
          style={{ fontSize: 12 }}
        >
          找不到对应 SKU？请先到产品录入 →
        </a>
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomLeft"
      destroyOnHidden
    >
      <span style={triggerStyle}>
        <ConfIcon confidence={confidence} />
        {currentName ? (
          <>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'rgba(0,0,0,0.88)' }}>
              {currentName}
            </span>
            {currentSku && (
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 11,
                  background: 'rgba(0,0,0,0.04)',
                  padding: '1px 5px',
                  borderRadius: 3,
                  color: 'rgba(0,0,0,0.45)',
                }}
              >
                {currentSku}
              </span>
            )}
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: 'rgba(0,0,0,0.45)' }}>
              {confidence.toFixed(2)}
            </span>
          </>
        ) : (
          <span style={{ fontSize: 13, color: '#ff4d4f', fontStyle: 'italic' }}>
            未匹配 — 点此选 SKU
          </span>
        )}
        <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 11, marginLeft: 2 }}>▾</span>
      </span>
    </Popover>
  );
}
