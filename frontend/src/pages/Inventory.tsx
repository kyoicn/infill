import { useEffect, useState } from 'react';
import { Card, Table, Button, InputNumber, Tag, message } from 'antd';
import { EditOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { api } from '../api/client';

export default function Inventory() {
  const [inventory, setInventory] = useState<any[]>([]);
  const [surplus, setSurplus] = useState<any[]>([]);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<number, number>>({});

  const reload = () => {
    api.getInventory().then(setInventory);
    api.getSurplus().then(setSurplus);
  };

  useEffect(() => { reload(); }, []);

  // 合并库存和富余数据：以 inventory 为主，补上 surplus 的 demand 信息
  const rows = inventory.map(inv => {
    const s = surplus.find(s => s.component_id === inv.component_id && s.color === (inv.color || ''));
    return {
      id: inv.id,
      component_id: inv.component_id,
      component_name: s?.component_name || `组件#${inv.component_id}`,
      color: inv.color || '',
      stock: inv.quantity,
      demand: s?.demand || 0,
    };
  });

  const startEdit = () => {
    const values: Record<number, number> = {};
    for (const r of rows) {
      values[r.id] = r.stock;
    }
    setEditValues(values);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditValues({});
  };

  const saveAll = async () => {
    try {
      const promises = rows
        .filter(r => editValues[r.id] !== r.stock)
        .map(r => api.setInventory(r.id, { component_id: r.component_id, color: r.color, quantity: editValues[r.id] }));
      await Promise.all(promises);
      setEditing(false);
      reload();
      message.success('库存已更新');
    } catch (e: any) {
      message.error(e.message);
    }
  };

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>库存管理</h2>

      <Card
        extra={
          editing ? (
            <span>
              <Button icon={<CheckOutlined />} type="primary" onClick={saveAll} style={{ marginRight: 8 }}>保存</Button>
              <Button icon={<CloseOutlined />} onClick={cancelEdit}>取消</Button>
            </span>
          ) : (
            <Button icon={<EditOutlined />} onClick={startEdit}>编辑库存</Button>
          )
        }
      >
        <Table
          dataSource={rows}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '组件', dataIndex: 'component_name' },
            { title: '颜色', dataIndex: 'color', width: 80,
              render: (v: string) => v || '-',
            },
            {
              title: '当前库存',
              dataIndex: 'stock',
              width: 140,
              render: (v: number, rec: any) =>
                editing ? (
                  <InputNumber
                    min={0}
                    value={editValues[rec.id]}
                    onChange={val => setEditValues({ ...editValues, [rec.id]: val ?? 0 })}
                    size="small"
                    style={{ width: 100 }}
                  />
                ) : v,
            },
            { title: '订单需求', dataIndex: 'demand', width: 100 },
            {
              title: '富余',
              width: 100,
              render: (_: any, rec: any) => {
                const stock = editing ? (editValues[rec.id] ?? rec.stock) : rec.stock;
                const val = stock - rec.demand;
                return <Tag color={val >= 0 ? 'green' : 'red'}>{val >= 0 ? `+${val}` : val}</Tag>;
              },
            },
          ]}
        />
      </Card>
    </div>
  );
}
