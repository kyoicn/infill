import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Image,
  InputNumber,
  Modal,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import SkuPicker from './SkuPicker';
import type {
  AutoImportCommitItem,
  AutoImportPreviewItem,
  AutoImportScanResponse,
} from '../../api/client';

const { Text } = Typography;

interface RowProduct {
  listing_title: string;
  quantity: number;
  matched_sku_code: string | null;
  confidence: number;
  reasoning: string;
}

interface RowState {
  item: AutoImportPreviewItem;
  checked: boolean;
  overrideDuplicate: boolean;
  products: RowProduct[];
  /** cache last picked name per sku_code for nicer display when api only returned code */
  pickedNames: Record<string, string>;
}

interface PreviewTableProps {
  batch: AutoImportScanResponse;
  sourcePlatform: 'xhs' | 'xianyu';
  onCommit: (items: AutoImportCommitItem[]) => void;
  onCancel: () => void;
}

type FilterKey = 'all' | 'new' | 'dup' | 'fix';

const MIN_AUTOCHECK_CONFIDENCE = 0.55;

function deriveInitialChecked(item: AutoImportPreviewItem): boolean {
  if (item.is_duplicate) return false;
  if (item.products.length === 0) return false;
  for (const p of item.products) {
    if (!p.matched_sku_code) return false;
    if (p.confidence < MIN_AUTOCHECK_CONFIDENCE) return false;
  }
  return true;
}

function rowMinConfidence(products: RowProduct[]): number {
  if (products.length === 0) return 0;
  return Math.min(...products.map((p) => p.confidence));
}

function rowAllMatched(products: RowProduct[]): boolean {
  return products.length > 0 && products.every((p) => !!p.matched_sku_code);
}

function confTier(c: number): 'high' | 'mid' | 'low' {
  if (c >= 0.85) return 'high';
  if (c >= 0.55) return 'mid';
  return 'low';
}

function platformTag(platform: 'xhs' | 'xianyu') {
  if (platform === 'xhs') {
    return (
      <Tag color="#fff1f3" style={{ color: '#ff2442', border: 'none', margin: 0 }}>
        小红书
      </Tag>
    );
  }
  return (
    <Tag color="#fff5e6" style={{ color: '#ff7a00', border: 'none', margin: 0 }}>
      闲鱼
    </Tag>
  );
}

export default function PreviewTable(props: PreviewTableProps) {
  const { batch, sourcePlatform, onCommit, onCancel } = props;

  const [filter, setFilter] = useState<FilterKey>('all');
  const [rows, setRows] = useState<RowState[]>(() =>
    batch.items.map((item) => ({
      item,
      checked: deriveInitialChecked(item),
      overrideDuplicate: false,
      products: item.products.map((p) => ({
        listing_title: p.listing_title,
        quantity: p.quantity,
        matched_sku_code: p.matched_sku_code ?? null,
        confidence: p.confidence,
        reasoning: p.reasoning,
      })),
      pickedNames: {},
    })),
  );

  // ---------- mutations ----------

  function updateRow(idx: number, mut: (r: RowState) => RowState) {
    setRows((prev) => prev.map((r, i) => (i === idx ? mut(r) : r)));
  }

  function toggleChecked(idx: number, checked: boolean) {
    updateRow(idx, (r) => ({ ...r, checked }));
  }

  function setProductSku(idx: number, pIdx: number, sku: string, name: string) {
    updateRow(idx, (r) => {
      const newProducts = r.products.map((p, i) =>
        i === pIdx
          ? { ...p, matched_sku_code: sku, confidence: Math.max(p.confidence, 1.0) }
          : p,
      );
      return {
        ...r,
        products: newProducts,
        pickedNames: { ...r.pickedNames, [sku]: name },
      };
    });
  }

  function setProductQty(idx: number, pIdx: number, qty: number) {
    updateRow(idx, (r) => ({
      ...r,
      products: r.products.map((p, i) => (i === pIdx ? { ...p, quantity: qty } : p)),
    }));
  }

  function removeProduct(idx: number, pIdx: number) {
    updateRow(idx, (r) => ({
      ...r,
      products: r.products.filter((_, i) => i !== pIdx),
    }));
  }

  function addProduct(idx: number, sku: string, name: string) {
    updateRow(idx, (r) => ({
      ...r,
      products: [
        ...r.products,
        {
          listing_title: '（手动添加）',
          quantity: 1,
          matched_sku_code: sku,
          confidence: 1.0,
          reasoning: 'manual',
        },
      ],
      pickedNames: { ...r.pickedNames, [sku]: name },
    }));
  }

  function overrideDup(idx: number) {
    Modal.confirm({
      title: '确定要把此单当作新单导入？',
      content: '系统检测到该单已存在；改判后将作为新订单写入，可能产生重复。',
      okText: '确定改判',
      cancelText: '取消',
      onOk: () =>
        updateRow(idx, (r) => ({
          ...r,
          overrideDuplicate: true,
          checked:
            r.products.length > 0 && rowAllMatched(r.products) ? true : r.checked,
        })),
    });
  }

  // ---------- filter / counts ----------

  const counts = useMemo(() => {
    let dup = 0;
    let fix = 0;
    let neu = 0;
    for (const r of rows) {
      if (r.item.is_duplicate && !r.overrideDuplicate) {
        dup += 1;
      } else if (!rowAllMatched(r.products) || rowMinConfidence(r.products) < MIN_AUTOCHECK_CONFIDENCE) {
        fix += 1;
      } else {
        neu += 1;
      }
    }
    return { all: rows.length, new: neu, dup, fix };
  }, [rows]);

  const visibleIndexes = useMemo(() => {
    return rows
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => {
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        const isFix =
          !isDup &&
          (!rowAllMatched(r.products) ||
            rowMinConfidence(r.products) < MIN_AUTOCHECK_CONFIDENCE);
        if (filter === 'all') return true;
        if (filter === 'dup') return isDup;
        if (filter === 'fix') return isFix;
        if (filter === 'new') return !isDup && !isFix;
        return true;
      })
      .map(({ i }) => i);
  }, [rows, filter]);

  // ---------- footer summary ----------

  const summary = useMemo(() => {
    let orderCount = 0;
    let totalQty = 0;
    for (const r of rows) {
      if (!r.checked) continue;
      orderCount += 1;
      for (const p of r.products) totalQty += Math.max(0, p.quantity);
    }
    return { orderCount, totalQty };
  }, [rows]);

  // ---------- bulk actions ----------

  function bulkSelectNew() {
    setRows((prev) =>
      prev.map((r) => {
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        const min = rowMinConfidence(r.products);
        const ok = !isDup && rowAllMatched(r.products) && min >= MIN_AUTOCHECK_CONFIDENCE;
        return ok ? { ...r, checked: true } : r;
      }),
    );
  }
  function bulkNone() {
    setRows((prev) => prev.map((r) => ({ ...r, checked: false })));
  }
  function bulkInvert() {
    setRows((prev) =>
      prev.map((r) => {
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        const min = rowMinConfidence(r.products);
        const low = min < MIN_AUTOCHECK_CONFIDENCE || !rowAllMatched(r.products);
        if (isDup || low) return r;
        return { ...r, checked: !r.checked };
      }),
    );
  }

  // ---------- submit ----------

  function handleCommit() {
    const items: AutoImportCommitItem[] = [];
    for (const r of rows) {
      if (!r.checked) continue;
      const products = r.products
        .filter((p) => p.matched_sku_code && p.quantity > 0)
        .map((p) => ({ sku: p.matched_sku_code as string, quantity: p.quantity }));
      if (products.length === 0) continue;
      items.push({
        external_order_id: r.item.external_order_id,
        buyer_nickname: r.item.buyer_nickname,
        external_created_at: r.item.external_created_at ?? null,
        platform: sourcePlatform,
        override_duplicate: r.overrideDuplicate || undefined,
        products,
      });
    }
    onCommit(items);
  }

  // ---------- table render ----------

  const columns: TableColumnsType<{ idx: number }> = [
    {
      title: (
        <Checkbox
          indeterminate={summary.orderCount > 0 && summary.orderCount < rows.length}
          checked={summary.orderCount === rows.length && rows.length > 0}
          onChange={(e) => {
            if (e.target.checked) bulkSelectNew();
            else bulkNone();
          }}
        />
      ),
      key: 'check',
      width: 48,
      render: (_, rec) => {
        const r = rows[rec.idx];
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        const min = rowMinConfidence(r.products);
        const low = !rowAllMatched(r.products) || min < MIN_AUTOCHECK_CONFIDENCE;
        const disabled = isDup || low;
        const cb = (
          <Checkbox
            checked={r.checked}
            disabled={disabled}
            onChange={(e) => toggleChecked(rec.idx, e.target.checked)}
          />
        );
        if (disabled) {
          const tip = isDup
            ? '重复单 — 点「改判为新单」后才可勾选'
            : !rowAllMatched(r.products)
              ? '有商品未匹配 SKU — 请先在右侧选定 SKU'
              : '置信度过低 — 请先核对';
          return <Tooltip title={tip}>{cb}</Tooltip>;
        }
        return cb;
      },
    },
    {
      title: '平台',
      key: 'platform',
      width: 80,
      render: () => platformTag(sourcePlatform),
    },
    {
      title: '外部订单号',
      key: 'oid',
      width: 160,
      render: (_, rec) => {
        const r = rows[rec.idx];
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        return (
          <div>
            <div
              style={{
                fontFamily: 'monospace',
                fontSize: 11.5,
                color: 'rgba(0,0,0,0.65)',
                textDecoration: isDup ? 'line-through' : 'none',
              }}
            >
              {r.item.external_order_id}
            </div>
            {r.item.is_duplicate && (
              <div style={{ marginTop: 4 }}>
                <Tag
                  color="default"
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    letterSpacing: 0.5,
                    padding: '0 6px',
                    lineHeight: '16px',
                  }}
                >
                  {r.overrideDuplicate ? '已改判' : '重复'}
                </Tag>
                {!r.overrideDuplicate && (
                  <div
                    style={{
                      color: 'rgba(0,0,0,0.45)',
                      fontSize: 11,
                      marginTop: 4,
                      lineHeight: 1.5,
                    }}
                  >
                    {r.item.existing_order_id != null && (
                      <div>已导入 · order #{r.item.existing_order_id}</div>
                    )}
                    <a onClick={() => overrideDup(rec.idx)}>改判为新单 →</a>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      },
    },
    {
      title: '买家 / 下单',
      key: 'buyer',
      width: 180,
      render: (_, rec) => {
        const r = rows[rec.idx];
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        return (
          <div>
            <div
              style={{
                fontWeight: 500,
                textDecoration: isDup ? 'line-through' : 'none',
                color: 'rgba(0,0,0,0.88)',
              }}
            >
              {r.item.buyer_nickname}
            </div>
            {r.item.external_created_at && (
              <div
                style={{
                  color: 'rgba(0,0,0,0.45)',
                  fontSize: 11,
                  marginTop: 2,
                  fontFamily: 'monospace',
                }}
              >
                {r.item.external_created_at}
              </div>
            )}
          </div>
        );
      },
    },
    {
      title: '商品（点 SKU 框可换品 · 件数可改）',
      key: 'products',
      render: (_, rec) => {
        const r = rows[rec.idx];
        const isDup = r.item.is_duplicate && !r.overrideDuplicate;
        const rawTitle = r.products[0]?.listing_title ?? '';
        return (
          <div style={{ minWidth: 360 }}>
            <div
              style={{
                color: 'rgba(0,0,0,0.65)',
                fontSize: 12.5,
                lineHeight: 1.4,
                marginBottom: 8,
                textDecoration: isDup ? 'line-through' : 'none',
              }}
            >
              <Text type="secondary" style={{ fontFamily: 'monospace' }}>
                "
              </Text>
              {rawTitle}
              <Text type="secondary" style={{ fontFamily: 'monospace' }}>
                "
              </Text>
              {r.products.length > 1 && (
                <Tag
                  color="#e6f4ff"
                  style={{
                    color: '#1677ff',
                    border: 'none',
                    marginLeft: 6,
                    fontSize: 10,
                  }}
                >
                  {r.products.length} 件商品
                </Tag>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {r.products.map((p, pIdx) => {
                const tier = confTier(p.confidence);
                const name =
                  p.matched_sku_code && r.pickedNames[p.matched_sku_code]
                    ? r.pickedNames[p.matched_sku_code]
                    : p.matched_sku_code
                      ? p.matched_sku_code
                      : null;
                return (
                  <div
                    key={pIdx}
                    style={{ display: 'flex', alignItems: 'center', gap: 8 }}
                  >
                    <span
                      style={{
                        color: 'rgba(0,0,0,0.25)',
                        fontSize: 11,
                        width: 12,
                        textAlign: 'center',
                      }}
                    >
                      ·
                    </span>
                    <SkuPicker
                      currentSku={p.matched_sku_code}
                      currentName={name}
                      confidence={p.confidence}
                      rawTitle={p.listing_title}
                      onChange={(sku, n) => setProductSku(rec.idx, pIdx, sku, n)}
                    />
                    <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>×</span>
                    <InputNumber
                      min={1}
                      value={p.quantity}
                      disabled={isDup || tier === 'low' || !p.matched_sku_code}
                      onChange={(v) =>
                        setProductQty(rec.idx, pIdx, typeof v === 'number' ? v : 1)
                      }
                      style={{ width: 64 }}
                      controls={false}
                    />
                    {!isDup && (
                      <Tooltip title="删除此商品">
                        <a
                          onClick={() => removeProduct(rec.idx, pIdx)}
                          style={{
                            color: 'rgba(0,0,0,0.45)',
                            padding: '2px 6px',
                            fontSize: 13,
                          }}
                        >
                          ✕
                        </a>
                      </Tooltip>
                    )}
                  </div>
                );
              })}
              {!isDup && (
                <div>
                  <SkuPicker
                    currentSku={null}
                    currentName={null}
                    confidence={0}
                    rawTitle={rawTitle}
                    onChange={(sku, n) => addProduct(rec.idx, sku, n)}
                  />
                  <span
                    style={{ marginLeft: 8, color: 'rgba(0,0,0,0.45)', fontSize: 12 }}
                  >
                    + 添加商品
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      },
    },
  ];

  const dataSource = visibleIndexes.map((i) => ({ idx: i, key: i }));

  const rowClassName = (rec: { idx: number }) => {
    const r = rows[rec.idx];
    if (r.item.is_duplicate && !r.overrideDuplicate) return 'auto-import-row-dup';
    const min = rowMinConfidence(r.products);
    if (!rowAllMatched(r.products) || min < MIN_AUTOCHECK_CONFIDENCE)
      return 'auto-import-row-low';
    if (min < 0.85) return 'auto-import-row-mid';
    return '';
  };

  const minConf = rows.length
    ? Math.min(
        ...rows.map((r) => (r.products.length ? rowMinConfidence(r.products) : 1)),
      )
    : 0;
  const avgConf = rows.length
    ? rows.reduce((acc, r) => {
        if (!r.products.length) return acc;
        const sum = r.products.reduce((s, p) => s + p.confidence, 0);
        return acc + sum / r.products.length;
      }, 0) / rows.length
    : 0;

  return (
    <div>
      <style>{`
        .auto-import-row-dup td { background: #f0f0f0 !important; color: rgba(0,0,0,0.25); }
        .auto-import-row-mid td { background: #fffbe6 !important; }
        .auto-import-row-low td { background: #fff1f0 !important; }
      `}</style>

      <div style={{ marginBottom: 4 }}>
        <Text type="secondary" style={{ fontSize: 13 }}>
          订单管理 · 自动导入 · 预览批次{' '}
          <code style={{ background: '#fafafa', padding: '1px 6px', borderRadius: 3 }}>
            #{batch.batch_id.slice(0, 8)}
          </code>
        </Text>
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          marginBottom: 4,
        }}
      >
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
          预览导入 — {rows.length} 单
        </h1>
        <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 13 }}>
          {platformTag(sourcePlatform)}
          <span style={{ marginLeft: 8 }}>
            扫描批次 · 共 {batch.stats.total} 单 · 丢弃 {batch.stats.dropped_count}
          </span>
        </span>
      </div>
      <div style={{ color: 'rgba(0,0,0,0.65)', fontSize: 13, marginBottom: 16 }}>
        点 SKU 框可换品 · 点件数可改数量 · 一单可有多商品 ·
        重复单和未匹配单默认不勾选。
      </div>

      {sourcePlatform === 'xianyu' && (batch.screen_count ?? 0) > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
            源截屏（{batch.screen_count} 张，点击查看大图）
          </div>
          <Image.PreviewGroup>
            <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 6 }}>
              {Array.from({ length: batch.screen_count ?? 0 }, (_, i) => (
                <div
                  key={i}
                  style={{
                    flex: '0 0 auto',
                    width: 120,
                    aspectRatio: '9 / 19.5',
                    background: '#000',
                    borderRadius: 4,
                    overflow: 'hidden',
                    position: 'relative',
                  }}
                >
                  <Image
                    src={`/api/auto-import/xianyu/screen?batch_id=${encodeURIComponent(batch.batch_id)}&seq=${i}`}
                    alt={`截屏 #${i}`}
                    width="100%"
                    height="100%"
                    style={{ objectFit: 'contain', display: 'block' }}
                    preview={{ mask: '🔍' }}
                  />
                  <span
                    style={{
                      position: 'absolute',
                      top: 4,
                      left: 6,
                      background: '#ff7a00',
                      color: '#fff',
                      width: 20,
                      height: 20,
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 11,
                      fontWeight: 700,
                      pointerEvents: 'none',
                    }}
                  >
                    {i}
                  </span>
                </div>
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}

      {batch.dropped.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 20 }}
          message={`LLM 解析出 ${batch.dropped.length + rows.length} 条记录，其中 ${batch.dropped.length} 条因必填字段缺失被丢弃`}
          description={
            <div>
              <div style={{ fontSize: 12, color: 'rgba(0,0,0,0.65)', marginBottom: 8 }}>
                以下记录将不会进入预览。检查上面截屏，确认是 LLM 漏读还是截图本身没有这些字段。
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {batch.dropped.map((d, idx) => (
                  <div
                    key={idx}
                    style={{
                      background: '#fff',
                      border: '1px solid #ffe7ba',
                      borderRadius: 4,
                      padding: '8px 10px',
                      fontSize: 12,
                    }}
                  >
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                      <Tag color="orange" style={{ margin: 0 }}>
                        丢弃 #{idx + 1}
                      </Tag>
                      {(d.missing_fields || []).map((f) => (
                        <Tag key={f} color="red" style={{ margin: 0 }}>
                          缺 {f}
                        </Tag>
                      ))}
                    </div>
                    <div style={{ marginTop: 6, color: 'rgba(0,0,0,0.65)' }}>
                      <div>外部订单号: <Text code>{d.external_order_id || '(空)'}</Text></div>
                      <div>买家: <Text code>{d.buyer_nickname || '(空)'}</Text></div>
                      <div>下单时间: <Text code>{d.external_created_at || '(空)'}</Text></div>
                      <div>
                        商品: {' '}
                        {(d.products && d.products.length > 0)
                          ? d.products.map((p, i) => (
                              <Text key={i} code style={{ marginRight: 6 }}>
                                {p.listing_title || '?'} ×{p.quantity ?? 1}
                              </Text>
                            ))
                          : <Text code>(空)</Text>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          }
        />
      )}

      {rows.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 64, color: 'var(--text3, rgba(0,0,0,0.45))' }}>
          <div style={{ fontSize: 16, marginBottom: 12 }}>
            {batch.dropped.length > 0 ? '所有 LLM 解析结果都被丢弃' : '未抓取到任何订单'}
          </div>
          <div style={{ fontSize: 13, marginBottom: 24 }}>
            {batch.dropped.length > 0
              ? '可参考上面源截屏 + 丢弃明细，确定下一步：重新截屏 / 直接手工录入'
              : '请检查千帆 tab 是否打开，或闲鱼是否截取到订单页'}
          </div>
          <Button onClick={onCancel}>返回扫描页</Button>
        </div>
      ) : (
        <>
          <div
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              marginBottom: 16,
              flexWrap: 'wrap',
            }}
          >
            <FilterChip
              active={filter === 'all'}
              label="全部"
              count={counts.all}
              onClick={() => setFilter('all')}
            />
            <FilterChip
              active={filter === 'new'}
              label="● 新单"
              count={counts.new}
              color="#52c41a"
              onClick={() => setFilter('new')}
            />
            <FilterChip
              active={filter === 'dup'}
              label="○ 重复"
              count={counts.dup}
              color="rgba(0,0,0,0.45)"
              onClick={() => setFilter('dup')}
            />
            <FilterChip
              active={filter === 'fix'}
              label="! 未匹配"
              count={counts.fix}
              color="#ff4d4f"
              onClick={() => setFilter('fix')}
            />
            <span
              style={{
                marginLeft: 'auto',
                fontSize: 12,
                color: 'rgba(0,0,0,0.45)',
              }}
            >
              平均置信度{' '}
              <code style={{ background: '#fafafa', padding: '1px 6px', borderRadius: 3 }}>
                {avgConf.toFixed(2)}
              </code>{' '}
              · 最低{' '}
              <code style={{ background: '#fafafa', padding: '1px 6px', borderRadius: 3 }}>
                {minConf.toFixed(2)}
              </code>
            </span>
          </div>

          <Table
            columns={columns}
            dataSource={dataSource}
            rowClassName={rowClassName}
            pagination={false}
            size="middle"
            bordered={false}
          />

          <div
            style={{
              background: '#fff',
              border: '1px solid #f0f0f0',
              borderRadius: 8,
              padding: '16px 20px',
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              position: 'sticky',
              bottom: 16,
              marginTop: 16,
              boxShadow: '0 -4px 12px rgba(0,0,0,0.04)',
            }}
          >
            <div style={{ color: 'rgba(0,0,0,0.65)', fontSize: 13 }}>
              将导入{' '}
              <b style={{ color: 'rgba(0,0,0,0.88)', fontSize: 16 }}>{summary.orderCount}</b>{' '}
              单 · 共{' '}
              <b style={{ color: 'rgba(0,0,0,0.88)', fontSize: 16 }}>{summary.totalQty}</b>{' '}
              件商品
            </div>
            <span
              style={{ marginLeft: 'auto', color: 'rgba(0,0,0,0.45)', fontSize: 12 }}
            >
              <a onClick={bulkSelectNew} style={{ marginLeft: 8 }}>
                全选新单
              </a>
              <a onClick={bulkNone} style={{ marginLeft: 8 }}>
                全不选
              </a>
              <a onClick={bulkInvert} style={{ marginLeft: 8 }}>
                反选
              </a>
            </span>
            <Button onClick={onCancel}>取消，丢弃本批</Button>
            <Button
              type="primary"
              disabled={summary.orderCount === 0}
              onClick={handleCommit}
            >
              导入勾选的 {summary.orderCount} 单
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function FilterChip({
  active,
  label,
  count,
  color,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  color?: string;
  onClick: () => void;
}) {
  return (
    <span
      onClick={onClick}
      style={{
        background: active ? '#1677ff' : '#fff',
        border: `1px solid ${active ? '#1677ff' : '#f0f0f0'}`,
        padding: '6px 12px',
        borderRadius: 100,
        fontSize: 13,
        cursor: 'pointer',
        color: active ? '#fff' : color ?? 'rgba(0,0,0,0.65)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontWeight: active ? 500 : 400,
      }}
    >
      <span>{label}</span>
      <span style={{ fontWeight: 600 }}>{count}</span>
    </span>
  );
}
