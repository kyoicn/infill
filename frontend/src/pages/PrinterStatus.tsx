import { useCallback, useEffect, useState } from 'react';
import { Button, Col, Empty, Row, Space, Spin } from 'antd';
import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  SyncOutlined,
} from '@ant-design/icons';
import {
  api,
  type PrinterStatusEvent,
  type PrinterStatusSnapshot,
} from '../api/client';
import { usePrinterStatusWS } from './printer_status/usePrinterStatusWS';
import { PrinterCard } from './printer_status/PrinterCard';

export default function PrinterStatus() {
  const [snapshots, setSnapshots] = useState<PrinterStatusSnapshot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState<Date>(new Date());

  const loadSnapshot = useCallback(async () => {
    try {
      const data = await api.getPrinterStatusSnapshot();
      setSnapshots(data);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '拉取失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const onEvent = useCallback((e: PrinterStatusEvent) => {
    setSnapshots((prev) =>
      prev
        ? prev.map((s) =>
            s.printer_id === e.printer_id
              ? { ...s, state: e.state, last_state_change_ts: e.ts }
              : s,
          )
        : prev,
    );
  }, []);

  const { status: wsStatus } = usePrinterStatusWS(onEvent, loadSnapshot);

  // mount 时立即拉一次 snapshot — 不依赖 WS 是否连得上（PRD CUJ-2 Step 1：
  // 先 snapshot 再 WS，是两个独立动作）。onReconnect 回调留作断线重连补齐。
  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  // 每分钟 tick 一次更新「现在」竖线位置（粒度足够 — 时间轴 1440 分一格）
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);

  const wsBadge =
    wsStatus === 'connected' ? (
      <Space size={4}>
        <CheckCircleFilled style={{ color: '#52c41a' }} />
        <span>实时连接中</span>
      </Space>
    ) : wsStatus === 'reconnecting' || wsStatus === 'connecting' ? (
      <Space size={4}>
        <SyncOutlined spin style={{ color: '#faad14' }} />
        <span>重连中…</span>
      </Space>
    ) : (
      <Space size={4}>
        <ExclamationCircleFilled style={{ color: '#ff4d4f' }} />
        <span>实时连接断开</span>
      </Space>
    );

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin />
        <div style={{ marginTop: 12, color: '#666' }}>加载中…</div>
      </div>
    );
  }

  if (error) {
    return (
      <Empty description={error}>
        <Button
          onClick={() => {
            setLoading(true);
            void loadSnapshot();
          }}
        >
          重试
        </Button>
      </Empty>
    );
  }

  if (!snapshots || snapshots.length === 0) {
    return (
      <Empty
        description={
          <>
            暂无打印机，请先到 <a href="/settings">系统设置 → 打印机管理</a> 添加。
          </>
        }
      />
    );
  }

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <h2 style={{ margin: 0 }}>打印机状态</h2>
        {wsBadge}
      </div>
      <Row gutter={[16, 16]}>
        {snapshots.map((s) => (
          <Col key={s.printer_id} xs={24} sm={12} lg={6}>
            <PrinterCard snapshot={s} now={now} />
          </Col>
        ))}
      </Row>
    </div>
  );
}
