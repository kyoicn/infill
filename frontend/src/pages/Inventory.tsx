import { useEffect, useState } from 'react';
import { Card, Table, Button, InputNumber, Tag, message } from 'antd';
import { EditOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { api } from '../api/client';

export default function Inventory() {
  const [surplus, setSurplus] = useState<any[]>([]);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<number, number>>({});

  const reload = () => {
    api.getSurplus().then(setSurplus);
  };

  useEffect(() => { reload(); }, []);

  const startEdit = () => {
    const values: Record<number, number> = {};
    for (const s of surplus) {
      values[s.component_id] = s.stock;
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
      const promises = surplus
        .filter(s => editValues[s.component_id] !== s.stock)
        .map(s => api.setInventory(s.component_id, editValues[s.component_id]));
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
          dataSource={surplus}
          rowKey="component_id"
          size="small"
          pagination={false}
          columns={[
            { title: '组件', dataIndex: 'component_name' },
            {
              title: '当前库存',
              dataIndex: 'stock',
              width: 140,
              render: (v: number, rec: any) =>
                editing ? (
                  <InputNumber
                    min={0}
                    value={editValues[rec.component_id]}
                    onChange={val => setEditValues({ ...editValues, [rec.component_id]: val ?? 0 })}
                    size="small"
                    style={{ width: 100 }}
                  />
                ) : v,
            },
            { title: '订单需求', dataIndex: 'demand', width: 100 },
            {
              title: '富余',
              dataIndex: 'surplus',
              width: 100,
              render: (_: number, rec: any) => {
                const stock = editing ? (editValues[rec.component_id] ?? rec.stock) : rec.stock;
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
