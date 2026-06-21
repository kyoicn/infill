import { useEffect, useMemo, useState } from 'react';
import { Card, Table, Button, Modal, Select, InputNumber, Space, Popconfirm, Tag, Tabs, message, Divider, Input, Tooltip, Typography } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined, SearchOutlined, LeftOutlined, DownOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import { api } from '../api/client';
import ShippingBlock from '../components/ShippingBlock';

interface OrderDraft {
  items: { product_id: number | undefined; quantity: number | undefined }[];
  notes: string;
}

interface EditState {
  id: number;
  created_at: string;
  shipped_at: string | null;
  status: string;
  items: { product_id: number | undefined; quantity: number | undefined }[];
  notes: string;
}

export default function Orders() {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<any[]>([]);
  const [products, setProducts] = useState<any[]>([]);
  const [modal, setModal] = useState(false);
  const [tab, setTab] = useState('pending');
  const [query, setQuery] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<number[]>([]);

  // 下单日期排序状态持久化。antd 原生 3 态：'ascend' | 'descend' | null
  // （null = 用户点了 3 次"取消排序"，按服务端返回顺序展示）
  type SortOrder = 'ascend' | 'descend' | null;
  const SORT_LS_KEY = 'infill.orders.sortOrder';
  const [sortOrder, setSortOrder] = useState<SortOrder>(() => {
    try {
      const v = window.localStorage.getItem(SORT_LS_KEY);
      if (v === 'ascend' || v === 'descend' || v === 'null') {
        return v === 'null' ? null : v;
      }
    } catch { /* ignore */ }
    return 'ascend';  // 默认升序：老单在前
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(SORT_LS_KEY, sortOrder === null ? 'null' : sortOrder);
    } catch { /* ignore */ }
  }, [sortOrder]);

  // 列宽持久化（拖拽列头右边缘调整后落到 localStorage）
  const WIDTH_LS_KEY = 'infill.orders.colWidths';
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try {
      const v = window.localStorage.getItem(WIDTH_LS_KEY);
      if (v) return JSON.parse(v);
    } catch { /* ignore */ }
    return {};
  });
  useEffect(() => {
    try { window.localStorage.setItem(WIDTH_LS_KEY, JSON.stringify(widths)); } catch { /* ignore */ }
  }, [widths]);
  const handleResize = (key: string) => (_e: unknown, data: { size: { width: number } }) => {
    setWidths((prev) => ({ ...prev, [key]: Math.max(60, data.size.width) }));
  };
  const [drafts, setDrafts] = useState<OrderDraft[]>([]);
  const [editState, setEditState] = useState<EditState | null>(null);

  const reload = () => {
    api.getOrders(tab === 'all' ? undefined : tab).then(setOrders);
    api.getProducts().then(setProducts);
  };

  useEffect(() => { reload(); }, [tab]);

  const openModal = () => {
    setDrafts([{ items: [{ product_id: undefined, quantity: undefined }], notes: '' }]);
    setModal(true);
  };

  const updateItem = (oi: number, ii: number, field: string, value: any) => {
    const next = [...drafts];
    (next[oi].items[ii] as any)[field] = value;
    setDrafts(next);
  };

  const updateNotes = (oi: number, value: string) => {
    const next = [...drafts];
    next[oi].notes = value;
    setDrafts(next);
  };

  const addItem = (oi: number) => {
    const next = [...drafts];
    next[oi].items.push({ product_id: undefined, quantity: undefined });
    setDrafts(next);
  };

  const removeItem = (oi: number, ii: number) => {
    const next = [...drafts];
    next[oi].items.splice(ii, 1);
    if (next[oi].items.length === 0) next.splice(oi, 1);
    setDrafts(next);
  };

  const addOrder = () => {
    setDrafts([...drafts, { items: [{ product_id: undefined, quantity: undefined }], notes: '' }]);
  };

  const removeOrder = (oi: number) => {
    setDrafts(drafts.filter((_, i) => i !== oi));
  };

  const validDrafts = drafts.filter(d =>
    d.items.length > 0 && d.items.every(it => it.product_id != null && it.quantity != null && it.quantity > 0)
  );

  const submitAll = async () => {
    if (validDrafts.length === 0) {
      message.error('没有有效的订单');
      return;
    }
    try {
      for (const draft of validDrafts) {
        await api.createOrder({ items: draft.items, notes: draft.notes || '' });
      }
      message.success(`已创建 ${validDrafts.length} 个订单`);
      setModal(false);
      reload();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const shipOrder = async (id: number) => {
    try {
      await api.shipOrder(id);
      reload();
      message.success('订单已发货，库存已扣减');
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const deleteOrder = async (id: number) => {
    await api.deleteOrder(id);
    reload();
  };

  const openEditModal = (order: any) => {
    setEditState({
      id: order.id,
      created_at: order.created_at,
      shipped_at: order.shipped_at || null,
      status: order.status,
      items: (order.items || []).map((it: any) => ({ product_id: it.product_id, quantity: it.quantity })),
      notes: order.notes || '',
    });
  };

  const editUpdateItem = (ii: number, field: string, value: any) => {
    if (!editState) return;
    const next = { ...editState, items: [...editState.items] };
    (next.items[ii] as any)[field] = value;
    setEditState(next);
  };

  const editAddItem = () => {
    if (!editState) return;
    setEditState({ ...editState, items: [...editState.items, { product_id: undefined, quantity: undefined }] });
  };

  const editRemoveItem = (ii: number) => {
    if (!editState) return;
    const next = { ...editState, items: editState.items.filter((_, i) => i !== ii) };
    setEditState(next);
  };

  const editValid = !!editState
    && editState.items.length > 0
    && editState.items.every(it => it.product_id != null && it.quantity != null && it.quantity > 0);

  const submitEdit = async () => {
    if (!editState || !editValid) {
      message.error('请检查订单项：每行必须选择产品且数量大于 0');
      return;
    }
    try {
      await api.updateOrder(editState.id, {
        items: editState.items.map(it => ({ product_id: it.product_id, quantity: it.quantity })),
        notes: editState.notes || '',
      });
      message.success('订单已更新');
      setEditState(null);
      reload();
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const isProdMissing = (id: number) => !products.find(p => p.id === id);
  const getProdName = (id: number) => {
    const p = products.find(p => p.id === id);
    return p ? p.name : `(已删除产品 #${id})`;
  };

  // 汇总待处理订单的产品需求
  const pendingOrders = tab === 'pending' ? orders : orders.filter(o => o.status === 'pending');
  const demandSummary: Record<number, number> = {};
  for (const order of pendingOrders) {
    for (const item of (order.items || [])) {
      demandSummary[item.product_id] = (demandSummary[item.product_id] || 0) + item.quantity;
    }
  }
  const demandEntries = Object.entries(demandSummary).map(([pid, qty]) => ({
    name: getProdName(Number(pid)),
    quantity: qty,
  })).sort((a, b) => b.quantity - a.quantity);

  const renderItems = (rec: any) => {
    const items = rec.items || [];
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {items.map((item: any, idx: number) => {
          const missing = isProdMissing(item.product_id);
          const name = getProdName(item.product_id);
          return (
            <Tag
              key={idx}
              color={missing ? 'red' : 'blue'}
              style={{ margin: 0, fontSize: 12 }}
            >
              {name}
              <span style={{ color: missing ? '#a8071a' : '#1677ff', fontWeight: 600, marginLeft: 4 }}>
                ×{item.quantity}
              </span>
            </Tag>
          );
        })}
      </div>
    );
  };

  // 客户端搜索过滤：跨 买家 / 外部单号 / 收件人 / 电话 / 地址 / 备注 / 内部 id / 产品名 模糊匹配
  const filteredOrders = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return orders;
    return orders.filter((o) => {
      const productNames = (o.items || [])
        .map((it: any) => getProdName(it.product_id))
        .join(' ');
      const hay = [
        o.buyer_nickname,
        o.external_order_id,
        o.recipient_name,
        o.recipient_phone,
        o.recipient_address,
        o.notes,
        String(o.id),
        productNames,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [orders, query, products]);

  // 下单时间格式化：'2026-06-18T15:18:50' → '2026-06-18 15:18'
  const fmtExternal = (v: string) => v.replace('T', ' ').slice(0, 16);

  const renderExpanded = (rec: any) => {
    const items = rec.items || [];
    return (
      <div style={{ padding: '4px 12px', fontSize: 13, color: 'rgba(0,0,0,0.75)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 6, columnGap: 12 }}>
          <span style={{ color: 'rgba(0,0,0,0.45)' }}>内部编号</span>
          <span style={{ fontFamily: 'monospace' }}>#{rec.id}</span>

          <span style={{ color: 'rgba(0,0,0,0.45)' }}>系统创建</span>
          <span style={{ fontFamily: 'monospace' }}>{new Date(rec.created_at).toLocaleString('zh-CN')}</span>

          {rec.shipped_at && (
            <>
              <span style={{ color: 'rgba(0,0,0,0.45)' }}>发货时间</span>
              <span style={{ fontFamily: 'monospace' }}>{new Date(rec.shipped_at).toLocaleString('zh-CN')}</span>
            </>
          )}

          {rec.external_order_id && (
            <>
              <span style={{ color: 'rgba(0,0,0,0.45)' }}>外部单号</span>
              <span style={{ fontFamily: 'monospace' }}>{rec.external_order_id}</span>
            </>
          )}

          {(rec.recipient_address || rec.recipient_name || rec.recipient_phone) && (
            <>
              <span style={{ color: 'rgba(0,0,0,0.45)' }}>完整收货</span>
              <span>
                {rec.recipient_name} {rec.recipient_phone}
                {rec.recipient_address && (
                  <>
                    <br />
                    {rec.recipient_address}
                  </>
                )}
              </span>
            </>
          )}

          {rec.notes && (
            <>
              <span style={{ color: 'rgba(0,0,0,0.45)' }}>完整备注</span>
              <span style={{ whiteSpace: 'pre-wrap' }}>{rec.notes}</span>
            </>
          )}

          <span style={{ color: 'rgba(0,0,0,0.45)' }}>所有商品</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {items.map((it: any, idx: number) => {
              const missing = isProdMissing(it.product_id);
              return (
                <div key={idx}>
                  {missing
                    ? <Typography.Text type="danger">{getProdName(it.product_id)}</Typography.Text>
                    : <span>{getProdName(it.product_id)}</span>}
                  <span style={{ color: 'rgba(0,0,0,0.45)', marginLeft: 8 }}>
                    × {it.quantity}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>订单管理</h2>

      <Card
        extra={
          <Space>
            <Input
              allowClear
              placeholder="搜索 买家/单号/收件人/电话/地址/备注/产品名"
              prefix={<SearchOutlined style={{ color: 'rgba(0,0,0,0.35)' }} />}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: 280 }}
            />
            <Button onClick={() => navigate('/orders/import')}>自动导入 →</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openModal}>新增订单</Button>
          </Space>
        }
      >
        <Tabs activeKey={tab} onChange={setTab} items={[
          { key: 'pending', label: '待处理' },
          { key: 'shipped', label: '已发货' },
          { key: 'all', label: '全部' },
        ]} />

        {demandEntries.length > 0 && (
          <div style={{ marginBottom: 16, padding: '8px 12px', background: '#fafafa', borderRadius: 4 }}>
            <strong>待处理需求：</strong>
            {demandEntries.map(d => (
              <Tag key={d.name} color="blue" style={{ marginLeft: 4 }}>{d.name} x{d.quantity}</Tag>
            ))}
            <span style={{ marginLeft: 8, color: '#999' }}>共 {pendingOrders.length} 个订单</span>
          </div>
        )}

        <div style={{ marginBottom: 8, fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>
          {filteredOrders.length === orders.length
            ? <>共 <b style={{ color: 'rgba(0,0,0,0.65)' }}>{orders.length}</b> 单</>
            : <>过滤后 <b style={{ color: 'rgba(0,0,0,0.65)' }}>{filteredOrders.length}</b> / {orders.length} 单</>}
        </div>

        <Table
          dataSource={filteredOrders}
          rowKey="id"
          size="small"
          pagination={false}  // 不分页，全部展示；密集时用顶部搜索框过滤
          scroll={{ x: 'max-content' }}  // 强制 fixed layout，列宽才会被实际渲染
          expandable={{
            expandedRowRender: renderExpanded,
            expandedRowKeys: expandedKeys,
            showExpandColumn: false,  // 隐藏首列默认 + 图标，改成操作列里的自定义按钮
          }}
          onChange={(_p, _f, sorter) => {
            // antd 原生 3 态：ascend → descend → undefined → ascend ...
            // undefined 表示用户点了 3 次想取消排序；保留并存。
            if (Array.isArray(sorter)) return;
            if (sorter.columnKey === 'external_created_at' || sorter.field === 'external_created_at') {
              setSortOrder(sorter.order ?? null);
            } else if (!sorter.columnKey && !sorter.order) {
              // antd 第 3 次点击给了空对象，意思就是当前列回到无序
              setSortOrder(null);
            }
          }}
          components={{ header: { cell: ResizableTitle } }}
          columns={[
            {
              title: '来源 / 买家',
              key: 'source',
              width: widths.source ?? 220,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('source') }),
              render: (_: any, rec: any) => <SourceCell order={rec} />,
            },
            {
              title: '下单日期',
              dataIndex: 'external_created_at',
              key: 'external_created_at',
              width: widths.external_created_at ?? 160,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('external_created_at') }),
              sortOrder,  // 受控，从 localStorage 恢复
              sorter: (a: any, b: any) => {
                // 没下单时间（手工录入）的排到最后
                const va = a.external_created_at || '';
                const vb = b.external_created_at || '';
                if (!va && !vb) return 0;
                if (!va) return 1;
                if (!vb) return -1;
                return va.localeCompare(vb);
              },
              render: (v: string | null, rec: any) => {
                const sysCreated = `系统创建：${new Date(rec.created_at).toLocaleString('zh-CN')}`;
                if (!v) {
                  return (
                    <Tooltip title={sysCreated}>
                      <span style={{ color: '#ccc' }}>-</span>
                    </Tooltip>
                  );
                }
                return (
                  <Tooltip title={sysCreated}>
                    <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'rgba(0,0,0,0.75)' }}>
                      {fmtExternal(v)}
                    </span>
                  </Tooltip>
                );
              },
            },
            {
              title: '收货信息',
              key: 'shipping',
              width: widths.shipping ?? 240,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('shipping') }),
              render: (_: any, rec: any) => (
                <ShippingBlock
                  name={rec.recipient_name}
                  phone={rec.recipient_phone}
                  address={rec.recipient_address}
                />
              ),
            },
            ...(tab === 'all'
              ? [{
                  title: '状态',
                  dataIndex: 'status',
                  key: 'status',
                  width: widths.status ?? 80,
                  onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('status') }),
                  render: (v: string) => (
                    <Tag color={v === 'pending' ? 'orange' : 'green'}>
                      {v === 'pending' ? '待处理' : '已发货'}
                    </Tag>
                  ),
                }]
              : []),
            {
              title: '产品明细',
              key: 'products',
              width: widths.products ?? 360,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('products') }),
              render: (_: any, rec: any) => renderItems(rec),
            },
            {
              title: '备注',
              dataIndex: 'notes',
              key: 'notes',
              width: widths.notes ?? 100,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('notes') }),
              ellipsis: { showTitle: false } as any,
              render: (v: string) => v
                ? <Tooltip title={v}><span style={{ color: '#666' }}>{v}</span></Tooltip>
                : <span style={{ color: '#ccc' }}>-</span>,
            },
            {
              title: '操作',
              key: 'actions',
              width: widths.actions ?? 210,
              onHeaderCell: (col: any) => ({ width: col.width, onResize: handleResize('actions') }),
              render: (_: any, rec: any) => {
                const isExpanded = expandedKeys.includes(rec.id);
                return (
                  <Space>
                    <Tooltip title="编辑订单">
                      <Button size="small" icon={<EditOutlined />} onClick={() => openEditModal(rec)} />
                    </Tooltip>
                    {rec.status === 'pending' && (
                      <Popconfirm title="确认发货？库存将自动扣减。" onConfirm={() => shipOrder(rec.id)}>
                        <Button size="small" type="primary">发货</Button>
                      </Popconfirm>
                    )}
                    <Popconfirm title="删除此订单？此操作无法恢复" onConfirm={() => deleteOrder(rec.id)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                    <Tooltip title={isExpanded ? '收起详情' : '展开详情'}>
                      <Button
                        size="small"
                        icon={isExpanded ? <DownOutlined /> : <LeftOutlined />}
                        onClick={() =>
                          setExpandedKeys((prev) =>
                            prev.includes(rec.id)
                              ? prev.filter((k) => k !== rec.id)
                              : [...prev, rec.id],
                          )
                        }
                      />
                    </Tooltip>
                  </Space>
                );
              },
            },
          ]}
        />
      </Card>

      <Modal
        title="新增订单"
        open={modal}
        onOk={submitAll}
        onCancel={() => setModal(false)}
        okText={`创建 ${validDrafts.length} 个订单`}
        okButtonProps={{ disabled: validDrafts.length === 0 }}
        width={600}
        styles={{ body: { maxHeight: '60vh', overflowY: 'auto' } }}
      >
        {drafts.map((draft, oi) => (
          <div key={oi}>
            {oi > 0 && <Divider style={{ margin: '12px 0' }} />}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <strong>订单 {oi + 1}</strong>
              {drafts.length > 1 && (
                <Button size="small" danger onClick={() => removeOrder(oi)}>删除此订单</Button>
              )}
            </div>
            {draft.items.map((item, ii) => (
              <Space key={ii} style={{ display: 'flex', marginBottom: 8 }}>
                <Select
                  placeholder="选择产品"
                  style={{ width: 200 }}
                  value={item.product_id}
                  onChange={v => updateItem(oi, ii, 'product_id', v)}
                >
                  {products.map(p => (
                    <Select.Option key={p.id} value={p.id}>{p.name}</Select.Option>
                  ))}
                </Select>
                <InputNumber
                  min={1}
                  placeholder="数量"
                  value={item.quantity}
                  onChange={v => updateItem(oi, ii, 'quantity', v)}
                />
                <Button danger icon={<DeleteOutlined />} onClick={() => removeItem(oi, ii)} />
              </Space>
            ))}
            <Button size="small" type="dashed" onClick={() => addItem(oi)} icon={<PlusOutlined />}>
              添加产品
            </Button>
            <Input.TextArea
              placeholder="备注（可选）"
              autoSize={{ minRows: 1, maxRows: 3 }}
              value={draft.notes}
              onChange={e => updateNotes(oi, e.target.value)}
              style={{ marginTop: 8 }}
            />
          </div>
        ))}
        <Divider style={{ margin: '12px 0' }} />
        <Button type="dashed" onClick={addOrder} icon={<PlusOutlined />} block>
          再加一个订单
        </Button>
      </Modal>

      <Modal
        title={editState ? `编辑订单 #${editState.id}` : ''}
        open={!!editState}
        onOk={submitEdit}
        onCancel={() => setEditState(null)}
        okText="保存"
        okButtonProps={{ disabled: !editValid }}
        width={600}
        styles={{ body: { maxHeight: '60vh', overflowY: 'auto' } }}
      >
        {editState && (
          <div>
            <div style={{ marginBottom: 12, color: '#666', fontSize: 13 }}>
              <div>
                <span style={{ color: '#999' }}>创建时间（不可改）：</span>
                {new Date(editState.created_at).toLocaleString('zh-CN')}
              </div>
              {editState.shipped_at && (
                <div>
                  <span style={{ color: '#999' }}>发货时间（不可改）：</span>
                  {new Date(editState.shipped_at).toLocaleString('zh-CN')}
                </div>
              )}
              <div>
                <span style={{ color: '#999' }}>状态：</span>
                <Tag color={editState.status === 'pending' ? 'orange' : 'green'}>
                  {editState.status === 'pending' ? '待处理' : '已发货'}
                </Tag>
              </div>
            </div>
            <Divider style={{ margin: '12px 0' }} />
            <div style={{ marginBottom: 8 }}><strong>产品明细</strong></div>
            {editState.items.map((item, ii) => {
              const missing = item.product_id != null && isProdMissing(item.product_id);
              return (
                <Space key={ii} style={{ display: 'flex', marginBottom: 8 }}>
                  <Select
                    placeholder="选择产品"
                    style={{ width: 240 }}
                    value={item.product_id}
                    status={missing ? 'error' : undefined}
                    onChange={v => editUpdateItem(ii, 'product_id', v)}
                  >
                    {missing && item.product_id != null && (
                      <Select.Option key={`missing-${item.product_id}`} value={item.product_id} disabled>
                        (已删除产品 #{item.product_id})
                      </Select.Option>
                    )}
                    {products.map(p => (
                      <Select.Option key={p.id} value={p.id}>{p.name}</Select.Option>
                    ))}
                  </Select>
                  <InputNumber
                    min={1}
                    placeholder="数量"
                    value={item.quantity}
                    onChange={v => editUpdateItem(ii, 'quantity', v)}
                  />
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    disabled={editState.items.length <= 1}
                    onClick={() => editRemoveItem(ii)}
                  />
                </Space>
              );
            })}
            <Button size="small" type="dashed" onClick={editAddItem} icon={<PlusOutlined />}>
              添加产品
            </Button>
            <Divider style={{ margin: '12px 0' }} />
            <div style={{ marginBottom: 4 }}><strong>备注</strong></div>
            <Input.TextArea
              placeholder="备注（可选）"
              autoSize={{ minRows: 2, maxRows: 5 }}
              value={editState.notes}
              onChange={e => setEditState({ ...editState, notes: e.target.value })}
            />
          </div>
        )}
      </Modal>
    </div>
  );
}

// 列头 cell：包一层 Resizable，给右边缘加一条 col-resize 把手
function ResizableTitle(props: any) {
  const { onResize, width, ...rest } = props;
  if (typeof width !== 'number') return <th {...rest} />;
  return (
    <Resizable
      width={width}
      height={0}
      axis="x"
      handle={
        <span
          className="react-resizable-handle react-resizable-handle-e"
          onClick={(e) => e.stopPropagation()}  // 别冒泡到表头触发排序
          onMouseDown={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            right: -4,
            top: 0,
            bottom: 0,
            width: 8,
            cursor: 'col-resize',
            zIndex: 2,
            background: 'transparent',
            // 视觉：默认隐形，hover 出一根细蓝线提示可拖
            backgroundImage: 'none',
          }}
        />
      }
      onResize={onResize}
      draggableOpts={{ enableUserSelectHack: false }}
    >
      <th {...rest} style={{ ...rest.style, position: 'relative' }} />
    </Resizable>
  );
}

function SourceCell({ order }: { order: any }) {
  const { id, platform, buyer_nickname, external_order_id } = order;
  const idBadge = (
    <span
      style={{
        color: 'rgba(0,0,0,0.35)',
        fontSize: 10.5,
        fontFamily: 'monospace',
        marginLeft: 6,
      }}
    >
      #{id}
    </span>
  );
  if (!platform) {
    return (
      <span>
        <Tag color="default" style={{ margin: 0, fontSize: 11 }}>
          手工录入
        </Tag>
        {idBadge}
      </span>
    );
  }
  const isXianyu = platform === 'xianyu';
  return (
    <div style={{ fontSize: 12, lineHeight: 1.5 }}>
      <span>
        <Tag
          color={isXianyu ? '#fff5e6' : '#fff1f3'}
          style={{
            margin: 0,
            color: isXianyu ? '#ff7a00' : '#ff2442',
            border: 'none',
            fontWeight: 500,
          }}
        >
          {isXianyu ? '闲鱼' : '小红书'}
        </Tag>
        {idBadge}
      </span>
      {buyer_nickname && (
        <div style={{ marginTop: 4, fontWeight: 500, color: 'rgba(0,0,0,0.88)' }}>
          {buyer_nickname}
        </div>
      )}
      {external_order_id && (
        <Tooltip title={`外部单号：${external_order_id}`}>
          <div
            style={{
              fontFamily: 'monospace',
              fontSize: 11,
              color: 'rgba(0,0,0,0.45)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: 180,
            }}
          >
            {external_order_id}
          </div>
        </Tooltip>
      )}
    </div>
  );
}
