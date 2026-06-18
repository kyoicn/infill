import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Spin, Tooltip, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import {
  api,
  type AutoImportAdbConfig,
  type AutoImportDiagnostic,
  type AutoImportScanResponse,
} from '../../api/client';
import ScreencapGrid, { type ScreenEntry } from './ScreencapGrid';

interface XianyuTabProps {
  onScan: (batchId: string, scan: AutoImportScanResponse) => void;
  otherInProgress: boolean;
}

type XianyuState =
  | { kind: 'idle' }
  | {
      kind: 'capturing';
      batchId: string;
      screens: ScreenEntry[];
      parsedOrders: unknown[];
    }
  | { kind: 'finishing'; batchId: string }
  | { kind: 'error_adb'; diagnostics: AutoImportDiagnostic[] };

const DEVICE_LABELS: Record<string, string> = {
  mumu: 'MuMu 模拟器',
  bluestacks: '蓝叠模拟器',
  ldplayer: '雷电模拟器',
  usb: 'USB 真机',
};

const POLL_INTERVAL_MS = 1500;

export default function XianyuTab({ onScan, otherInProgress }: XianyuTabProps) {
  const navigate = useNavigate();
  const [config, setConfig] = useState<AutoImportAdbConfig | null>(null);
  const [state, setState] = useState<XianyuState>({ kind: 'idle' });
  const [bootstrapping, setBootstrapping] = useState(true);
  const [screencapBusy, setScreencapBusy] = useState(false);

  const pollTimerRef = useRef<number | null>(null);
  const stateRef = useRef<XianyuState>(state);
  stateRef.current = state;

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const probe = useCallback(async (cfg: AutoImportAdbConfig) => {
    try {
      const resp = await api.autoImport.xianyu.probe(cfg);
      if (resp.ok && resp.adb_connected) {
        setState({ kind: 'idle' });
      } else {
        setState({ kind: 'error_adb', diagnostics: resp.diagnostics ?? [] });
      }
    } catch (err) {
      setState({
        kind: 'error_adb',
        diagnostics: [
          {
            name: 'probe_error',
            label: 'ADB 探测失败',
            ok: false,
            detail: err instanceof Error ? err.message : String(err),
          },
        ],
      });
    }
  }, []);

  // Mount: load config + initial probe
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await api.autoImport.xianyu.getConfig();
        if (cancelled) return;
        setConfig(cfg);
        await probe(cfg);
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: 'error_adb',
          diagnostics: [
            {
              name: 'config_load',
              label: '读取 ADB 配置失败',
              ok: false,
              detail: err instanceof Error ? err.message : String(err),
            },
          ],
        });
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [probe]);

  // Polling: only when capturing
  const startPolling = useCallback(
    (batchId: string) => {
      stopPolling();
      const tick = async () => {
        try {
          const resp = await api.autoImport.xianyu.scanStatus(batchId);
          const current = stateRef.current;
          if (current.kind !== 'capturing' || current.batchId !== batchId) {
            return;
          }
          const mergedScreens: ScreenEntry[] = resp.screens.map((s) => {
            const status: ScreenEntry['status'] =
              s.status === 'parsed' || s.status === 'failed'
                ? s.status
                : 'parsing';
            return {
              seq: s.seq,
              status,
              error: s.error ?? null,
            };
          });
          setState({
            kind: 'capturing',
            batchId,
            screens: mergedScreens,
            parsedOrders: resp.parsed_orders ?? [],
          });
        } catch {
          // swallow transient errors; keep polling
        } finally {
          const current = stateRef.current;
          if (current.kind === 'capturing' && current.batchId === batchId) {
            pollTimerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
          }
        }
      };
      pollTimerRef.current = window.setTimeout(tick, POLL_INTERVAL_MS);
    },
    [stopPolling],
  );

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  useEffect(() => {
    if (state.kind !== 'capturing' && pollTimerRef.current !== null) {
      stopPolling();
    }
  }, [state.kind, stopPolling]);

  const handleScreencap = useCallback(async () => {
    if (!config) return;
    if (screencapBusy) return;
    setScreencapBusy(true);

    let batchId: string;
    let startingFresh = false;
    if (state.kind === 'idle') {
      batchId = crypto.randomUUID();
      startingFresh = true;
      setState({
        kind: 'capturing',
        batchId,
        screens: [],
        parsedOrders: [],
      });
    } else if (state.kind === 'capturing') {
      batchId = state.batchId;
    } else {
      setScreencapBusy(false);
      return;
    }

    try {
      const resp = await api.autoImport.xianyu.screencap({
        batch_id: batchId,
        config,
      });
      if (!resp.ok) {
        message.error(resp.error || resp.error_kind || '截屏失败');
        return;
      }
      const newSeq = resp.seq ?? 0;
      setState((prev) => {
        if (prev.kind !== 'capturing' || prev.batchId !== batchId) return prev;
        const exists = prev.screens.some((s) => s.seq === newSeq);
        const screens: ScreenEntry[] = exists
          ? prev.screens
          : [
              ...prev.screens,
              { seq: newSeq, status: 'parsing' as const, error: null },
            ];
        return { ...prev, screens };
      });
      if (startingFresh) {
        startPolling(batchId);
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '截屏请求失败');
    } finally {
      setScreencapBusy(false);
    }
  }, [config, screencapBusy, state, startPolling]);

  const handleFinish = useCallback(async () => {
    if (state.kind !== 'capturing') return;
    if (state.screens.length === 0) return;
    const batchId = state.batchId;
    stopPolling();
    setState({ kind: 'finishing', batchId });
    try {
      const resp = await api.autoImport.xianyu.finishScan(batchId);
      if (!resp.ok) {
        message.error(resp.error || resp.error_kind || '解析失败');
        setState({ kind: 'idle' });
        return;
      }
      onScan(batchId, resp);
      setState({ kind: 'idle' });
    } catch (err) {
      message.error(err instanceof Error ? err.message : '解析请求失败');
      setState({ kind: 'idle' });
    }
  }, [state, stopPolling, onScan]);

  const handleCancel = useCallback(async () => {
    if (state.kind !== 'capturing') return;
    const batchId = state.batchId;
    stopPolling();
    try {
      await api.autoImport.cancelScan(batchId);
    } catch {
      // best-effort
    }
    setState({ kind: 'idle' });
  }, [state, stopPolling]);

  const handleRetestAdb = useCallback(async () => {
    if (!config) return;
    setState({ kind: 'idle' });
    setBootstrapping(true);
    await probe(config);
    setBootstrapping(false);
  }, [config, probe]);

  // ---------- Derived button state ----------
  const adbConnected = state.kind !== 'error_adb';
  const inCaptureMode = state.kind === 'capturing';
  const captureCount = inCaptureMode ? state.screens.length : 0;

  const canScreencap =
    !otherInProgress &&
    adbConnected &&
    !screencapBusy &&
    (state.kind === 'idle' || state.kind === 'capturing');

  const canFinish =
    !otherInProgress && inCaptureMode && captureCount >= 1;

  const canCancel = !otherInProgress && inCaptureMode;

  const disabledByOther = otherInProgress;

  // ---------- Renderers ----------
  if (bootstrapping) {
    return (
      <div style={{ padding: 64, textAlign: 'center' }}>
        <Spin tip="正在检测 ADB 连接..." />
      </div>
    );
  }

  const wrapButton = (btn: React.ReactNode) =>
    disabledByOther ? (
      <Tooltip title="等待小红书扫描完成">{btn}</Tooltip>
    ) : (
      btn
    );

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '360px 1fr',
        gap: 20,
      }}
    >
      {/* Left sticky control panel */}
      <aside
        style={{
          position: 'sticky',
          top: 16,
          alignSelf: 'flex-start',
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: 20,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div>
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              background: '#fff',
              padding: '4px 10px',
              borderRadius: 100,
              fontSize: 12,
              color: 'rgba(0,0,0,0.65)',
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: adbConnected ? '#52c41a' : '#ff4d4f',
                display: 'inline-block',
              }}
            />
            {adbConnected ? 'ADB 已连接' : 'ADB 未连接'}
          </div>
        </div>

        {config && (
          <div
            style={{
              fontSize: 12,
              color: 'rgba(0,0,0,0.65)',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'rgba(0,0,0,0.45)', minWidth: 64 }}>
                设备类型
              </span>
              <span>
                {DEVICE_LABELS[config.device_type] ?? config.device_type}
              </span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'rgba(0,0,0,0.45)', minWidth: 64 }}>
                PC 端点
              </span>
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: 11,
                  background: '#fff',
                  padding: '1px 5px',
                  borderRadius: 3,
                  border: '1px solid #f0f0f0',
                }}
              >
                {config.pc_ip}:{config.port}
              </span>
            </div>
            <a
              href="/settings/auto-import"
              onClick={(e) => {
                e.preventDefault();
                navigate('/settings/auto-import');
              }}
              style={{ color: '#1677ff', fontSize: 12 }}
            >
              编辑 →
            </a>
          </div>
        )}

        {/* 4-step guide */}
        <div
          style={{
            background: '#f5f5f5',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            padding: '12px 14px',
            fontSize: 12,
            color: 'rgba(0,0,0,0.65)',
            lineHeight: 1.7,
          }}
        >
          <b style={{ color: 'rgba(0,0,0,0.88)' }}>典型流程：</b>
          <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li>Android 设备打开闲鱼 → 订单列表</li>
            <li>点「截屏 (+1)」捕获当前画面</li>
            <li>滑动设备一屏，再次点击「截屏」</li>
            <li>看完后点「完成截屏，开始解析」</li>
          </ol>
        </div>

        {/* Counter */}
        {inCaptureMode && (
          <div
            style={{
              background: '#fff',
              border: '1px dashed #d9d9d9',
              padding: '10px 12px',
              borderRadius: 6,
              fontSize: 13,
              textAlign: 'center',
              color: 'rgba(0,0,0,0.65)',
            }}
          >
            已捕获{' '}
            <b style={{ fontSize: 18, color: 'rgba(0,0,0,0.88)' }}>
              {captureCount}
            </b>{' '}
            张
          </div>
        )}

        {/* Action buttons */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            borderTop: '1px solid #f0f0f0',
            paddingTop: 14,
          }}
        >
          {wrapButton(
            <Button
              block
              size="large"
              onClick={handleScreencap}
              disabled={!canScreencap}
              loading={screencapBusy}
              style={{
                background: canScreencap ? '#fff5e6' : undefined,
                color: canScreencap ? '#ff7a00' : undefined,
                borderColor: canScreencap ? '#ffd591' : undefined,
                fontWeight: 600,
              }}
            >
              截屏 (+1)
            </Button>,
          )}
          {wrapButton(
            <Button
              block
              type="primary"
              size="large"
              onClick={handleFinish}
              disabled={!canFinish}
              style={
                canFinish
                  ? { background: '#ff7a00', borderColor: '#ff7a00' }
                  : undefined
              }
            >
              完成截屏，开始解析{captureCount > 0 ? ` (${captureCount} 张)` : ''}
            </Button>,
          )}
          {wrapButton(
            <Button block onClick={handleCancel} disabled={!canCancel}>
              取消
            </Button>,
          )}
        </div>
      </aside>

      {/* Right main area */}
      <section
        style={{
          border: '1px solid #f0f0f0',
          borderRadius: 8,
          padding: 24,
          minHeight: 500,
          background: '#fff',
        }}
      >
        {state.kind === 'idle' && (
          <div
            style={{
              padding: 64,
              textAlign: 'center',
              color: 'rgba(0,0,0,0.45)',
              fontSize: 13,
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: '50%',
                background: '#fafafa',
                margin: '0 auto 14px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 24,
              }}
            >
              ⌖
            </div>
            <div style={{ marginBottom: 6, color: 'rgba(0,0,0,0.65)' }}>
              点击左侧「截屏 (+1)」开始
            </div>
            <div style={{ fontSize: 12 }}>
              建议先在 Android 设备上把闲鱼滚到订单列表顶部
            </div>
          </div>
        )}

        {state.kind === 'capturing' && (
          <ScreencapGrid
            screens={state.screens}
            parsedOrders={state.parsedOrders}
          />
        )}

        {state.kind === 'finishing' && (
          <div style={{ padding: 64, textAlign: 'center' }}>
            <Spin tip="正在合并解析结果..." size="large" />
          </div>
        )}

        {state.kind === 'error_adb' && (
          <div
            style={{
              border: '1px solid #ffccc7',
              background: '#fff2f0',
              borderRadius: 8,
              padding: 24,
            }}
          >
            <h3
              style={{
                margin: 0,
                color: '#cf1322',
                fontSize: 15,
                marginBottom: 10,
              }}
            >
              ADB 连接失败
            </h3>
            <div
              style={{
                background: '#fff',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: 14,
                marginBottom: 14,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              {state.diagnostics.slice(0, 3).map((d) => (
                <div
                  key={d.name}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    fontSize: 13,
                  }}
                >
                  <span
                    style={{
                      width: 18,
                      height: 18,
                      borderRadius: '50%',
                      background: d.ok ? '#52c41a' : '#ff4d4f',
                      color: '#fff',
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 11,
                      flexShrink: 0,
                    }}
                  >
                    {d.ok ? '✓' : '✗'}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      color: d.ok ? 'rgba(0,0,0,0.65)' : '#cf1322',
                    }}
                  >
                    {d.label}
                  </span>
                  {d.detail && (
                    <span
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: 'rgba(0,0,0,0.45)',
                      }}
                    >
                      {d.detail}
                    </span>
                  )}
                </div>
              ))}
              {state.diagnostics.length === 0 && (
                <span style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>
                  无详细诊断信息
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <Button type="primary" danger onClick={handleRetestAdb}>
                重新测试 ADB
              </Button>
              <a
                href="/settings/auto-import"
                onClick={(e) => {
                  e.preventDefault();
                  navigate('/settings/auto-import');
                }}
                style={{ color: '#1677ff', fontSize: 13 }}
              >
                打开设置页修改 endpoint →
              </a>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
