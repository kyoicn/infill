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
  dragAndDrop as webadbDragAndDrop,
  getScreenSize,
  isConnected,
  isWebUsbSupported,
  pressBack,
  screencap as webadbScreencap,
  tap as webadbTap,
} from '../../api/webadb';
import ScreencapGrid, { type ScreenEntry } from './ScreencapGrid';

const AUTO_SCAN_MAX = 50;
const TAP_TO_DETAIL_INITIAL_MS = 1500;   // tap 后先 sleep 这么久再开始轮询
const TAP_TO_DETAIL_MAX_MS = 10000;      // 等详情加载最大时长，超过抛错
const TAP_TO_DETAIL_LOADED_MIN_BYTES = 800_000;  // 加载完的详情页 PNG ≥ 1MB（含商品图 + 地址 + 多色文字）；
// 闲鱼 loading 页是淡灰背景 + 小 spinner，实测 ~500KB（RGBA + 渐变压不下去），
// 因此最低阈值不能低于 800KB，否则会把 loading 页误判成已加载。
const TAP_TO_DETAIL_POLL_MS = 500;
const TAP_TO_EXPAND_MS = 600;        // 「订单编号」点击 → 展开动画
const BACK_TO_LIST_MS = 900;         // back → 列表稳定
const SCROLL_SETTLE_MS = 2500;       // swipe → 列表稳定（含弹性回弹 / fling 余动）
const SCROLL_STEP_SLEEP_MS = 500;    // 多步 swipe 每步之间留时间让 ACTION_UP 不残留动量

// 🚨 详情页底部 12% 高危带：「联系买家 / 取消订单 / 去发货」固定操作栏的覆盖区。
//    只在详情页（展开按钮 tap）时启用——LLM 落到这区里立即中止。
// ⚠️ 列表页**不**用这个阈值——列表页底部没有固定操作栏，危险的是「每张卡片自身底部」
//    那一行 4 个按钮（更多/求小红花/联系买家/去发货），但卡中心 y 远高于这一行，LLM 只要
//    给到 product image 区就安全。列表卡片真正要过滤的是「被屏幕底部裁掉的不完整卡」
//    ——其中心估值不可靠，可能撞到按钮行。
const DANGER_ZONE_FRAC = 0.12;
const LIST_CARD_BOTTOM_MARGIN_PX = 40;   // 卡片底部到屏幕底部至少留这么多像素才算完整可见
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * 等详情页加载完——loading 时截屏只是大片白底 + 黄色 spinner（PNG 字节 < 100KB），
 * 加载完后是有图有文的详情页（PNG > 500KB）。轮询直到字节数超过阈值 AND 与上次相近（稳定），
 * 或到达最大等待时长。返回最后一张截屏 PNG。
 */
async function screencapDetailWhenLoaded(): Promise<Uint8Array> {
  await new Promise((r) => setTimeout(r, TAP_TO_DETAIL_INITIAL_MS));
  const deadline = Date.now() + TAP_TO_DETAIL_MAX_MS - TAP_TO_DETAIL_INITIAL_MS;
  let last = await webadbScreencap();
  while (Date.now() < deadline) {
    if (last.length >= TAP_TO_DETAIL_LOADED_MIN_BYTES) {
      // 已经长得像加载完的页面，再 sleep 一小段 + 复查稳定就返回
      await new Promise((r) => setTimeout(r, TAP_TO_DETAIL_POLL_MS));
      const cur = await webadbScreencap();
      const ratio = cur.length / last.length;
      if (ratio > 0.95 && ratio < 1.05) return cur;
      last = cur;
      continue;
    }
    // 像 loading 页 → 继续等
    await new Promise((r) => setTimeout(r, TAP_TO_DETAIL_POLL_MS));
    last = await webadbScreencap();
  }
  // 超时 — 把最后截到的回去让 LLM 自己看，反正它会返回 0/0 让上层报错
  return last;
}

/**
 * 列表向下滚动一格 ≈ 1 张卡片高度。用 Android 7+ 的 `input draganddrop`——这条命令明确告诉
 * 系统「这是一次拖拽，不是 swipe」，**不会触发 fling 惯性**，移动距离严格 = (sy) → (ey)。
 * `input swipe`（即使 1500ms 慢拖）在 Nubia 实测仍有惯性余动导致超滚 3-4 张。
 * 距离设 1.0 × cardHeightPx（仅 1 张卡，前后批必然 50%+ 重叠，后端 dedup 兜底，绝不漏单）。
 */
async function scrollListByOneCard(
  cardX: number,
  cardHeightPx: number,
  screenH: number,
  scrollFactor = 2.0,
): Promise<void> {
  // 默认滚 2 张卡的距离。一批扫 3 张 → 新批起点是旧批 card 3 → 仅 1 张重叠（被后端 dedup 静默丢）。
  // 滚 3 张才完全无重叠，但容错更脆弱（一旦 LLM 卡片高度估错就漏单），所以选 2× + 后端 dedup 兜底。
  const totalScrollDy = cardHeightPx > 0
    ? Math.round(cardHeightPx * scrollFactor)
    : Math.round(screenH * 0.45);
  const sy = Math.round(screenH * 0.75);
  const ey = Math.max(Math.round(screenH * 0.15), sy - totalScrollDy);
  await webadbDragAndDrop(cardX, sy, cardX, ey, 2500);
}

/** 详情页 tap 安全闸：屏幕底部 12% 一律拒绝（固定操作栏区）。 */
function assertSafeDetailTap(
  label: string,
  x: number,
  y: number,
  screenW: number,
  screenH: number,
): void {
  const dangerY = screenH - Math.round(screenH * DANGER_ZONE_FRAC);
  if (y >= dangerY) {
    throw new Error(
      `详情页安全闸触发：${label} y=${y} 落在屏幕底部 ${Math.round(DANGER_ZONE_FRAC * 100)}% 高危带` +
      `（≥${dangerY}），禁止点击（防止误碰「联系买家 / 取消订单 / 去发货」按钮）`,
    );
  }
  if (x < 0 || x > screenW || y < 0) {
    throw new Error(`详情页安全闸触发：${label} 坐标 (${x},${y}) 越界（屏幕 ${screenW}x${screenH}）`);
  }
}

/** 列表卡片 tap 安全闸：只检查坐标越界（"完整可见"过滤在 layout 阶段已做）。 */
function assertSafeListTap(
  label: string,
  x: number,
  y: number,
  screenW: number,
  screenH: number,
): void {
  if (x < 0 || x > screenW || y < 0 || y > screenH) {
    throw new Error(`列表安全闸触发：${label} 坐标 (${x},${y}) 越界（屏幕 ${screenW}x${screenH}）`);
  }
}

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
          // 列表卡过滤：丢掉被屏幕底部裁掉、不完整可见的卡（中心估值不可靠，可能撞到
          // 卡片自身的「去发货」按钮行）。判定：card_bottom = center_y + cardHeight/2
          // 必须 ≤ screenH - margin，否则视为不完整。
          // 注意：这里**不**用"屏幕底部 12% 高危带"——列表页底部没有固定操作栏。
          const halfH = cardHeightPx > 0 ? Math.round(cardHeightPx / 2) : Math.round(screenSize.h * 0.15);
          const maxFullyVisibleY = screenSize.h - LIST_CARD_BOTTOM_MARGIN_PX - halfH;
          const safe = layout.card_centers_y.filter((y) => y <= maxFullyVisibleY);
          const dropped = layout.card_centers_y.length - safe.length;
          if (dropped > 0) {
            // eslint-disable-next-line no-console
            console.log(
              `[xianyu auto-scan] dropped ${dropped} partial card(s) ` +
              `(y > ${maxFullyVisibleY}, halfH=${halfH}): ` +
              `${layout.card_centers_y.filter((y) => y > maxFullyVisibleY).join(',')}`,
            );
          }
          visibleCardYs = safe;
          if (visibleCardYs.length === 0) {
            // 当前可视区没有完整卡片（顶部 / 底部都被裁了？）→ 滚一格再试
            setAutoStatus('当前没有完整可视卡片，滚动一格…');
            await scrollListByOneCard(cardX, cardHeightPx, screenSize.h);
            await sleep(SCROLL_SETTLE_MS);
            continue;
          }
        }

        if (autoAbortRef.current) break;

        // —— 2. tap 列表卡片 → 轮询等详情页加载完 ——
        const targetY = visibleCardYs.shift()!;
        // 🚨 安全闸：坐标落在底部高危带就立刻终止
        assertSafeListTap(`卡片 #${scanned + 1}`, cardX, targetY, screenSize.w, screenSize.h);
        setAutoStatus(`点开第 ${scanned + 1} 单（共 ${autoN}）…`);
        await webadbTap(cardX, targetY);
        setAutoStatus(`等第 ${scanned + 1} 单详情加载…`);
        const detailPng = await screencapDetailWhenLoaded();
        if (autoAbortRef.current) {
          await pressBack();
          break;
        }

        // —— 3. 每单都重新识别「订单编号」展开按钮 ——
        //    地址行数 / 商品标题长度 / 商品个数都会让这一行 y 上下漂移几十~几百像素，
        //    复用第一单的坐标会点到空白处不触发展开（实测：第 2、3 单复用结果）。
        setAutoStatus(`识别第 ${scanned + 1} 单「订单编号」展开按钮…`);
        const expand = await api.autoImport.xianyu.detectExpandButton(detailPng);
        if (!expand.ok || (expand.x === 0 && expand.y === 0)) {
          // 不主动 pressBack — 如果之前 tap 其实没点中卡片（手机还在列表页），
          // 这一下 back 会直接退出闲鱼。让用户手动回到列表页更安全。
          throw new Error(
            `「订单编号」按钮识别失败：${expand.error ?? expand.error_kind ?? '坐标返回 0/0'}` +
            ` — 请手动检查手机现在是哪一页（详情页就手动 back 回列表，列表页可能 LLM 把卡片中心估歪了）`,
          );
        }
        // 🚨 安全闸：展开按钮 y 落在底部高危带也拒绝（防 LLM 把「去发货」黄按钮误识成展开箭头）
        assertSafeDetailTap('「订单编号」展开按钮', expand.x, expand.y, screenSize.w, screenSize.h);

        // —— 4. tap 展开 + 截屏 + 上传 ——
        setAutoStatus(`展开 + 截屏第 ${scanned + 1} 单…`);
        await webadbTap(expand.x, expand.y);
        await sleep(TAP_TO_EXPAND_MS);
        const finalPng = await webadbScreencap();
        const upRes = await api.autoImport.xianyu.uploadScreencap(currentBatch, finalPng);
        if (!upRes.ok) {
          if (upRes.error_kind === 'duplicate') {
            // 后端识别出和已上传某张完全一致 → 这一卡是上批扫过的同一单（前后批的天然重叠）。
            // 不要在这里 scroll —— 当前 visibleCardYs 里剩下的卡是新的，先把它们扫掉；
            // 等可视卡都消耗光了再 scroll，避免漏掉夹在 dup 后面的卡（实测：屏幕显示 3/4/5
            // 时 card 3 dup，如果立刻滚就会变成 5/6/7，错过 card 4）。
            // eslint-disable-next-line no-console
            console.log(`[xianyu auto-scan] dup detected (matches seq=${upRes.seq}), consume next card (no extra scroll)`);
            message.info(`第 ${scanned + 1} 张和 seq=${upRes.seq} 同款（前后批天然重叠），跳过本卡继续下一张`);
            await pressBack();
            await sleep(BACK_TO_LIST_MS);
            // 不增加 scanned —— 这单不算扫到；不清空 visibleCardYs —— 让循环继续消耗下一张
            continue;
          }
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

        // —— 6. 可视卡片消耗光 → 滚一格，让下一批进入视野 ——
        if (visibleCardYs.length === 0 && scanned < autoN && !autoAbortRef.current) {
          setAutoStatus('向下滚动列表（一格）…');
          await scrollListByOneCard(cardX, cardHeightPx, screenSize.h);
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
              <div
                style={{
                  marginBottom: 10,
                  padding: '8px 10px',
                  background: '#fff7e6',
                  border: '1px solid #ffd591',
                  borderRadius: 6,
                  fontSize: 11,
                  color: 'rgba(0,0,0,0.75)',
                  lineHeight: 1.55,
                }}
              >
                ⚠️ 开始前请把手机停在闲鱼「<b>卖出 - 待发货</b>」列表的<b>第一屏</b>
                （最新单在最上面）。程序会从顶部往下逐单扫描；中途切到别的页面会失败。
              </div>
              <div
                style={{
                  marginBottom: 10,
                  padding: '8px 10px',
                  background: '#fff1f0',
                  border: '1px solid #ffa39e',
                  borderRadius: 6,
                  fontSize: 11,
                  color: '#a8071a',
                  lineHeight: 1.55,
                }}
              >
                🚨 双重安全闸：① <b>详情页</b>底部
                {Math.round(DANGER_ZONE_FRAC * 100)}% 一律拒绝点击（防误碰
                「联系买家 / 取消订单 / 去发货」按钮）；② <b>列表页</b>只点完整可见的卡片中心，
                被屏幕底部裁掉的卡片自动跳过 + 滚动后再扫。
              </div>
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

        {/* 手动截屏（备用） — 把「截屏 (+1)」按钮放进卡片内，让它和卡片绑定，
            避免和后面"通用：解析/取消"按钮视觉粘连。 */}
        <div
          style={{
            background: '#fff',
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '12px 14px',
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: 'rgba(0,0,0,0.65)',
              lineHeight: 1.7,
              marginBottom: 10,
            }}
          >
            <b style={{ color: 'rgba(0,0,0,0.88)' }}>手动截屏（备用）：</b>
            <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              <li>闲鱼 App → 待发货 → 点开第 1 单详情</li>
              <li>点下方「截屏 (+1)」</li>
              <li>手机点返回 → 第 2 单详情 → 重复</li>
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
          <Tooltip title={!connected ? '请先连接手机' : ''}>
            <Button
              block
              size="small"
              type="primary"
              loading={screencapBusy}
              disabled={!canScreencap}
              onClick={handleScreencap}
              style={canScreencap ? { background: '#ff7a00', borderColor: '#ff7a00' } : undefined}
            >
              截屏 (+1)
            </Button>
          </Tooltip>
        </div>

        {/* 通用：本批操作（自动 / 手动都用） — 用粗实线 + 不同背景视觉分离 */}
        <div
          style={{
            background: '#fafafa',
            border: '2px solid #d9d9d9',
            borderRadius: 8,
            padding: '12px 14px',
            marginTop: 4,
          }}
        >
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: 'rgba(0,0,0,0.65)',
              marginBottom: 8,
              letterSpacing: '0.5px',
            }}
          >
            本批操作（自动 / 手动通用）
          </div>
          {captureCount > 0 && (
            <div
              style={{
                fontSize: 12,
                color: 'rgba(0,0,0,0.65)',
                textAlign: 'center',
                padding: '6px 0',
                marginBottom: 8,
                background: '#fff',
                border: '1px dashed #d9d9d9',
                borderRadius: 4,
              }}
            >
              已捕获{' '}
              <b style={{ fontSize: 18, color: 'rgba(0,0,0,0.88)' }}>{captureCount}</b> 张
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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
              取消（清空本批）
            </Button>
          </div>
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
