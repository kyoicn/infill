/**
 * WebUSB-based ADB client — browser 端直接讲 ADB 协议跟 USB 连接的 Android 手机通信，
 * 无需后端跑 adb 子进程，无需 Windows/Mac/Linux 上装额外 helper。
 *
 * 依赖 @yume-chan/adb 系列包 + 浏览器 WebUSB API（Chrome/Edge 支持，Firefox 不支持）。
 *
 * 使用方式：
 *   import { connectPhone, screencap, disconnectPhone } from './webadb';
 *   await connectPhone();          // 弹浏览器 USB 设备选择器
 *   const png = await screencap(); // Uint8Array, 是真实 PNG 字节
 *   await disconnectPhone();
 */

import { Adb, AdbDaemonTransport } from '@yume-chan/adb';
import type { AdbCredentialStore } from '@yume-chan/adb';
import { AdbDaemonWebUsbDeviceManager } from '@yume-chan/adb-daemon-webusb';

// ===== 凭证：用 localStorage + WebCrypto 持久化 RSA 私钥 =====

const LS_PRIVKEY = 'infill.adb.privkey.pkcs8.b64';

async function generateAndStore(): Promise<{ buffer: Uint8Array; name: string }> {
  // ADB 用 RSA-2048 + SHA-1 签名
  const keyPair = await crypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-1',
    },
    true,
    ['sign'],
  );
  const pkcs8 = await crypto.subtle.exportKey('pkcs8', keyPair.privateKey);
  const buf = new Uint8Array(pkcs8);
  // base64 编码存
  const b64 = btoa(String.fromCharCode(...buf));
  window.localStorage.setItem(LS_PRIVKEY, b64);
  return { buffer: buf, name: 'infill' };
}

function loadStored(): { buffer: Uint8Array; name: string } | null {
  const b64 = window.localStorage.getItem(LS_PRIVKEY);
  if (!b64) return null;
  try {
    const buf = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return { buffer: buf, name: 'infill' };
  } catch {
    return null;
  }
}

const credentialStore: AdbCredentialStore = {
  async generateKey() {
    return generateAndStore();
  },
  iterateKeys() {
    const k = loadStored();
    return (function* () {
      if (k) yield k;
    })();
  },
};

// ===== 单例连接 =====

interface PhoneConnection {
  adb: Adb;
  deviceName: string;
  serial: string;
}

let current: PhoneConnection | null = null;

export function isConnected(): boolean {
  return current !== null;
}

export function currentDeviceName(): string | null {
  return current?.deviceName ?? null;
}

export function isWebUsbSupported(): boolean {
  return typeof navigator !== 'undefined'
    && 'usb' in navigator
    && AdbDaemonWebUsbDeviceManager.BROWSER !== undefined;
}

/**
 * 弹浏览器 USB 设备选择器，让用户挑手机。挑完后跑 ADB 握手 + 鉴权。
 * 首次连接时手机会弹「允许通过 USB 调试吗？」对话框，用户必须点允许。
 */
export async function connectPhone(): Promise<{ deviceName: string; serial: string }> {
  if (!isWebUsbSupported()) {
    throw new Error('当前浏览器不支持 WebUSB（请用 Chrome / Edge）');
  }
  if (current) {
    // 已连接：直接返回
    return { deviceName: current.deviceName, serial: current.serial };
  }
  console.log('[webadb] step 1: get manager');
  const manager = AdbDaemonWebUsbDeviceManager.BROWSER!;
  console.log('[webadb] step 2: request device (浏览器会弹 USB 选择器)');
  const device = await manager.requestDevice();
  if (!device) {
    throw new Error('未选择设备');
  }
  console.log('[webadb] step 3: device picked', { serial: device.serial, name: device.name });
  console.log('[webadb] step 4: open USB connection');
  let connection;
  try {
    connection = await device.connect();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // Mac/Linux 上后台 adb server 会一直占着 USB 设备，Chrome 抢不到
    if (/already in use|busy|claim/i.test(msg)) {
      throw new Error(
        '手机被本机的 adb 守护进程占用了。请在终端执行 `adb kill-server` 后再点连接（或在 Windows 任务管理器结束 adb.exe）。'
      );
    }
    throw err;
  }
  console.log('[webadb] step 5: ADB authenticate handshake (手机会弹"允许 USB 调试")');
  const transport = await AdbDaemonTransport.authenticate({
    serial: device.serial,
    connection,
    credentialStore,
  });
  console.log('[webadb] step 6: instantiate Adb');
  const adb = new Adb(transport);
  console.log('[webadb] step 7: done', { product: transport.banner.product });
  // banner.product 是手机的 ro.product.name (如 "raven" / "OnePlus9Pro")
  const deviceName = transport.banner.product || device.name || 'Android 设备';
  current = { adb, deviceName, serial: device.serial };
  return { deviceName, serial: device.serial };
}

export async function disconnectPhone(): Promise<void> {
  if (!current) return;
  try {
    await current.adb.close();
  } catch {
    // 忽略：手机可能已经拔掉
  }
  current = null;
}

/**
 * 调 `screencap -p` 直出 PNG 字节。需要 shellProtocol（Android ≥ 7 基本都支持）。
 */
export async function screencap(): Promise<Uint8Array> {
  if (!current) throw new Error('未连接手机');
  const shell = current.adb.subprocess.shellProtocol;
  if (!shell || !shell.isSupported) {
    throw new Error('设备 ADB 太旧不支持 shellProtocol — 请用 Android 7+');
  }
  const result = await shell.spawnWait(['screencap', '-p']);
  if (result.exitCode !== 0) {
    const err = new TextDecoder().decode(result.stderr);
    throw new Error(`screencap 失败 (exit ${result.exitCode}): ${err || '(无 stderr)'}`);
  }
  return result.stdout;
}

// ===== 自动化原语 =====
// 全自动扫描需要：tap / swipe / back / 读屏幕尺寸。
// 都走 `input` / `wm` 系统命令，Android 4+ 都有。

async function shellRun(argv: string[]): Promise<string> {
  if (!current) throw new Error('未连接手机');
  const shell = current.adb.subprocess.shellProtocol;
  if (!shell || !shell.isSupported) {
    throw new Error('设备 ADB 太旧不支持 shellProtocol — 请用 Android 7+');
  }
  const result = await shell.spawnWait(argv);
  if (result.exitCode !== 0) {
    const err = new TextDecoder().decode(result.stderr);
    throw new Error(`${argv.join(' ')} 失败 (exit ${result.exitCode}): ${err || '(无 stderr)'}`);
  }
  return new TextDecoder().decode(result.stdout);
}

export async function tap(x: number, y: number): Promise<void> {
  await shellRun(['input', 'tap', String(Math.round(x)), String(Math.round(y))]);
}

export async function swipe(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  durationMs = 300,
): Promise<void> {
  await shellRun([
    'input',
    'swipe',
    String(Math.round(x1)),
    String(Math.round(y1)),
    String(Math.round(x2)),
    String(Math.round(y2)),
    String(Math.round(durationMs)),
  ]);
}

export async function pressBack(): Promise<void> {
  // KEYCODE_BACK = 4
  await shellRun(['input', 'keyevent', '4']);
}

export async function getScreenSize(): Promise<{ w: number; h: number }> {
  const out = await shellRun(['wm', 'size']);
  // 典型输出："Physical size: 1080x2340\n" 或 "Physical size: 1080x2340\nOverride size: 1080x2340"
  // Override 是用户自定义分辨率，优先用它（实际渲染就是这个尺寸）。
  const physical = out.match(/Physical size:\s*(\d+)x(\d+)/);
  const override = out.match(/Override size:\s*(\d+)x(\d+)/);
  const m = override || physical;
  if (!m) {
    throw new Error(`wm size 输出无法解析：${out.slice(0, 200)}`);
  }
  return { w: parseInt(m[1], 10), h: parseInt(m[2], 10) };
}
