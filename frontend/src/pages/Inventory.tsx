import { useEffect, useMemo, useState } from 'react';
import { Card, Table, Button, InputNumber, Tag, message } from 'antd';
import { EditOutlined, CheckOutlined, CloseOutlined, MinusOutlined, PlusOutlined } from '@ant-design/icons';
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

  // 直接管理排序状态：点同一列翻转方向；点新列默认 ascend
  const toggleSort = (key: string) =>
    setSortState(prev =>
      prev && prev.column === key
        ? { column: key, order: prev.order === 'ascend' ? 'descend' : 'ascend' }
        : { column: key, order: 'ascend' },
    );
  const headerCell = (key: string) => () => ({ onClick: () => toggleSort(key) });

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

  // 排序后的稳定顺序：仅在 sortState 或行集合（id）变化时重算；
  // 库存数值变化（+/- 调整、编辑保存）不会触发重排序，避免行跳动
  const idsKey = rows.map(r => r.id).join(',');
  const orderIds = useMemo(() => {
    const arr = [...rows];
    if (sortState) {
      const { column, order } = sortState;
      arr.sort((a, b) => {
        let cmp = 0;
        if (column === 'component_name') cmp = (a.component_name || '').localeCompare(b.component_name || '');
        else if (column === 'color') cmp = (a.color || '').localeCompare(b.color || '');
        else if (column === 'stock') cmp = a.stock - b.stock;
        else if (column === 'demand') cmp = a.demand - b.demand;
        else if (column === 'surplus') cmp = (a.stock - a.demand) - (b.stock - b.demand);
        return order === 'descend' ? -cmp : cmp;
      });
    }
    return arr.map(r => r.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortState, idsKey]);

  const byId = new Map(rows.map(r => [r.id, r]));
  const displayRows = orderIds.map(id => byId.get(id)).filter(Boolean) as typeof rows;

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

  const adjust = async (rec: any, delta: number) => {
    if (delta < 0 && rec.stock <= 0) return;
    try {
      await api.adjustInventory({ component_id: rec.component_id, color: rec.color, quantity: delta });
      reload();
    } catch (e: any) {
      message.error(e.message);
    }
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
          dataSource={displayRows}
          rowKey="id"
          size="small"
          pagination={false}
          sortDirections={['ascend', 'descend']}
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
              sorter: true,
              onHeaderCell: headerCell('component_name'),
            },
            {
              title: '颜色',
              key: 'color',
              dataIndex: 'color',
              width: 80,
              sortOrder: sortOrderFor('color'),
              sorter: true,
              onHeaderCell: headerCell('color'),
              render: (v: string) => v || '-',
            },
            {
              title: '当前库存',
              key: 'stock',
              dataIndex: 'stock',
              width: 140,
              sortOrder: sortOrderFor('stock'),
              sorter: true,
              onHeaderCell: headerCell('stock'),
              render: (v: number, rec: any) =>
                editing ? (
                  <InputNumber
                    min={0}
                    value={editValues[rec.id]}
                    onChange={val => setEditValues({ ...editValues, [rec.id]: val ?? 0 })}
                    size="small"
                    style={{ width: 100 }}
                  />
                ) : (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <Button
                      size="small"
                      icon={<MinusOutlined />}
                      disabled={v <= 0}
                      onClick={() => adjust(rec, -1)}
                    />
                    <span style={{ minWidth: 24, textAlign: 'center', display: 'inline-block' }}>{v}</span>
                    <Button
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={() => adjust(rec, 1)}
                    />
                  </span>
                ),
            },
            {
              title: '订单需求',
              key: 'demand',
              dataIndex: 'demand',
              width: 100,
              sortOrder: sortOrderFor('demand'),
              sorter: true,
              onHeaderCell: headerCell('demand'),
            },
            {
              title: '富余',
              key: 'surplus',
              width: 100,
              sortOrder: sortOrderFor('surplus'),
              sorter: true,
              onHeaderCell: headerCell('surplus'),
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
