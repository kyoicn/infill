/**
 * Chrome 扩展通信封装 — 通过 externally_connectable + chrome.runtime.sendMessage。
 * Extension ID 来自 VITE_INFILL_EXT_ID 构建时环境变量（frontend/.env）。
 */

import type { AutoImportScanResponse } from './client';

// tsconfig.app.json 的 types 数组只白名单了 vite/client，所以即便安装了
// @types/chrome 也不会自动注入 chrome 全局。这里手写最小声明，保证类型自洽。
interface ChromeLastError {
  message?: string;
}
interface ChromeRuntime {
  lastError?: ChromeLastError;
  sendMessage: (
    extId: string,
    msg: unknown,
    cb: (response: unknown) => void,
  ) => void;
}
interface ChromeGlobal {
  runtime: ChromeRuntime;
}
declare const chrome: ChromeGlobal;

const EXT_ID: string | undefined = import.meta.env.VITE_INFILL_EXT_ID;

export interface ExtensionPingResponse {
  ok: boolean;
  version?: string;
  error_kind?: string;
}

export interface ExtensionScrapeResponse {
  ok: boolean;
  scan_response?: AutoImportScanResponse;
  error_kind?: string;
}

function hasChrome(): boolean {
  // chrome 全局只在装了扩展的 Chromium 系列浏览器里存在
  return (
    typeof window !== 'undefined' &&
    typeof (window as { chrome?: unknown }).chrome !== 'undefined'
  );
}

export function getExtensionId(): string | undefined {
  return EXT_ID;
}

export async function pingExtension(timeoutMs = 2000): Promise<ExtensionPingResponse> {
  if (!EXT_ID) return { ok: false, error_kind: 'no_ext_id' };
  if (!hasChrome()) return { ok: false, error_kind: 'no_chrome_runtime' };
  return new Promise<ExtensionPingResponse>((resolve) => {
    const timer = setTimeout(
      () => resolve({ ok: false, error_kind: 'timeout' }),
      timeoutMs,
    );
    try {
      chrome.runtime.sendMessage(
        EXT_ID,
        { action: 'ping' },
        (response: unknown) => {
          clearTimeout(timer);
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error_kind: 'extension_not_found' });
            return;
          }
          if (response && typeof response === 'object') {
            resolve(response as ExtensionPingResponse);
            return;
          }
          resolve({ ok: false, error_kind: 'no_response' });
        },
      );
    } catch {
      clearTimeout(timer);
      resolve({ ok: false, error_kind: 'send_failed' });
    }
  });
}

export async function scrapeXhs(batchId: string): Promise<ExtensionScrapeResponse> {
  if (!EXT_ID) return { ok: false, error_kind: 'no_ext_id' };
  if (!hasChrome()) return { ok: false, error_kind: 'no_chrome_runtime' };
  return new Promise<ExtensionScrapeResponse>((resolve) => {
    try {
      chrome.runtime.sendMessage(
        EXT_ID,
        { action: 'scrape_xhs', batch_id: batchId },
        (response: unknown) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error_kind: 'extension_not_found' });
            return;
          }
          if (response && typeof response === 'object') {
            resolve(response as ExtensionScrapeResponse);
            return;
          }
          resolve({ ok: false, error_kind: 'no_response' });
        },
      );
    } catch {
      resolve({ ok: false, error_kind: 'send_failed' });
    }
  });
}
