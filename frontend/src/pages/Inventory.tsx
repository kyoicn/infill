import { useEffect, useState } from 'react';
import { Card, Table, Button, InputNumber, Tag, message } from 'antd';
import { EditOutlined, CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { api } from '../api/client';

type SortOrder = 'ascend' | 'descend';
type SortState = { column: string; order: SortOrder } | null;
const SORT_STORAGE_KEY = 'infill.inventory.sort';

function loadSortState(): SortState {
  try {
    const raw = localStorage.getItem(SORT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.column && (parsed.order === 'ascend' || parsed.order === 'descend')) return parsed;
    return null;
  } catch {
    return null;
  }
}

export default function Inventory() {
  const [inventory, setInventory] = useState<any[]>([]);
  const [surplus, setSurplus] = useState<any[]>([]);
  const [editing, setEditing] = useState(false);
  const [editValues, setEditValues] = useState<Record<number, number>>({});
  const [sortState, setSortState] = useState<SortState>(loadSortState);

  const reload = () => {
    api.getInventory().then(setInventory);
    api.getSurplus().then(setSurplus);
  };

  useEffect(() => { reload(); }, []);

  useEffect(() => {
    if (sortState) localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(sortState));
    else localStorage.removeItem(SORT_STORAGE_KEY);
  }, [sortState]);

  const sortOrderFor = (key: string): SortOrder | null =>
    sortState?.column === key ? sortState.order : null;

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
    <div style={{ maxWidth: 1280, margin: '0 auto' }}>
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
          sortDirections={['ascend', 'descend']}
          onChange={(_p, _f, sorter: any) => {
            // 砍掉 3 态后 sorter.order 总有值；用户点 column 切换升降序，状态写 localStorage
            if (sorter && !Array.isArray(sorter) && sorter.order && sorter.columnKey) {
              setSortState({ column: String(sorter.columnKey), order: sorter.order });
            }
          }}
          rowClassName={(rec: any) => {
            const stock = editing ? (editValues[rec.id] ?? rec.stock) : rec.stock;
            const val = stock - rec.demand;
            if (val < 0) return 'inv-row-deficit';
            if (val > 0) return 'inv-row-surplus';
            return '';
          }}
          columns={[
            {
              title: '组件',
              key: 'component_name',
              dataIndex: 'component_name',
              sortOrder: sortOrderFor('component_name'),
              sorter: (a: any, b: any) => (a.component_name || '').localeCompare(b.component_name || ''),
            },
            {
              title: '颜色',
              key: 'color',
              dataIndex: 'color',
              width: 80,
              sortOrder: sortOrderFor('color'),
              sorter: (a: any, b: any) => (a.color || '').localeCompare(b.color || ''),
              render: (v: string) => v || '-',
            },
            {
              title: '当前库存',
              key: 'stock',
              dataIndex: 'stock',
              width: 140,
              sortOrder: sortOrderFor('stock'),
              sorter: (a: any, b: any) => a.stock - b.stock,
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
            {
              title: '订单需求',
              key: 'demand',
              dataIndex: 'demand',
              width: 100,
              sortOrder: sortOrderFor('demand'),
              sorter: (a: any, b: any) => a.demand - b.demand,
            },
            {
              title: '富余',
              key: 'surplus',
              width: 100,
              sortOrder: sortOrderFor('surplus'),
              sorter: (a: any, b: any) => {
                const sa = editing ? (editValues[a.id] ?? a.stock) : a.stock;
                const sb = editing ? (editValues[b.id] ?? b.stock) : b.stock;
                return (sa - a.demand) - (sb - b.demand);
              },
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
