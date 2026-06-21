import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, InputNumber, Spin, Tooltip, message } from 'antd';
import { ApiOutlined, DisconnectOutlined, ThunderboltOutlined } from '@ant-design/icons';
import {
  api,
  type AutoImportScanResponse,
} from '../../api/client';
import {
  connectPhone,
  currentDeviceName,
  disconnectPhone,
  getScreenSize,
  isConnected,
  isWebUsbSupported,
  pressBack,
  screencap as webadbScreencap,
  swipe as webadbSwipe,
  tap as webadbTap,
} from '../../api/webadb';
import ScreencapGrid, { type ScreenEntry } from './ScreencapGrid';

const AUTO_SCAN_MAX = 50;
const TAP_TO_DETAIL_MS = 2200;       // 列表 tap → 详情页 loading 完成
const TAP_TO_EXPAND_MS = 500;        // 「订单编号」点击 → 展开动画
const BACK_TO_LIST_MS = 700;         // back → 列表稳定
const SCROLL_SETTLE_MS = 800;        // swipe → 列表稳定
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

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
  const [autoN, setAutoN] = useState<number>(10);
  const [autoRunning, setAutoRunning] = useState(false);
  const [autoProgress, setAutoProgress] = useState<number>(0);
  const [autoStatus, setAutoStatus] = useState<string>('');
  const autoAbortRef = useRef<boolean>(false);

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

  const handleDeleteScreen = useCallback(
    async (seq: number) => {
      if (!batchId) return;
      try {
        const resp = await api.autoImport.xianyu.deleteScreen(batchId, seq);
        if (!resp.ok) {
          message.error(`删除失败：${resp.error_kind ?? '未知错误'}`);
          return;
        }
        setScreens((prev) => prev.filter((s) => s.seq !== seq));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        message.error(`删除失败：${msg}`);
      }
    },
    [batchId],
  );

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

  const handleAutoAbort = useCallback(() => {
    autoAbortRef.current = true;
    setAutoStatus('正在中止…');
  }, []);

  /**
   * 自动扫描 N 单：
   *   tap 列表卡片 → 等详情页 loading → tap「订单编号」展开 → 截屏 → back → 下一单。
   *   首次进入详情页通过 LLM 拿展开按钮坐标，之后整轮缓存；卡片坐标每批可视范围都重新喂 LLM。
   */
  const handleAutoScan = useCallback(async () => {
    if (!isConnected() || autoRunning || finishing || screencapBusy) return;
    if (!autoN || autoN < 1) {
      message.warning('请填一个正整数');
      return;
    }

    autoAbortRef.current = false;
    setAutoRunning(true);
    setAutoProgress(0);
    setAutoStatus('准备中…');

    // 复用已有 batchId，方便和手动截屏混用
    let currentBatch = batchId;
    if (!currentBatch) {
      currentBatch =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `xy-${Date.now()}`;
      setBatchId(currentBatch);
    }

    try {
      const screenSize = await getScreenSize();

      let cardX = Math.round(screenSize.w / 2);
      let cardHeightPx = 0;
      let visibleCardYs: number[] = [];
      let expandX = 0;
      let expandY = 0;
      let scanned = 0;

      while (scanned < autoN && !autoAbortRef.current) {
        // —— 1. 没有可视卡片队列时，截屏 → LLM 拿坐标 ——
        if (visibleCardYs.length === 0) {
          setAutoStatus(`截屏识别列表布局（已扫 ${scanned}/${autoN}）…`);
          const listPng = await webadbScreencap();
          const layout = await api.autoImport.xianyu.detectListLayout(listPng);
          if (!layout.ok) {
            throw new Error(`列表布局识别失败：${layout.error ?? layout.error_kind ?? '未知错误'}`);
          }
          if (!layout.card_centers_y || layout.card_centers_y.length === 0) {
            message.info(
              scanned === 0
                ? '当前画面识别不到待发货卡片，请先在手机上打开「卖出 - 待发货」页'
                : `已扫到列表底部，共完成 ${scanned} 单`,
            );
            break;
          }
          if (layout.card_x > 0) cardX = layout.card_x;
          if (layout.card_height_px > 0) cardHeightPx = layout.card_height_px;
          visibleCardYs = [...layout.card_centers_y];
        }

        if (autoAbortRef.current) break;

        // —— 2. tap 列表卡片 → 等详情页 ——
        const targetY = visibleCardYs.shift()!;
        setAutoStatus(`点开第 ${scanned + 1} 单（共 ${autoN}）…`);
        await webadbTap(cardX, targetY);
        await sleep(TAP_TO_DETAIL_MS);
        if (autoAbortRef.current) {
          await pressBack();
          break;
        }

        // —— 3. 首次进入详情：LLM 找「订单编号」按钮坐标 ——
        if (expandX === 0 && expandY === 0) {
          setAutoStatus('识别「订单编号」展开按钮…');
          const detailPng = await webadbScreencap();
          const expand = await api.autoImport.xianyu.detectExpandButton(detailPng);
          if (!expand.ok || (expand.x === 0 && expand.y === 0)) {
            // back 出去以免卡在详情页
            await pressBack();
            throw new Error(
              `「订单编号」按钮识别失败：${expand.error ?? expand.error_kind ?? '坐标返回 0/0'}`,
            );
          }
          expandX = expand.x;
          expandY = expand.y;
        }

        // —— 4. tap 展开 + 截屏 + 上传 ——
        setAutoStatus(`展开 + 截屏第 ${scanned + 1} 单…`);
        await webadbTap(expandX, expandY);
        await sleep(TAP_TO_EXPAND_MS);
        const finalPng = await webadbScreencap();
        const upRes = await api.autoImport.xianyu.uploadScreencap(currentBatch, finalPng);
        if (!upRes.ok) {
          await pressBack();
          throw new Error(`上传截屏失败：${upRes.error ?? upRes.error_kind ?? '未知'}`);
        }
        const seq = upRes.seq ?? scanned;
        setScreens((prev) =>
          prev.some((s) => s.seq === seq)
            ? prev
            : [...prev, { seq, status: 'captured' as const, error: null }],
        );

        // —— 5. back 回列表 ——
        await pressBack();
        await sleep(BACK_TO_LIST_MS);

        scanned += 1;
        setAutoProgress(scanned);

        // —— 6. 可视卡片消耗光 → 滚一段，让下一批进入视野 ——
        if (visibleCardYs.length === 0 && scanned < autoN && !autoAbortRef.current) {
          const scrollDy = cardHeightPx > 0 ? cardHeightPx * 3 : Math.round(screenSize.h * 0.6);
          const startY = Math.round(screenSize.h * 0.8);
          const endY = Math.max(Math.round(screenSize.h * 0.15), startY - scrollDy);
          setAutoStatus('向下滑动列表…');
          await webadbSwipe(cardX, startY, cardX, endY, 400);
          await sleep(SCROLL_SETTLE_MS);
        }
      }

      if (autoAbortRef.current) {
        message.info(`已中止，保留前 ${scanned} 单截屏`);
      } else if (scanned > 0) {
        message.success(`自动扫描完成：${scanned} 单`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error('[xianyu auto-scan] failed', err);
      message.error(`自动扫描失败：${msg}`);
    } finally {
      setAutoRunning(false);
      setAutoStatus('');
      setAutoProgress(0);
      autoAbortRef.current = false;
    }
  }, [autoN, autoRunning, finishing, screencapBusy, batchId]);

  // 卸载时断开
  useEffect(() => {
    return () => {
      // 不主动断开 — 用户切到 xhs tab 回来时还能用
    };
  }, []);

  const captureCount = screens.length;
  const canScreencap = connected && !otherInProgress && !screencapBusy && !finishing && !autoRunning;
  const canFinish = connected && captureCount >= 1 && !finishing && !autoRunning;
  const canCancel = captureCount >= 1 && !finishing && !autoRunning;
  const canAutoScan =
    connected && !otherInProgress && !screencapBusy && !finishing && !autoRunning && autoN >= 1;

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

        {/* 自动扫描（推荐） */}
        <div
          style={{
            background: '#fff',
            border: '1px solid #ffd591',
            borderRadius: 8,
            padding: '12px 14px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <ThunderboltOutlined style={{ color: '#ff7a00' }} />
            <b style={{ color: 'rgba(0,0,0,0.88)', fontSize: 13 }}>自动扫描（推荐）</b>
          </div>
          {!autoRunning ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.65)' }}>扫描前</span>
                <InputNumber
                  size="small"
                  min={1}
                  max={AUTO_SCAN_MAX}
                  value={autoN}
                  onChange={(v) => setAutoN(typeof v === 'number' ? v : 10)}
                  style={{ width: 64 }}
                  disabled={!canAutoScan && !autoRunning}
                />
                <span style={{ fontSize: 12, color: 'rgba(0,0,0,0.65)' }}>单</span>
              </div>
              <Tooltip
                title={
                  !connected
                    ? '先连接手机'
                    : !canAutoScan
                      ? '当前不能开始'
                      : '手机停在「卖出 - 待发货」列表第一屏，点击开始'
                }
              >
                <Button
                  block
                  size="small"
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  disabled={!canAutoScan}
                  onClick={handleAutoScan}
                  style={canAutoScan ? { background: '#ff7a00', borderColor: '#ff7a00' } : undefined}
                >
                  开始自动扫描
                </Button>
              </Tooltip>
              <div
                style={{
                  marginTop: 8,
                  fontSize: 11,
                  color: 'rgba(0,0,0,0.45)',
                  lineHeight: 1.6,
                }}
              >
                先把手机停在闲鱼「卖出 - 待发货」列表的第一屏，剩下交给电脑：识别卡片 → 点开
                → 展开订单编号 → 截屏 → 返回 → 滚动 → 下一单。
              </div>
            </>
          ) : (
            <>
              <div
                style={{
                  fontSize: 12,
                  color: 'rgba(0,0,0,0.65)',
                  marginBottom: 4,
                  minHeight: 18,
                }}
              >
                {autoStatus || '运行中…'}
              </div>
              <div style={{ fontSize: 13, marginBottom: 8 }}>
                进度 <b>{autoProgress}</b> / {autoN}
              </div>
              <Button block size="small" danger onClick={handleAutoAbort}>
                中止自动扫描
              </Button>
            </>
          )}
        </div>

        {/* 操作说明（手动备用） */}
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
          <b style={{ color: 'rgba(0,0,0,0.88)' }}>手动截屏（备用）：</b>
          <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li>闲鱼 App → 待发货 → 点开第 1 单详情</li>
            <li>点下方「截屏 (+1)」</li>
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
            onDelete={handleDeleteScreen}
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
