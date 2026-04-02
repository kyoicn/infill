import { useEffect, useState } from 'react';
import { Card, Table, Button, Modal, Select, InputNumber, Space, Popconfirm, Tag, Tabs, message, Divider } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../api/client';

interface OrderDraft {
  items: { product_id: number | undefined; quantity: number | undefined }[];
}

export default function Orders() {
  const [orders, setOrders] = useState<any[]>([]);
  const [products, setProducts] = useState<any[]>([]);
  const [modal, setModal] = useState(false);
  const [tab, setTab] = useState('pending');
  const [drafts, setDrafts] = useState<OrderDraft[]>([]);

  const reload = () => {
    api.getOrders(tab === 'all' ? undefined : tab).then(setOrders);
    api.getProducts().then(setProducts);
  };

  useEffect(() => { reload(); }, [tab]);

  const openModal = () => {
    setDrafts([{ items: [{ product_id: undefined, quantity: undefined }] }]);
    setModal(true);
  };

  const updateItem = (oi: number, ii: number, field: string, value: any) => {
    const next = [...drafts];
    (next[oi].items[ii] as any)[field] = value;
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
    setDrafts([...drafts, { items: [{ product_id: undefined, quantity: undefined }] }]);
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
        await api.createOrder({ items: draft.items });
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

  const getProdName = (id: number) => products.find(p => p.id === id)?.name || `#${id}`;

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

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>订单管理</h2>

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
              render: (_: any, rec: any) =>
                rec.items?.map((item: any) => `${getProdName(item.product_id)} x${item.quantity}`).join(', '),
            },
            {
              title: '操作', width: 160,
              render: (_: any, rec: any) => (
                <Space>
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
          </div>
        ))}
        <Divider style={{ margin: '12px 0' }} />
        <Button type="dashed" onClick={addOrder} icon={<PlusOutlined />} block>
          再加一个订单
        </Button>
      </Modal>
    </div>
  );
}
