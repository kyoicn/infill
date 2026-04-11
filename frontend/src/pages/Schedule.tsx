import { useEffect, useState, useRef, useCallback } from 'react';
import { Card, Button, DatePicker, Table, Tag, Space, Popconfirm, Switch, message, Tabs, InputNumber, TimePicker, Select, Radio, Slider } from 'antd';
import { BellOutlined, CheckCircleOutlined, PlayCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, WarningOutlined } from '@ant-design/icons';
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
  const [startTime, setStartTime] = useState(dayjs('00:00', 'HH:mm'));
  const [durationHours, setDurationHours] = useState(24);
  const [viewMode, setViewMode] = useState('list');
  const [changeoverMin, setChangeoverMin] = useState(15);
  const [products, setProducts] = useState<any[]>([]);
  const [surplus, setSurplus] = useState<any[]>([]);
  const [strategy, setStrategy] = useState<string>('product_first');
  const [targetProductIds, setTargetProductIds] = useState<number[]>([]);
  const [syncStrength, setSyncStrength] = useState<number>(50);

  // 闹钟
  const [alarmTime, setAlarmTime] = useState<string | null>(null);
  const [alarmCountdown, setAlarmCountdown] = useState('');
  const [alarmMinutes, setAlarmMinutes] = useState<number | null>(null);
  const alarmTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const alarmAudioRef = useRef<HTMLAudioElement | null>(null);

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

  const refreshPlan = useCallback(async () => {
    if (selectedPlan) {
      const updated = await api.getPlan(selectedPlan.id);
      setSelectedPlan(updated);
    }
  }, [selectedPlan]);

  useEffect(() => { reload(); }, []);
  useEffect(() => { api.getAllConfigs().then(setConfigs); }, []);

  const generate = async () => {
    try {
      const plan = await api.generatePlan({
        date: date.format('YYYY-MM-DD'),
        surplus_enabled: surplusEnabled,
        start_time: startTime.format('HH:mm'),
        duration_hours: durationHours,
        strategy,
        target_product_ids: targetProductIds.length > 0 ? targetProductIds : null,
        sync_strength: syncStrength,
      });
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
    refreshPlan();
  };

  const deleteBatch = async (batchId: number) => {
    await api.deleteBatch(batchId);
    refreshPlan();
  };

  const startBatch = async (batchId: number) => {
    const now = new Date();
    const actualTime = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
    const res = await api.startBatch(batchId, actualTime);
    refreshPlan();
    const delta = res.delta_minutes;
    if (delta === 0) {
      message.success(`批次已开始（${actualTime}，与计划一致）`);
    } else {
      const sign = delta > 0 ? '晚' : '早';
      message.success(`批次已开始（${actualTime}，比计划${sign}了${Math.abs(delta)}分钟，后续批次已调整）`);
    }
  };

  const completeTask = async (taskId: number) => {
    try {
      const res = await api.completeTask(taskId);
      refreshPlan();
      reload();
      message.success(`任务已完成，库存 +${res.added_quantity}`);
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const cancelTask = async (taskId: number) => {
    try {
      await api.cancelTask(taskId);
      refreshPlan();
      message.info('任务已取消');
    } catch (e: any) {
      message.error(e.message);
    }
  };

  const failTask = async (taskId: number) => {
    try {
      await api.failTask(taskId);
      refreshPlan();
      message.warning('任务已标记为失败');
    } catch (e: any) {
      message.error(e.message);
    }
  };

  // ---- 闹钟 ----
  const setAlarm = (minutesFromNow: number) => {
    if (alarmTimerRef.current) clearInterval(alarmTimerRef.current);

    const target = Date.now() + minutesFromNow * 60 * 1000;
    const h = new Date(target).getHours().toString().padStart(2, '0');
    const m = new Date(target).getMinutes().toString().padStart(2, '0');
    setAlarmTime(`${h}:${m}`);

    alarmTimerRef.current = setInterval(() => {
      const remaining = target - Date.now();
      if (remaining <= 0) {
        if (alarmTimerRef.current) clearInterval(alarmTimerRef.current);
        setAlarmCountdown('');
        setAlarmTime(null);
        // 触发提醒
        try {
          if (!alarmAudioRef.current) {
            // 用 AudioContext 生成提示音
            const ctx = new AudioContext();
            const playBeep = () => {
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.frequency.value = 800;
              gain.gain.value = 0.3;
              osc.start();
              osc.stop(ctx.currentTime + 0.2);
            };
            playBeep();
            setTimeout(playBeep, 400);
            setTimeout(playBeep, 800);
          }
        } catch {}
        if (Notification.permission === 'granted') {
          new Notification('收菜时间到！', { body: '该去打印机收菜换版了' });
        }
        message.warning('收菜时间到！', 10);
      } else {
        const min = Math.floor(remaining / 60000);
        const sec = Math.floor((remaining % 60000) / 1000);
        setAlarmCountdown(`${min}:${sec.toString().padStart(2, '0')}`);
      }
    }, 1000);

    message.success(`闹钟已设定：${h}:${m}（${minutesFromNow}分钟后）`);
  };

  const setAlarmForBatch = (batch: any) => {
    // 计算收菜时间距现在的分钟数
    const [sh, sm] = batch.start_time.split(':').map(Number);
    const goMin = sh * 60 + sm - changeoverMin;
    const now = new Date();
    const nowMin = now.getHours() * 60 + now.getMinutes();
    const diff = goMin - nowMin;
    if (diff <= 0) {
      message.warning('该批次收菜时间已过');
      return;
    }
    setAlarm(diff);
  };

  const cancelAlarm = () => {
    if (alarmTimerRef.current) clearInterval(alarmTimerRef.current);
    setAlarmTime(null);
    setAlarmCountdown('');
    message.info('闹钟已取消');
  };

  // 请求通知权限
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  // 清理定时器
  useEffect(() => {
    return () => { if (alarmTimerRef.current) clearInterval(alarmTimerRef.current); };
  }, []);

  const isConfirmed = selectedPlan?.status === 'confirmed';

  // 处理超过24:00的时间显示，如 "33:40" → "03-31 09:40"
  const fmtTime = (t: string) => {
    const [h, m] = t.split(':').map(Number);
    if (h >= 24) {
      const d = dayjs().startOf('day').add(Math.floor(h / 24), 'day');
      return `${d.format('MM-DD')} ${String(h % 24).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    }
    return t;
  };

  const getConfigInfo = (configId: number, color?: string) => {
    const cfg = configs.find(c => c.id === configId);
    if (!cfg) return `配置#${configId}`;
    const comp = components.find(c => c.id === cfg.component_id);
    const colorStr = color ? `/${color}` : '';
    return `${cfg.plate_name}（${comp?.name || '?'}${colorStr} x${cfg.quantity}, ${cfg.duration_minutes}分钟）`;
  };

  const getPrinterName = (printerId: number) => {
    return printers.find(p => p.id === printerId)?.name || `打印机#${printerId}`;
  };

  const batchStatusTag = (status: string) => {
    switch (status) {
      case 'started': return <Tag color="blue">进行中</Tag>;
      case 'completed': return <Tag color="green">已完成</Tag>;
      default: return <Tag>待开始</Tag>;
    }
  };

  const taskStatusTag = (status: string) => {
    switch (status) {
      case 'completed': return <Tag color="green">已完成</Tag>;
      case 'cancelled': return <Tag color="default">已取消</Tag>;
      case 'failed': return <Tag color="red">失败</Tag>;
      default: return <Tag>进行中</Tag>;
    }
  };

  // 排班总结
  const renderSummary = () => {
    if (!selectedPlan?.batches?.length) return null;

    // 用 "component_id:color" 作为 key
    const mk = (compId: number, color: string) => `${compId}:${color || ''}`;

    const production: Record<string, number> = {};
    const surplusProduction: Record<string, number> = {};
    for (const batch of selectedPlan.batches) {
      for (const task of batch.tasks) {
        const cfg = configs.find(c => c.id === task.print_config_id);
        if (cfg) {
          const key = mk(cfg.component_id, task.color || '');
          production[key] = (production[key] || 0) + cfg.quantity;
          if (task.is_surplus) {
            surplusProduction[key] = (surplusProduction[key] || 0) + cfg.quantity;
          }
        }
      }
    }

    const surplusMap: Record<string, { stock: number; demand: number }> = {};
    for (const s of surplus) {
      surplusMap[mk(s.component_id, s.color || '')] = { stock: s.stock, demand: s.demand };
    }

    // 计算比当前排班更早的其他排班的产出
    const earlierProduction: Record<string, number> = {};
    const curKey = `${selectedPlan.date} ${selectedPlan.start_time || '08:00'}`;
    for (const p of plans) {
      const pKey = `${p.date} ${p.start_time || '08:00'}`;
      if (pKey >= curKey || p.id === selectedPlan.id) continue;
      for (const batch of (p.batches || [])) {
        for (const task of (batch.tasks || [])) {
          const cfg = configs.find(c => c.id === task.print_config_id);
          if (cfg) {
            const key = mk(cfg.component_id, task.color || '');
            earlierProduction[key] = (earlierProduction[key] || 0) + cfg.quantity;
          }
        }
      }
    }

    // 收集所有需要显示的 key
    const allKeys = new Set<string>();
    Object.keys(production).forEach(k => allKeys.add(k));
    Object.keys(surplusMap).forEach(k => { if (surplusMap[k].demand > 0) allKeys.add(k); });

    const compRows = Array.from(allKeys).map(key => {
      const [compIdStr, color] = key.split(':');
      const compId = Number(compIdStr);
      const comp = components.find(c => c.id === compId);
      const produced = production[key] || 0;
      const surplusProd = surplusProduction[key] || 0;
      const stock = (surplusMap[key]?.stock || 0) + (earlierProduction[key] || 0);
      const demand = surplusMap[key]?.demand || 0;
      const afterPlan = stock + produced;
      const remaining = demand - afterPlan;
      return { key, compId, name: comp?.name || '?', color, produced, surplusProd, stock, afterPlan, demand, remaining: remaining > 0 ? remaining : 0 };
    }).filter(r => r.produced > 0 || r.demand > 0);

    const productCapacity = products.map(prod => {
      const bom = prod.bom_items || [];
      if (bom.length === 0) return null;
      const count = Math.min(...bom.map((b: any) => {
        const key = mk(b.component_id, b.color || '');
        const row = compRows.find(r => r.key === key);
        const available = row ? row.afterPlan : (surplusMap[key]?.stock || 0);
        return Math.floor(available / b.quantity);
      }));
      return { name: prod.name, count };
    }).filter(Boolean);

    return (
      <div style={{ marginBottom: 16 }}>
        <Table
          dataSource={compRows}
          rowKey="key"
          size="small"
          pagination={false}
          title={() => <strong>排班总结</strong>}
          columns={[
            { title: '组件', dataIndex: 'name' },
            { title: '颜色', dataIndex: 'color', width: 80, render: (v: string) => v || '-' },
            { title: '当前库存', dataIndex: 'stock', width: 90 },
            { title: '本次生产', dataIndex: 'produced', width: 120,
              render: (_: number, rec: any) => {
                if (rec.produced <= 0) return '-';
                const demand = rec.produced - rec.surplusProd;
                return (
                  <span>
                    {demand > 0 && <Tag color="blue">+{demand}</Tag>}
                    {rec.surplusProd > 0 && <Tag color="orange">+{rec.surplusProd} 富余</Tag>}
                  </span>
                );
              },
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

        {/* 打印机利用率 */}
        {(() => {
          const totalSpan = (selectedPlan.duration_hours || 24) * 60; // 完整排班周期（分钟）

          // 每台打印机的工作时长
          const printerWork: Record<number, number> = {};
          for (const batch of selectedPlan.batches) {
            for (const task of batch.tasks) {
              const [sh, sm] = task.start_time.split(':').map(Number);
              const [eh, em] = task.end_time.split(':').map(Number);
              const dur = (eh * 60 + em) - (sh * 60 + sm);
              printerWork[task.printer_id] = (printerWork[task.printer_id] || 0) + dur;
            }
          }

          const rows = printers.map(p => {
            const work = printerWork[p.id] || 0;
            const rate = Math.round((work / totalSpan) * 100);
            const workH = Math.floor(work / 60);
            const workM = work % 60;
            return { id: p.id, name: p.name, workLabel: workM > 0 ? `${workH}h${workM}m` : `${workH}h`, rate };
          });

          return (
            <Table
              dataSource={rows}
              rowKey="id"
              size="small"
              pagination={false}
              style={{ marginTop: 12 }}
              title={() => <strong>打印机利用率（{selectedPlan.duration_hours || 24}小时）</strong>}
              columns={[
                { title: '打印机', dataIndex: 'name', width: 120 },
                { title: '工作时长', dataIndex: 'workLabel', width: 100 },
                { title: '利用率', dataIndex: 'rate', width: 200,
                  render: (v: number) => (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, background: '#f0f0f0', borderRadius: 4, height: 16 }}>
                        <div style={{ width: `${v}%`, background: v > 80 ? '#52c41a' : v > 50 ? '#1677ff' : '#faad14', height: '100%', borderRadius: 4 }} />
                      </div>
                      <span style={{ width: 40, textAlign: 'right' }}>{v}%</span>
                    </div>
                  ),
                },
              ]}
            />
          );
        })()}
      </div>
    );
  };

  // 甘特图
  const renderGantt = () => {
    if (!selectedPlan?.batches?.length) return <div style={{ color: '#999' }}>无排班数据</div>;

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

    const taskColor = (task: any) => {
      switch (task.status) {
        case 'completed': return '#52c41a';
        case 'cancelled': return '#d9d9d9';
        case 'failed': return '#ff4d4f';
        default: return task.is_surplus ? '#faad14' : '#1677ff';
      }
    };

    // 根据总时长自动选择刻度间隔，避免标签拥挤
    const totalHours = Math.ceil(totalMin / 60);
    const step = totalHours <= 6 ? 1 : totalHours <= 14 ? 2 : totalHours <= 30 ? 3 : 6;

    return (
      <div style={{ overflowX: 'auto' }}>
        <div style={{ marginLeft: 100, marginBottom: 8, position: 'relative', height: 30 }}>
          {Array.from({ length: Math.floor(totalHours / step) + 1 }, (_, i) => {
            const hour = Math.floor(minTime / 60) + i * step;
            const h24 = ((hour % 24) + 24) % 24;
            const dayIdx = Math.floor(hour / 24);
            const prevDayIdx = i === 0 ? -1 : Math.floor((hour - step) / 24);
            const showDate = dayIdx !== prevDayIdx;
            const dateLabel = dayjs(selectedPlan.date).add(dayIdx, 'day').format('M/D');
            const offset = (i * step * 60) / totalMin * 100;
            return (
              <div key={i} style={{ position: 'absolute', left: `${offset}%`, fontSize: 12, color: '#999', whiteSpace: 'nowrap' }}>
                <div>{`${h24}:00`}</div>
                {showDate && <div style={{ fontSize: 10, color: '#bbb' }}>{dateLabel}</div>}
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
                      title={`${getConfigInfo(task.print_config_id, task.color)}\n${fmtTime(task.start_time)} - ${fmtTime(task.end_time)}\n${{ completed: '已完成', cancelled: '已取消', failed: '失败' }[task.status as string] || '进行中'}`}
                      style={{
                        position: 'absolute', left: `${left}%`, width: `${width}%`,
                        top: 2, bottom: 2, background: taskColor(task),
                        borderRadius: 4, color: '#fff', fontSize: 11, padding: '0 4px',
                        overflow: 'hidden', whiteSpace: 'nowrap', cursor: 'pointer',
                      }}
                    >
                      {getConfigInfo(task.print_config_id, task.color)}
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
      const [sh, sm] = batch.start_time.split(':').map(Number);
      const goMin = sh * 60 + sm - changeoverMin;
      const goTimeRaw = `${String(Math.floor(goMin / 60)).padStart(2, '0')}:${String(goMin % 60).padStart(2, '0')}`;
      const title = batch.batch_order === 0
        ? `批次 ${batch.batch_order + 1} — ${fmtTime(batch.start_time)} 启动（首批）`
        : `批次 ${batch.batch_order + 1} — ${fmtTime(goTimeRaw)} 收菜，${fmtTime(batch.start_time)} 启动`;

      return (
      <Card
        key={batch.id}
        size="small"
        title={<span>{title} {isConfirmed && batchStatusTag(batch.status)}</span>}
        extra={
          <Space>
            {/* 执行控制（已确认排班才显示） */}
            {isConfirmed && batch.status === 'pending' && (
              <Popconfirm
                title="确认开始此批次？"
                description="将以当前时间作为实际开始时间，后续批次会相应调整。"
                onConfirm={() => startBatch(batch.id)}
              >
                <Button size="small" type="primary" icon={<PlayCircleOutlined />}>开始</Button>
              </Popconfirm>
            )}
            {/* 闹钟（下一个未开始的批次，且不是首批） */}
            {isConfirmed && batch.status === 'pending' && batch.batch_order > 0 && (
              <Button size="small" icon={<BellOutlined />} onClick={() => setAlarmForBatch(batch)}>
                设闹钟
              </Button>
            )}
            {/* 草稿模式下可删除 */}
            {!isConfirmed && (
              <Popconfirm title="删除此批次？" onConfirm={() => deleteBatch(batch.id)}>
                <Button size="small" danger>删除批次</Button>
              </Popconfirm>
            )}
          </Space>
        }
        style={{
          marginBottom: 12,
          borderLeft: batch.status === 'started' ? '3px solid #1677ff' : batch.status === 'completed' ? '3px solid #52c41a' : undefined,
        }}
      >
        <Table
          dataSource={batch.tasks}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '打印机', dataIndex: 'printer_id', render: (v: number) => getPrinterName(v) },
            { title: '打印内容', render: (_: any, rec: any) => (
              <span>
                {getConfigInfo(rec.print_config_id, rec.color)}
                {rec.is_surplus && <Tag color="orange" style={{ marginLeft: 4 }}>富余</Tag>}
              </span>
            )},
            { title: '开始', dataIndex: 'start_time', width: 90, render: (v: string) => fmtTime(v) },
            { title: '结束', dataIndex: 'end_time', width: 90, render: (v: string) => fmtTime(v) },
            ...(isConfirmed ? [{
              title: '状态', width: 100,
              render: (_: any, rec: any) => taskStatusTag(rec.status),
            }] : []),
            {
              title: '操作', width: isConfirmed ? 200 : 80,
              render: (_: any, rec: any) => {
                const ended = ['completed', 'cancelled', 'failed'].includes(rec.status);
                return (
                  <Space>
                    {isConfirmed && batch.status === 'started' && !ended && (
                      <>
                        <Popconfirm title="确认完成？库存将自动增加。" onConfirm={() => completeTask(rec.id)}>
                          <Button size="small" type="primary" icon={<CheckCircleOutlined />}>完成</Button>
                        </Popconfirm>
                        <Popconfirm title="确认取消此任务？" onConfirm={() => cancelTask(rec.id)}>
                          <Button size="small" icon={<CloseCircleOutlined />}>取消</Button>
                        </Popconfirm>
                        <Popconfirm title="标记任务失败？" onConfirm={() => failTask(rec.id)}>
                          <Button size="small" danger icon={<WarningOutlined />}>失败</Button>
                        </Popconfirm>
                      </>
                    )}
                    {!isConfirmed && (
                      <Popconfirm title="删除此任务？" onConfirm={() => deleteTask(rec.id)}>
                        <Button size="small" danger>删除</Button>
                      </Popconfirm>
                    )}
                  </Space>
                );
              },
            },
          ]}
        />
      </Card>
    )});
  };

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>排班中心</h2>

      {/* 闹钟状态栏 */}
      {alarmTime && (
        <Card size="small" style={{ marginBottom: 16, background: '#fff7e6', borderColor: '#ffd591' }}>
          <Space>
            <BellOutlined style={{ color: '#fa8c16', fontSize: 18 }} />
            <span>闹钟：<strong>{alarmTime}</strong> 收菜</span>
            <span>倒计时：<strong>{alarmCountdown}</strong></span>
            <Button size="small" onClick={cancelAlarm}>取消</Button>
          </Space>
        </Card>
      )}

      {/* 自定义闹钟 */}
      <Card size="small" style={{ marginBottom: 24 }}>
        <Space>
          <ClockCircleOutlined />
          <span>快速闹钟：</span>
          <InputNumber
            min={1}
            placeholder="分钟"
            value={alarmMinutes}
            onChange={v => setAlarmMinutes(v)}
            size="small"
            style={{ width: 80 }}
          />
          <Button size="small" disabled={!alarmMinutes} onClick={() => { if (alarmMinutes) setAlarm(alarmMinutes); }}>
            设定
          </Button>
          <span style={{ color: '#999', fontSize: 12 }}>或在下方批次中点"设闹钟"</span>
        </Space>
      </Card>

      {/* 生成排班 */}
      <Card style={{ marginBottom: 24 }}>
        <div style={{ maxWidth: 560, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Row 1: 时间参数 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
            <span style={{ fontWeight: 500, width: 70, flexShrink: 0 }}>排班时间</span>
            <DatePicker value={date} onChange={v => v && setDate(v)} style={{ flex: 1 }} />
            <TimePicker value={startTime} format="HH:mm" onChange={v => v && setStartTime(v)} placeholder="开始时间" style={{ flex: 1 }} />
            <InputNumber value={durationHours} min={1} max={168} onChange={v => setDurationHours(v ?? 24)} addonAfter="小时" style={{ flex: 1 }} />
          </div>

          {/* Row 2: 调度策略 + 富余 */}
          <div style={{ display: 'flex', gap: 12 }}>
            <span style={{ fontWeight: 500, width: 70, flexShrink: 0, lineHeight: '32px' }}>调度策略</span>
            <div style={{ flex: 1, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <div>
                <Radio.Group value={strategy} onChange={e => setStrategy(e.target.value)} optionType="button" buttonStyle="solid">
                  <Radio.Button value="product_first">优先凑齐发货</Radio.Button>
                  <Radio.Button value="utilization">最大化利用率</Radio.Button>
                  <Radio.Button value="two_phase">智能规划</Radio.Button>
                </Radio.Group>
                <div style={{ fontSize: 12, color: '#999', marginTop: 6, textAlign: 'right' }}>
                  {strategy === 'product_first' ? '优先安排能凑齐完整产品的瓶颈组件'
                    : strategy === 'utilization' ? '优先填满打印机，减少空闲时间'
                    : '全局优化组件配比，凑齐最多完整产品'}
                </div>
              </div>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, lineHeight: '32px' }}>
                  <span style={{ whiteSpace: 'nowrap' }}>富余生产</span>
                  <Switch checked={surplusEnabled} onChange={setSurplusEnabled} />
                </div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 6, textAlign: 'right' }}>
                  {surplusEnabled ? '满足后继续备货' : '仅生产订单所需'}
                </div>
              </div>
            </div>
          </div>

          {/* Row 3: 指定产品 */}
          <div style={{ display: 'flex', gap: 12 }}>
            <span style={{ fontWeight: 500, width: 70, flexShrink: 0, lineHeight: '32px' }}>指定产品</span>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <Select
                  mode="multiple"
                  allowClear
                  placeholder="全部产品（按订单顺序）"
                  value={targetProductIds}
                  onChange={setTargetProductIds}
                  style={{ flex: 1 }}
                  options={products.map((p: any) => ({ label: p.name, value: p.id }))}
                />
                {targetProductIds.length > 0 && (
                  <Button size="small" style={{ marginTop: 4 }} onClick={() => setTargetProductIds([])}>清除</Button>
                )}
              </div>
              {targetProductIds.length > 0 && (
                <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                  仅排班选中产品的组件，其余产品不会被生产
                </div>
              )}
            </div>
          </div>
          {/* Row 4: 同步强度 */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ fontWeight: 500, width: 70, flexShrink: 0 }}>同步强度</span>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <Slider
                  min={0}
                  max={100}
                  value={syncStrength}
                  onChange={setSyncStrength}
                  style={{ flex: 1 }}
                  marks={{ 0: '0', 50: '50', 100: '100' }}
                />
                <span style={{ minWidth: 30, textAlign: 'right', fontWeight: 500 }}>{syncStrength}</span>
              </div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 2, textAlign: 'right' }}>
                {syncStrength === 0 ? '不对齐，各打印机独立选最优任务'
                  : syncStrength === 100 ? '强制对齐，尽量所有打印机同时完成'
                  : '平衡最优任务和同批次打印机完成时间对齐'}
              </div>
            </div>
          </div>

          {/* 生成按钮 */}
          <div>
            <Button type="primary" size="large" onClick={generate}>生成排班表</Button>
          </div>
        </div>
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
            { title: '时间范围', render: (_: any, rec: any) => {
              const st = rec.start_time || '08:00';
              const [sh, sm] = st.split(':').map(Number);
              const endMin = sh * 60 + sm + (rec.duration_hours || 24) * 60;
              const endDay = Math.floor(endMin / 1440);
              const endH = Math.floor((endMin % 1440) / 60);
              const endM = endMin % 60;
              const endDate = dayjs(rec.date).add(endDay, 'day');
              const endStr = endDay > 0
                ? `${endDate.format('MM-DD')} ${String(endH).padStart(2,'0')}:${String(endM).padStart(2,'0')}`
                : `${String(endH).padStart(2,'0')}:${String(endM).padStart(2,'0')}`;
              return `${rec.date} ${st} ~ ${endStr}`;
            }},
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
        <Card title={`排班详情 — ${selectedPlan.date} ${selectedPlan.start_time || '08:00'}（${selectedPlan.duration_hours || 24}小时）`}>
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
