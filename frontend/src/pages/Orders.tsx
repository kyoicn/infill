import { useEffect, useState } from 'react';
import { Card, Table, Button, Modal, Select, InputNumber, Space, Popconfirm, Tag, Tabs, message, Divider, Input, Tooltip, Typography } from 'antd';
import { PlusOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

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
      <span>
        {items.map((item: any, idx: number) => {
          const missing = isProdMissing(item.product_id);
          const text = `${getProdName(item.product_id)} x${item.quantity}`;
          const sep = idx < items.length - 1 ? ', ' : '';
          return (
            <span key={idx}>
              {missing
                ? <Typography.Text type="danger">{text}</Typography.Text>
                : <span>{text}</span>}
              {sep}
            </span>
          );
        })}
      </span>
    );
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ marginTop: 0 }}>订单管理</h2>
        <Button type="primary" onClick={() => navigate('/orders/import')}>
          自动导入 →
        </Button>
      </div>

      <Card
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={openModal}>新增订单</Button>}
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

        <Table
          dataSource={orders}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 20 }}
          columns={[
            { title: '订单号', dataIndex: 'id', width: 80 },
            {
              title: '创建时间', dataIndex: 'created_at', width: 180,
              render: (v: string) => new Date(v).toLocaleString('zh-CN'),
            },
            {
              title: '状态', dataIndex: 'status', width: 100,
              render: (v: string) => <Tag color={v === 'pending' ? 'orange' : 'green'}>{v === 'pending' ? '待处理' : '已发货'}</Tag>,
            },
            {
              title: '产品明细',
              render: (_: any, rec: any) => renderItems(rec),
            },
            {
              title: '备注',
              dataIndex: 'notes',
              width: 160,
              ellipsis: { showTitle: false } as any,
              render: (v: string) => v
                ? <Tooltip title={v}><span style={{ color: '#666' }}>{v}</span></Tooltip>
                : <span style={{ color: '#ccc' }}>-</span>,
            },
            {
              title: '操作', width: 180,
              render: (_: any, rec: any) => (
                <Space>
                  <Tooltip title="编辑订单">
                    <Button size="small" icon={<EditOutlined />} onClick={() => openEditModal(rec)} />
                  </Tooltip>
                  {rec.status === 'pending' && (
                    <Popconfirm title="确认发货？库存将自动扣减。" onConfirm={() => shipOrder(rec.id)}>
                      <Button size="small" type="primary">发货</Button>
                    </Popconfirm>
                  )}
                  <Popconfirm title="确定删除？" onConfirm={() => deleteOrder(rec.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
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
