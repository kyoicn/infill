import { useEffect, useState } from 'react';
import { Card, Table, Button, Modal, Input, InputNumber, Space, Popconfirm, TimePicker, message } from 'antd';
import { PlusOutlined, DeleteOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { api } from '../api/client';
import dayjs from 'dayjs';

const DAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export default function Settings() {
  const [printers, setPrinters] = useState<any[]>([]);
  const [scheduleConfigs, setScheduleConfigs] = useState<any[]>([]);
  const [changeover, setChangeover] = useState('15');
  const [printerModal, setPrinterModal] = useState(false);
  const [windowModal, setWindowModal] = useState(false);
  const [editingDay, setEditingDay] = useState<number>(0);
  const [windows, setWindows] = useState<{ start: string; end: string }[]>([]);


  const reload = () => {
    api.getPrinters().then(setPrinters);
    api.getScheduleConfigs().then(setScheduleConfigs);
    api.getSystemConfigs().then(configs => {
      const co = configs.find((c: any) => c.key === 'changeover_minutes');
      if (co) setChangeover(co.value);
    });
  };

  useEffect(() => { reload(); }, []);

  const [printerNames, setPrinterNames] = useState<string[]>(['']);

  // 打印机
  const savePrinters = async () => {
    const names = printerNames.map(n => n.trim()).filter(Boolean);
    if (names.length === 0) { message.warning('请至少输入一个名称'); return; }
    for (const name of names) {
      await api.createPrinter({ name });
    }
    setPrinterModal(false);
    setPrinterNames(['']);
    message.success(`已添加 ${names.length} 台打印机`);
    reload();
  };
  const deletePrinter = async (id: number) => {
    await api.deletePrinter(id);
    reload();
  };

  // 换版时间
  const saveChangeover = async () => {
    await api.upsertSystemConfig('changeover_minutes', { key: 'changeover_minutes', value: changeover });
    message.success('已保存');
  };

  // 时间窗口
  const openWindowModal = (dow: number) => {
    setEditingDay(dow);
    const existing = scheduleConfigs.find(c => c.day_of_week === dow);
    setWindows(existing?.windows || [{ start: '08:00', end: '12:00' }, { start: '12:30', end: '18:00' }, { start: '18:30', end: '23:00' }]);
    setWindowModal(true);
  };

  const saveWindows = async () => {
    await api.upsertScheduleConfig(editingDay, { day_of_week: editingDay, windows });
    setWindowModal(false);
    reload();
    message.success('已保存');
  };

  const updateWindow = (idx: number, field: 'start' | 'end', value: string) => {
    const updated = [...windows];
    updated[idx] = { ...updated[idx], [field]: value };
    setWindows(updated);
  };

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>系统设置</h2>

      {/* 打印机 */}
      <Card
        title="打印机管理"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setPrinterNames(['']); setPrinterModal(true); }}>新增打印机</Button>}
        style={{ marginBottom: 24 }}
      >
        <Table
          dataSource={printers}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 60 },
            { title: '名称', dataIndex: 'name' },
            {
              title: '操作', width: 80,
              render: (_: any, rec: any) => (
                <Popconfirm title="确定删除？" onConfirm={() => deletePrinter(rec.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>

      {/* 换版时间 */}
      <Card title="换版时间（分钟）" style={{ marginBottom: 24 }}>
        <Space>
          <InputNumber value={Number(changeover)} min={0} onChange={v => setChangeover(String(v ?? 15))} />
          <Button type="primary" onClick={saveChangeover}>保存</Button>
        </Space>
      </Card>

      {/* 操作时间窗口 */}
      <Card title="操作时间窗口" style={{ marginBottom: 24 }}>
        <Table
          dataSource={Array.from({ length: 7 }, (_, i) => {
            const cfg = scheduleConfigs.find(c => c.day_of_week === i);
            return { day: i, windows: cfg?.windows || [] };
          })}
          rowKey="day"
          size="small"
          pagination={false}
          columns={[
            { title: '星期', dataIndex: 'day', render: (v: number) => DAY_NAMES[v], width: 80 },
            {
              title: '时间窗口',
              render: (_: any, rec: any) =>
                rec.windows.length > 0
                  ? rec.windows.map((w: any) => `${w.start}-${w.end}`).join('，')
                  : <span style={{ color: '#999' }}>未配置（使用默认）</span>,
            },
            {
              title: '操作', width: 80,
              render: (_: any, rec: any) => (
                <Button size="small" onClick={() => openWindowModal(rec.day)}>编辑</Button>
              ),
            },
          ]}
        />
      </Card>

      {/* 打印机弹窗 */}
      <Modal title="新增打印机" open={printerModal} onOk={savePrinters} onCancel={() => setPrinterModal(false)}>
        {printerNames.map((name, i) => (
          <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
            <Input
              placeholder={`如：${i + 1}号机`}
              value={name}
              onChange={e => {
                const updated = [...printerNames];
                updated[i] = e.target.value;
                setPrinterNames(updated);
              }}
              style={{ width: 200 }}
            />
            {printerNames.length > 1 && (
              <Button danger icon={<DeleteOutlined />} onClick={() => setPrinterNames(printerNames.filter((_, j) => j !== i))} />
            )}
          </Space>
        ))}
        <Button type="dashed" icon={<PlusOutlined />} onClick={() => setPrinterNames([...printerNames, ''])} block>
          再加一台
        </Button>
      </Modal>

      {/* 重置数据库 */}
      <Card title="数据库维护" style={{ marginBottom: 24 }}>
        <Space direction="vertical">
          <span style={{ color: '#666' }}>
            重置数据库会删除所有排班数据并重建表结构。库存、订单、打印机和系统配置会保留，产品目录从 YAML 重新加载。
          </span>
          <Button
            danger
            onClick={() => {
              Modal.confirm({
                title: '确定要重置数据库吗？',
                icon: <ExclamationCircleOutlined />,
                content: '排班数据将被清除，库存和订单会保留。此操作不可撤销。',
                okText: '确定重置',
                okType: 'danger',
                onOk: async () => {
                  try {
                    const res = await api.resetDatabase();
                    message.success(`重置完成，已恢复 ${res.restored.inventory} 条库存、${res.restored.orders} 条订单、${res.restored.printers} 台打印机`);
                    reload();
                  } catch (e: any) {
                    message.error(e.message || '重置失败');
                  }
                },
              });
            }}
          >
            重置数据库
          </Button>
        </Space>
      </Card>

      {/* 时间窗口弹窗 */}
      <Modal title={`编辑时间窗口 — ${DAY_NAMES[editingDay]}`} open={windowModal} onOk={saveWindows} onCancel={() => setWindowModal(false)} width={500}>
        {windows.map((w, i) => (
          <Space key={i} style={{ display: 'flex', marginBottom: 8 }}>
            <TimePicker
              format="HH:mm"
              value={dayjs(w.start, 'HH:mm')}
              onChange={v => v && updateWindow(i, 'start', v.format('HH:mm'))}
            />
            <span>至</span>
            <TimePicker
              format="HH:mm"
              value={dayjs(w.end, 'HH:mm')}
              onChange={v => v && updateWindow(i, 'end', v.format('HH:mm'))}
            />
            <Button danger icon={<DeleteOutlined />} onClick={() => setWindows(windows.filter((_, j) => j !== i))} />
          </Space>
        ))}
        <Button type="dashed" icon={<PlusOutlined />} onClick={() => setWindows([...windows, { start: '08:00', end: '12:00' }])} block>
          添加时间段
        </Button>
      </Modal>
    </div>
  );
}
