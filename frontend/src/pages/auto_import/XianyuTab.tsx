import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Spin, Tooltip, message } from 'antd';
import { ApiOutlined, DisconnectOutlined } from '@ant-design/icons';
import {
  api,
  type AutoImportScanResponse,
} from '../../api/client';
import {
  connectPhone,
  currentDeviceName,
  disconnectPhone,
  isConnected,
  isWebUsbSupported,
  screencap as webadbScreencap,
} from '../../api/webadb';
import ScreencapGrid, { type ScreenEntry } from './ScreencapGrid';

interface XianyuTabProps {
  onScan: (batchId: string, scan: AutoImportScanResponse) => void;
  otherInProgress: boolean;
}

export default function XianyuTab({ onScan, otherInProgress }: XianyuTabProps) {
  const [deviceName, setDeviceName] = useState<string | null>(currentDeviceName());
  const [connecting, setConnecting] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [screens, setScreens] = useState<ScreenEntry[]>([]);
  const [screencapBusy, setScreencapBusy] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const connected = deviceName !== null;
  const webusbOk = isWebUsbSupported();

  // 重新挂载组件时 sync 一下单例连接状态
  useEffect(() => {
    if (isConnected()) {
      setDeviceName(currentDeviceName());
    }
  }, []);

  const handleConnect = useCallback(async () => {
    if (otherInProgress) return;
    setConnecting(true);
    try {
      const { deviceName: name } = await connectPhone();
      setDeviceName(name);
      message.success(`已连接：${name}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error('[webadb] connect failed', err);
      message.error(`连接失败：${msg}`);
    } finally {
      setConnecting(false);
    }
  }, [otherInProgress]);

  const handleDisconnect = useCallback(async () => {
    await disconnectPhone();
    setDeviceName(null);
    setBatchId(null);
    setScreens([]);
    message.info('已断开手机');
  }, []);

  const handleScreencap = useCallback(async () => {
    if (!connected) return;
    setScreencapBusy(true);
    try {
      let currentBatch = batchId;
      if (!currentBatch) {
        currentBatch = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
          ? crypto.randomUUID()
          : `xy-${Date.now()}`;
        setBatchId(currentBatch);
      }

      const pngBytes = await webadbScreencap();

      // multipart upload to backend
      const fd = new FormData();
      fd.append('batch_id', currentBatch);
      fd.append('png', new Blob([pngBytes as BlobPart], { type: 'image/png' }), `screen-${screens.length}.png`);
      const resp = await fetch('/api/auto-import/xianyu/screencap', {
        method: 'POST',
        body: fd,
      });
      const data = await resp.json();
      if (!data.ok) {
        message.error(`上传失败：${data.error || data.error_kind || 'unknown'}`);
        return;
      }
      const seq = data.seq ?? screens.length;
      setScreens((prev) =>
        prev.some((s) => s.seq === seq)
          ? prev
          : [...prev, { seq, status: 'captured' as const, error: null }],
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      message.error(`截屏失败：${msg}`);
      // 手机可能被拔了，标记断开
      if (msg.includes('未连接') || msg.includes('Disconnected')) {
        setDeviceName(null);
      }
    } finally {
      setScreencapBusy(false);
    }
  }, [connected, batchId, screens.length]);

  const handleFinish = useCallback(async () => {
    if (!batchId || screens.length === 0) return;
    setFinishing(true);
    try {
      const resp = await api.autoImport.xianyu.finishScan(batchId);
      if (!resp.ok) {
        message.error(resp.error || resp.error_kind || '解析失败');
        return;
      }
      onScan(batchId, resp);
      // 重置本地态（连接保留，方便继续下一批）
      setBatchId(null);
      setScreens([]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      message.error(`解析请求失败：${msg}`);
    } finally {
      setFinishing(false);
    }
  }, [batchId, screens.length, onScan]);

  const handleCancel = useCallback(async () => {
    if (!batchId) return;
    try {
      await api.autoImport.cancelScan(batchId);
    } catch {
      // 忽略
    }
    setBatchId(null);
    setScreens([]);
  }, [batchId]);

  // 卸载时断开
  useEffect(() => {
    return () => {
      // 不主动断开 — 用户切到 xhs tab 回来时还能用
    };
  }, []);

  const captureCount = screens.length;
  const canScreencap = connected && !otherInProgress && !screencapBusy && !finishing;
  const canFinish = connected && captureCount >= 1 && !finishing;
  const canCancel = captureCount >= 1 && !finishing;

  // ---- Renderers ----

  if (!webusbOk) {
    const isChromium = /Chrome|Edg/.test(navigator.userAgent);
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    const isSecure = typeof window !== 'undefined'
      && (window.isSecureContext || /^https:/.test(origin) || /^http:\/\/(localhost|127\.0\.0\.1)/.test(origin));
    return (
      <div style={{ padding: '32px 24px', maxWidth: 720, margin: '0 auto' }}>
        <Alert
          type="error"
          showIcon
          message="浏览器无法访问 WebUSB"
          description={
            <div>
              <p style={{ margin: '0 0 8px' }}>
                闲鱼路径需要浏览器通过 USB 直接和 Android 手机通信，依赖 WebUSB API。
              </p>
              {!isChromium && (
                <p style={{ margin: '0 0 8px' }}>
                  ❌ 你用的不是 Chromium 内核浏览器。请改用 <b>Chrome 或 Edge</b>，Firefox / Safari 不支持 WebUSB。
                </p>
              )}
              {isChromium && !isSecure && (
                <>
                  <p style={{ margin: '0 0 8px' }}>
                    ❌ 你访问的是 <code>{origin}</code>，Chrome 把局域网 HTTP 视为不安全上下文，禁用了 WebUSB。
                  </p>
                  <p style={{ margin: '0 0 8px', fontWeight: 600 }}>30 秒解法：把这个地址加到 Chrome 的安全例外</p>
                  <ol style={{ margin: '0 0 8px', paddingLeft: 20 }}>
                    <li>新 tab 打开 <code>chrome://flags/#unsafely-treat-insecure-origin-as-secure</code></li>
                    <li>把 flag 切到 <b>Enabled</b></li>
                    <li>下方文本框填：<code>{origin}</code></li>
                    <li>点底部「Relaunch」重启 Chrome</li>
                    <li>回这页应该就能用了</li>
                  </ol>
                  <p style={{ margin: 0, fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>
                    长期方案：给 infill 配 HTTPS（自签证书或 mkcert）。Chrome 把 localhost / HTTPS 视为安全，自动放行。
                  </p>
                </>
              )}
              {isChromium && isSecure && (
                <p style={{ margin: 0 }}>
                  奇怪——你在 Chromium 内核 + 安全上下文里却拿不到 <code>navigator.usb</code>。
                  可能是 Chrome 版本太旧（&lt;89）或者企业管控策略禁用了 WebUSB。检查
                  <code>chrome://policy/</code>。
                </p>
              )}
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', gap: 24, padding: '24px' }}>
      {/* 左侧 sticky 控制面板 */}
      <aside
        style={{
          flex: '0 0 320px',
          position: 'sticky',
          top: 24,
          alignSelf: 'flex-start',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* 连接状态 */}
        <div
          style={{
            background: '#fff',
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '14px 16px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: connected ? '#52c41a' : '#d9d9d9',
                display: 'inline-block',
              }}
            />
            <span style={{ fontWeight: 500 }}>
              {connected ? `已连接：${deviceName}` : '未连接手机'}
            </span>
          </div>
          {connected ? (
            <Button
              size="small"
              icon={<DisconnectOutlined />}
              onClick={handleDisconnect}
              disabled={finishing}
            >
              断开
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<ApiOutlined />}
              loading={connecting}
              onClick={handleConnect}
              disabled={otherInProgress}
              block
            >
              连接手机
            </Button>
          )}
        </div>

        {/* 操作说明 */}
        <div
          style={{
            background: '#fff',
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '12px 14px',
            fontSize: 12,
            color: 'rgba(0,0,0,0.65)',
            lineHeight: 1.7,
          }}
        >
          <b style={{ color: 'rgba(0,0,0,0.88)' }}>典型流程：</b>
          <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li>USB 线把手机插电脑（开了 USB 调试）</li>
            <li>点「连接手机」→ 浏览器弹设备列表，选你的手机</li>
            <li>手机弹「允许 USB 调试吗？」点允许</li>
            <li>闲鱼 App → 待发货 → 点开第 1 单详情</li>
            <li>点「截屏 (+1)」</li>
            <li>手机点返回 → 第 2 单详情 → 重复</li>
            <li>都截完点「完成截屏，开始解析」</li>
          </ol>
          <div
            style={{
              marginTop: 8,
              padding: '6px 8px',
              background: '#fffbe6',
              border: '1px solid #ffe58f',
              borderRadius: 4,
              fontSize: 11,
            }}
          >
            ⚠️ 截<b>详情页</b>不是列表页（详情页才有订单号 / 下单时间）
          </div>
        </div>

        {/* 计数 */}
        {captureCount > 0 && (
          <div
            style={{
              background: '#fff',
              border: '1px dashed #d9d9d9',
              borderRadius: 8,
              padding: '10px 14px',
              fontSize: 12,
              color: 'rgba(0,0,0,0.65)',
              textAlign: 'center',
            }}
          >
            已捕获{' '}
            <b style={{ fontSize: 18, color: 'rgba(0,0,0,0.88)' }}>{captureCount}</b> 张
          </div>
        )}

        {/* 主操作 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Tooltip title={!connected ? '请先连接手机' : ''}>
            <Button
              block
              size="large"
              type="primary"
              loading={screencapBusy}
              disabled={!canScreencap}
              onClick={handleScreencap}
              style={canScreencap ? { background: '#ff7a00', borderColor: '#ff7a00' } : undefined}
            >
              截屏 (+1)
            </Button>
          </Tooltip>
          <Button
            block
            size="large"
            type="primary"
            loading={finishing}
            disabled={!canFinish}
            onClick={handleFinish}
            style={canFinish ? { background: '#ff7a00', borderColor: '#ff7a00' } : undefined}
          >
            完成截屏，开始解析{captureCount > 0 ? ` (${captureCount} 张)` : ''}
          </Button>
          <Button block onClick={handleCancel} disabled={!canCancel}>
            取消
          </Button>
        </div>
      </aside>

      {/* 右侧主区 */}
      <section style={{ flex: 1, minWidth: 0 }}>
        {finishing && (
          <div style={{ textAlign: 'center', padding: 64 }}>
            <Spin />
            <div style={{ marginTop: 16, color: 'rgba(0,0,0,0.65)' }}>
              正在解析 {captureCount} 张详情页 …
            </div>
          </div>
        )}
        {!finishing && captureCount > 0 && batchId && (
          <ScreencapGrid
            batchId={batchId}
            screens={screens}
            parsedOrders={[]}
          />
        )}
        {!finishing && captureCount === 0 && (
          <div
            style={{
              padding: '64px 32px',
              textAlign: 'center',
              color: 'rgba(0,0,0,0.45)',
              fontSize: 13,
            }}
          >
            {connected
              ? '点左侧「截屏 (+1)」捕获第一张详情页'
              : '先连接手机，再开始截屏'}
          </div>
        )}
      </section>
    </div>
  );
}
