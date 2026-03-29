import { useEffect, useState } from 'react';
import { Card, Button, DatePicker, Table, Tag, Space, Popconfirm, Switch, message, Tabs, Descriptions } from 'antd';
import { api } from '../api/client';
import dayjs from 'dayjs';

export default function Schedule() {
  const [plans, setPlans] = useState<any[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<any>(null);
  const [date, setDate] = useState(dayjs().add(1, 'day'));
  const [surplusEnabled, setSurplusEnabled] = useState(true);
  const [printers, setPrinters] = useState<any[]>([]);
  const [components, setComponents] = useState<any[]>([]);
  const [configs, setConfigs] = useState<any[]>([]);
  const [viewMode, setViewMode] = useState('list');
  const [changeoverMin, setChangeoverMin] = useState(15);
  const [products, setProducts] = useState<any[]>([]);
  const [surplus, setSurplus] = useState<any[]>([]);

  const reload = () => {
    api.getPlans().then(setPlans);
    api.getPrinters().then(setPrinters);
    api.getComponents().then(setComponents);
    api.getProducts().then(setProducts);
    api.getSurplus().then(setSurplus);
    api.getSystemConfigs().then(cfgs => {
      const co = cfgs.find((c: any) => c.key === 'changeover_minutes');
      if (co) setChangeoverMin(Number(co.value));
    });
  };

  useEffect(() => { reload(); }, []);

  // 加载所有打印盘配置
  useEffect(() => {
    api.getAllConfigs().then(setConfigs);
  }, []);

  const generate = async () => {
    try {
      const plan = await api.generatePlan({ date: date.format('YYYY-MM-DD'), surplus_enabled: surplusEnabled });
      message.success('排班表已生成');
      reload();
      setSelectedPlan(plan);
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const confirmPlan = async (id: number) => {
    await api.confirmPlan(id);
    reload();
    if (selectedPlan?.id === id) {
      const updated = await api.getPlan(id);
      setSelectedPlan(updated);
    }
  };

  const deletePlan = async (id: number) => {
    const res = await api.deletePlan(id);
    if (res.deleted_dates?.length > 1) {
      message.info(`已删除 ${res.deleted_dates.length} 个排班：${res.deleted_dates.join('、')}`);
    }
    reload();
    setSelectedPlan(null);
  };

  const deleteTask = async (taskId: number) => {
    await api.deleteTask(taskId);
    if (selectedPlan) {
      const updated = await api.getPlan(selectedPlan.id);
      setSelectedPlan(updated);
    }
  };

  const deleteBatch = async (batchId: number) => {
    await api.deleteBatch(batchId);
    if (selectedPlan) {
      const updated = await api.getPlan(selectedPlan.id);
      setSelectedPlan(updated);
    }
  };

  const getConfigInfo = (configId: number) => {
    const cfg = configs.find(c => c.id === configId);
    if (!cfg) return `配置#${configId}`;
    const comp = components.find(c => c.id === cfg.component_id);
    return `${cfg.plate_name}（${comp?.name || '?'} x${cfg.quantity}, ${cfg.duration_minutes}分钟）`;
  };

  const getPrinterName = (printerId: number) => {
    return printers.find(p => p.id === printerId)?.name || `打印机#${printerId}`;
  };

  // 排班总结
  const renderSummary = () => {
    if (!selectedPlan?.batches?.length) return null;

    // 1. 统计本次排班各组件产出
    const production: Record<number, number> = {};
    for (const batch of selectedPlan.batches) {
      for (const task of batch.tasks) {
        const cfg = configs.find(c => c.id === task.print_config_id);
        if (cfg) {
          production[cfg.component_id] = (production[cfg.component_id] || 0) + cfg.quantity;
        }
      }
    }

    // 2. 获取当前库存和订单需求（来自 surplus 接口）
    const surplusMap: Record<number, { stock: number; demand: number }> = {};
    for (const s of surplus) {
      surplusMap[s.component_id] = { stock: s.stock, demand: s.demand };
    }

    // 3. 计算排班后各组件状态
    const compRows = components.map(comp => {
      const produced = production[comp.id] || 0;
      const stock = surplusMap[comp.id]?.stock || 0;
      const demand = surplusMap[comp.id]?.demand || 0;
      const afterPlan = stock + produced;
      const remaining = demand - afterPlan;
      return {
        id: comp.id,
        name: comp.name,
        produced,
        stock,
        afterPlan,
        demand,
        remaining: remaining > 0 ? remaining : 0,
      };
    }).filter(r => r.produced > 0 || r.demand > 0);

    // 4. 计算可生产多少个产品（排班后库存能组装的数量）
    const productCapacity = products.map(prod => {
      const bom = prod.bom_items || [];
      if (bom.length === 0) return null;
      const count = Math.min(...bom.map((b: any) => {
        const row = compRows.find(r => r.id === b.component_id);
        const available = row ? row.afterPlan : (surplusMap[b.component_id]?.stock || 0);
        return Math.floor(available / b.quantity);
      }));
      return { name: prod.name, count };
    }).filter(Boolean);

    return (
      <div style={{ marginBottom: 16 }}>
        <Table
          dataSource={compRows}
          rowKey="id"
          size="small"
          pagination={false}
          title={() => <strong>排班总结</strong>}
          columns={[
            { title: '组件', dataIndex: 'name' },
            { title: '当前库存', dataIndex: 'stock', width: 90 },
            { title: '本次生产', dataIndex: 'produced', width: 90,
              render: (v: number) => v > 0 ? <Tag color="blue">+{v}</Tag> : '-',
            },
            { title: '排班后库存', dataIndex: 'afterPlan', width: 100 },
            { title: '订单需求', dataIndex: 'demand', width: 90 },
            { title: '仍缺', dataIndex: 'remaining', width: 80,
              render: (v: number) => v > 0 ? <Tag color="red">-{v}</Tag> : <Tag color="green">充足</Tag>,
            },
          ]}
        />
        <div style={{ marginTop: 8 }}>
          <strong>排班后可组装：</strong>
          {productCapacity.map((p: any) => (
            <Tag key={p.name} color={p.count > 0 ? 'blue' : 'default'} style={{ marginRight: 8 }}>
              {p.name} x{p.count}
            </Tag>
          ))}
        </div>
      </div>
    );
  };

  // 甘特图简易实现
  const renderGantt = () => {
    if (!selectedPlan?.batches?.length) return <div style={{ color: '#999' }}>无排班数据</div>;

    // 计算时间范围
    let minTime = 24 * 60, maxTime = 0;
    for (const batch of selectedPlan.batches) {
      for (const task of batch.tasks) {
        const [sh, sm] = task.start_time.split(':').map(Number);
        const [eh, em] = task.end_time.split(':').map(Number);
        minTime = Math.min(minTime, sh * 60 + sm);
        maxTime = Math.max(maxTime, eh * 60 + em);
      }
    }
    const totalMin = maxTime - minTime || 1;

    return (
      <div style={{ overflowX: 'auto' }}>
        {/* 时间轴标尺 */}
        <div style={{ display: 'flex', marginLeft: 100, marginBottom: 4, position: 'relative', height: 20 }}>
          {Array.from({ length: Math.ceil(totalMin / 60) + 1 }, (_, i) => {
            const hour = Math.floor(minTime / 60) + i;
            return (
              <div key={i} style={{ position: 'absolute', left: `${((i * 60) / totalMin) * 100}%`, fontSize: 12, color: '#999' }}>
                {hour}:00
              </div>
            );
          })}
        </div>
        {printers.map(printer => {
          const tasks = selectedPlan.batches.flatMap((b: any) =>
            b.tasks.filter((t: any) => t.printer_id === printer.id)
          );
          return (
            <div key={printer.id} style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
              <div style={{ width: 100, fontWeight: 500, flexShrink: 0 }}>{printer.name}</div>
              <div style={{ flex: 1, position: 'relative', height: 36, background: '#f5f5f5', borderRadius: 4 }}>
                {tasks.map((task: any) => {
                  const [sh, sm] = task.start_time.split(':').map(Number);
                  const [eh, em] = task.end_time.split(':').map(Number);
                  const start = sh * 60 + sm - minTime;
                  const dur = eh * 60 + em - (sh * 60 + sm);
                  const left = (start / totalMin) * 100;
                  const width = (dur / totalMin) * 100;
                  return (
                    <div
                      key={task.id}
                      title={`${getConfigInfo(task.print_config_id)}\n${task.start_time} - ${task.end_time}`}
                      style={{
                        position: 'absolute',
                        left: `${left}%`,
                        width: `${width}%`,
                        top: 2,
                        bottom: 2,
                        background: '#1677ff',
                        borderRadius: 4,
                        color: '#fff',
                        fontSize: 11,
                        padding: '0 4px',
                        overflow: 'hidden',
                        whiteSpace: 'nowrap',
                        cursor: 'pointer',
                      }}
                    >
                      {getConfigInfo(task.print_config_id)}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  // 列表视图
  const renderList = () => {
    if (!selectedPlan?.batches?.length) return <div style={{ color: '#999' }}>无排班数据</div>;

    return selectedPlan.batches.map((batch: any) => {
      // 收菜时间 = 启动时间 - 换版时间（即你需要去打印机旁的时刻）
      const [sh, sm] = batch.start_time.split(':').map(Number);
      const goMin = sh * 60 + sm - changeoverMin;
      const goTime = batch.batch_order === 0
        ? null  // 第一批没有上一轮要收
        : `${String(Math.floor(goMin / 60)).padStart(2, '0')}:${String(goMin % 60).padStart(2, '0')}`;
      const title = goTime
        ? `批次 ${batch.batch_order + 1} — ${goTime} 收菜，${batch.start_time} 启动`
        : `批次 ${batch.batch_order + 1} — ${batch.start_time} 启动（首批）`;
      return (
      <Card
        key={batch.id}
        size="small"
        title={title}
        extra={
          <Popconfirm title="删除此批次？" onConfirm={() => deleteBatch(batch.id)}>
            <Button size="small" danger>删除批次</Button>
          </Popconfirm>
        }
        style={{ marginBottom: 12 }}
      >
        <Table
          dataSource={batch.tasks}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '打印机', dataIndex: 'printer_id', render: (v: number) => getPrinterName(v) },
            { title: '打印内容', dataIndex: 'print_config_id', render: (v: number) => getConfigInfo(v) },
            { title: '开始', dataIndex: 'start_time' },
            { title: '结束', dataIndex: 'end_time' },
            {
              title: '操作',
              render: (_: any, rec: any) => (
                <Popconfirm title="删除此任务？" onConfirm={() => deleteTask(rec.id)}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>
    )});
  };

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>排班中心</h2>

      {/* 生成排班 */}
      <Card style={{ marginBottom: 24 }}>
        <Space>
          <DatePicker value={date} onChange={v => v && setDate(v)} />
          <span>富余生产：</span>
          <Switch checked={surplusEnabled} onChange={setSurplusEnabled} />
          <Button type="primary" onClick={generate}>生成排班表</Button>
        </Space>
      </Card>

      {/* 排班历史 */}
      <Card title="排班表列表" style={{ marginBottom: 24 }}>
        <Table
          dataSource={plans}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 10 }}
          onRow={(rec) => ({ onClick: () => api.getPlan(rec.id).then(setSelectedPlan), style: { cursor: 'pointer' } })}
          columns={[
            { title: '日期', dataIndex: 'date' },
            {
              title: '状态', dataIndex: 'status',
              render: (v: string) => <Tag color={v === 'draft' ? 'orange' : 'green'}>{v === 'draft' ? '草稿' : '已确认'}</Tag>,
            },
            { title: '批次数', render: (_: any, rec: any) => rec.batches?.length ?? '-' },
            {
              title: '操作',
              render: (_: any, rec: any) => (
                <Space>
                  {rec.status === 'draft' && (
                    <Popconfirm title="确认排班？" onConfirm={() => confirmPlan(rec.id)}>
                      <Button size="small" type="primary">确认</Button>
                    </Popconfirm>
                  )}
                  <Popconfirm
                    title="删除排班？"
                    description={(() => {
                      const laterCount = plans.filter(p => p.date >= rec.date && p.id !== rec.id).length;
                      return laterCount > 0 ? `将同时删除之后的 ${laterCount} 个排班` : undefined;
                    })()}
                    onConfirm={() => deletePlan(rec.id)}
                  >
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 排班详情 */}
      {selectedPlan && (
        <Card title={`排班详情 — ${selectedPlan.date}`}>
          {renderSummary()}
          <Tabs
            activeKey={viewMode}
            onChange={setViewMode}
            items={[
              { key: 'gantt', label: '甘特图', children: renderGantt() },
              { key: 'list', label: '列表视图', children: renderList() },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
