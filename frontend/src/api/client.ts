const BASE = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败: ${res.status}`);
  }
  return res.json();
}

// ============================================================================
// 打印机状态相关类型（prd-007 / printer-status）
// ============================================================================

export type PrinterState = 'running' | 'pause' | 'idle' | 'offline';
export type PrinterStateOrUnconfigured = PrinterState | 'unconfigured';

export interface TimelineSegment {
  start_minute: number;
  end_minute: number;
  state: PrinterState;
}

export interface PrinterStatusSnapshot {
  printer_id: number;
  name: string;
  state: PrinterStateOrUnconfigured;
  today_working_minutes: number;
  today_total_minutes: number;
  last_state_change_ts: string | null;
  timeline: TimelineSegment[];
}

export interface PrinterStatusEvent {
  type: 'state_change';
  printer_id: number;
  state: PrinterState;
  ts: string;
}

export interface PrinterStatusPing {
  type: 'ping';
  ts?: string;
}

export interface HistoryDay {
  date: string; // "YYYY-MM-DD"
  working_minutes: number;
}

export interface PrinterHistoryOut {
  printer_id: number;
  name: string;
  history: HistoryDay[]; // 升序，今天在最后
}

export type PrinterWSMessage = PrinterStatusEvent | PrinterStatusPing;

export interface Printer {
  id: number;
  name: string;
  ip: string | null;
  serial: string | null;
  access_code_masked: string | null;
}

export type PrinterUpdateBody = Partial<{
  name: string;
  ip: string;
  serial: string;
  access_code: string;
}>;

export const api = {
  // 目录（只读，数据源为 catalog.yaml）
  getComponents: () => request<any[]>('/components'),
  getProducts: () => request<any[]>('/products'),
  getAllConfigs: () => request<any[]>('/components/configs/all'),
  reloadCatalog: () => request<any>('/catalog/reload', { method: 'POST' }),

  // 订单
  getOrders: (status?: string) => request<any[]>(`/orders${status ? `?status=${status}` : ''}`),
  createOrder: (data: any) => request<any>('/orders', { method: 'POST', body: JSON.stringify(data) }),
  updateOrder: (id: number, data: { items?: any[]; notes?: string }) => request<any>(`/orders/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  shipOrder: (id: number) => request<any>(`/orders/${id}/ship`, { method: 'POST' }),
  deleteOrder: (id: number) => request<any>(`/orders/${id}`, { method: 'DELETE' }),

  // 库存
  getInventory: () => request<any[]>('/inventory'),
  adjustInventory: (data: any) => request<any>('/inventory/adjust', { method: 'POST', body: JSON.stringify(data) }),
  setInventory: (inventoryId: number, data: { component_id: number; color?: string; quantity: number }) => request<any>(`/inventory/${inventoryId}`, { method: 'PUT', body: JSON.stringify(data) }),
  getSurplus: () => request<any[]>('/inventory/surplus'),

  // 打印机
  getPrinters: () => request<Printer[]>('/printers'),
  createPrinter: (data: any) => request<any>('/printers', { method: 'POST', body: JSON.stringify(data) }),
  updatePrinter: (id: number, body: PrinterUpdateBody) =>
    request<Printer>(`/printers/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deletePrinter: (id: number) => request<any>(`/printers/${id}`, { method: 'DELETE' }),
  getPrinterStatusSnapshot: () => request<PrinterStatusSnapshot[]>('/printers/status/snapshot'),
  getPrinterUtilizationHistory: (days = 7) =>
    request<PrinterHistoryOut[]>(`/printers/utilization/history?days=${days}`),

  // 配置
  getScheduleConfigs: () => request<any[]>('/config/schedule'),
  upsertScheduleConfig: (dow: number, data: any) => request<any>(`/config/schedule/${dow}`, { method: 'PUT', body: JSON.stringify(data) }),
  getSystemConfigs: () => request<any[]>('/config/system'),
  upsertSystemConfig: (key: string, data: any) => request<any>(`/config/system/${key}`, { method: 'PUT', body: JSON.stringify(data) }),
  resetDatabase: () => request<any>('/config/reset-db', { method: 'POST' }),

  // 排班
  getPlans: () => request<any[]>('/schedule/plans'),
  getPlan: (id: number) => request<any>(`/schedule/plans/${id}`),
  generatePlan: (data: any) => request<any>('/schedule/generate', { method: 'POST', body: JSON.stringify(data) }),
  confirmPlan: (id: number) => request<any>(`/schedule/plans/${id}/confirm`, { method: 'POST' }),
  deletePlan: (id: number) => request<any>(`/schedule/plans/${id}`, { method: 'DELETE' }),
  deleteTask: (id: number) => request<any>(`/schedule/tasks/${id}`, { method: 'DELETE' }),
  replaceTaskConfig: (taskId: number, configId: number) => request<any>(`/schedule/tasks/${taskId}/config/${configId}`, { method: 'PUT' }),
  deleteBatch: (id: number) => request<any>(`/schedule/batches/${id}`, { method: 'DELETE' }),
  startBatch: (id: number, actualTime: string) => request<any>(`/schedule/batches/${id}/start`, { method: 'POST', body: JSON.stringify({ actual_time: actualTime }) }),
  completeTask: (id: number) => request<any>(`/schedule/tasks/${id}/complete`, { method: 'POST' }),
  cancelTask: (id: number) => request<any>(`/schedule/tasks/${id}/cancel`, { method: 'POST' }),
  failTask: (id: number) => request<any>(`/schedule/tasks/${id}/fail`, { method: 'POST' }),

  // 产品录入
  intake: {
    providerStatus: () => request<any>('/intake/provider-status'),
    upload: async (files: File[], sessionId?: string | null) => {
      const fd = new FormData();
      for (const f of files) fd.append('files', f);
      if (sessionId) fd.append('session_id', sessionId);
      const res = await fetch('/api/intake/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      return res.json();
    },
    recognize: (body: any, signal?: AbortSignal) =>
      request<any>('/intake/recognize', { method: 'POST', body: JSON.stringify(body), signal }),
    merge: (body: any) =>
      request<any>('/intake/merge', { method: 'POST', body: JSON.stringify(body) }),
    recentLogs: (lines = 100) =>
      request<any>(`/intake/recent-logs?lines=${lines}`),
  },

  // 目录 CRUD（写操作走 /catalog/*；路径键为 SKU，永久标识符）
  catalog: {
    addComponent: (body: any) =>
      request<any>('/catalog/components', { method: 'POST', body: JSON.stringify(body) }),
    updateComponent: (sku: string, body: any) =>
      request<any>(`/catalog/components/${encodeURIComponent(sku)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deleteComponent: (sku: string) =>
      request<any>(`/catalog/components/${encodeURIComponent(sku)}`, { method: 'DELETE' }),
    addPlate: (body: any) =>
      request<any>('/catalog/plates', { method: 'POST', body: JSON.stringify(body) }),
    updatePlate: (sku: string, body: any) =>
      request<any>(`/catalog/plates/${encodeURIComponent(sku)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deletePlate: (sku: string) =>
      request<any>(`/catalog/plates/${encodeURIComponent(sku)}`, { method: 'DELETE' }),
    addProduct: (body: any) =>
      request<any>('/catalog/products', { method: 'POST', body: JSON.stringify(body) }),
    updateProduct: (sku: string, body: any) =>
      request<any>(`/catalog/products/${encodeURIComponent(sku)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deleteProduct: (sku: string) =>
      request<any>(`/catalog/products/${encodeURIComponent(sku)}`, { method: 'DELETE' }),
    migrateToSku: () =>
      request<any>('/catalog/migrate-to-sku', { method: 'POST' }),
  },

  // 自动导入（小红书 + 闲鱼）
  autoImport: {
    xhs: {
      extensionStatus: () =>
        request<AutoImportExtensionStatus>('/auto-import/xhs/extension-status'),
      probe: () =>
        request<{ ok: boolean; has_xhs_tab: boolean }>('/auto-import/xhs/probe', { method: 'POST' }),
      scan: (payload: { batch_id: string; raw_orders: unknown[] }) =>
        request<AutoImportScanResponse>('/auto-import/xhs/scan', {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
    },
    xianyu: {
      probe: (config: AutoImportAdbConfig) =>
        request<AutoImportProbeXianyuResponse>('/auto-import/xianyu/probe', {
          method: 'POST',
          body: JSON.stringify(config),
        }),
      screencap: (payload: { batch_id: string; config: AutoImportAdbConfig }) =>
        request<AutoImportScreencapResponse>('/auto-import/xianyu/screencap', {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
      scanStatus: (batchId: string) =>
        request<AutoImportScanStatusResponse>(
          `/auto-import/xianyu/scan-status?batch_id=${encodeURIComponent(batchId)}`,
        ),
      deleteScreen: async (batchId: string, seq: number) => {
        const fd = new FormData();
        fd.append('batch_id', batchId);
        fd.append('seq', String(seq));
        const resp = await fetch('/api/auto-import/xianyu/screen-delete', {
          method: 'POST',
          body: fd,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json() as Promise<{ ok: boolean; error_kind?: string; remaining?: number }>;
      },
      finishScan: (batchId: string) =>
        request<AutoImportScanResponse>('/auto-import/xianyu/finish-scan', {
          method: 'POST',
          body: JSON.stringify({ batch_id: batchId }),
        }),
      detectListLayout: async (pngBytes: Uint8Array) => {
        const fd = new FormData();
        fd.append('png', new Blob([pngBytes as BlobPart], { type: 'image/png' }), 'list.png');
        const resp = await fetch('/api/auto-import/xianyu/detect-list-layout', {
          method: 'POST',
          body: fd,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json() as Promise<AutoImportDetectListLayoutResponse>;
      },
      detectExpandButton: async (pngBytes: Uint8Array) => {
        const fd = new FormData();
        fd.append('png', new Blob([pngBytes as BlobPart], { type: 'image/png' }), 'detail.png');
        const resp = await fetch('/api/auto-import/xianyu/detect-expand-button', {
          method: 'POST',
          body: fd,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json() as Promise<AutoImportDetectExpandResponse>;
      },
      uploadScreencap: async (batchId: string, pngBytes: Uint8Array) => {
        const fd = new FormData();
        fd.append('batch_id', batchId);
        fd.append('png', new Blob([pngBytes as BlobPart], { type: 'image/png' }), 'auto.png');
        const resp = await fetch('/api/auto-import/xianyu/screencap', {
          method: 'POST',
          body: fd,
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json() as Promise<AutoImportScreencapResponse>;
      },
      testAdb: (cfg: AutoImportAdbConfig) =>
        request<AutoImportTestAdbResponse>('/auto-import/xianyu/test-adb', {
          method: 'POST',
          body: JSON.stringify(cfg),
        }),
      getConfig: () => request<AutoImportAdbConfig>('/auto-import/xianyu/config'),
      putConfig: (cfg: AutoImportAdbConfig) =>
        request<{ ok: boolean }>('/auto-import/xianyu/config', {
          method: 'PUT',
          body: JSON.stringify(cfg),
        }),
    },
    cancelScan: (batchId: string) =>
      request<{ ok: boolean }>('/auto-import/cancel-scan', {
        method: 'POST',
        body: JSON.stringify({ batch_id: batchId }),
      }),
    skuSearch: (q: string, limit = 10) =>
      request<AutoImportSkuSearchResponse>('/auto-import/sku-search', {
        method: 'POST',
        body: JSON.stringify({ q, limit }),
      }),
    commit: (payload: { batch_id: string; items: AutoImportCommitItem[] }) =>
      request<AutoImportCommitResponse>('/auto-import/commit', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
  },
};

// ============================================================================
// 自动导入相关类型（auto-import / CUJ-1 小红书 + CUJ-2 闲鱼）
// ============================================================================

export interface AutoImportAdbConfig {
  device_type: string;
  pc_ip: string;
  port: number;
}

export interface AutoImportDiagnostic {
  name: string;
  label: string;
  ok: boolean;
  hint?: string | null;
  detail?: string;
}

export interface AutoImportExtensionStatus {
  ok: boolean;
  installed: boolean;
  version?: string | null;
  last_probe_at?: string | null;
  error_kind?: string;
  error?: string;
}

export interface AutoImportPreviewProduct {
  listing_title: string;
  quantity: number;
  matched_sku_code?: string | null;
  matched_sku_name?: string | null;
  confidence: number;
  reasoning: string;
}

export interface AutoImportPreviewItem {
  external_order_id: string;
  buyer_nickname: string;
  external_created_at?: string | null;
  is_duplicate: boolean;
  existing_order_id?: number | null;
  products: AutoImportPreviewProduct[];
  recipient_name?: string | null;
  recipient_phone?: string | null;
  recipient_address?: string | null;
  /** 闲鱼专用：该单源截屏的 seq 列表 */
  source_screen_seqs?: number[];
}

export interface AutoImportDroppedOrder {
  external_order_id?: string | null;
  buyer_nickname?: string | null;
  external_created_at?: string | null;
  products?: Array<{ listing_title?: string; quantity?: number }>;
  reason: string;
  missing_fields?: string[];
}

export interface AutoImportScanStats {
  total: number;
  dropped_count: number;
  duplicate_count: number;
  high_conf: number;
  mid_conf: number;
  low_conf: number;
}

export interface AutoImportExtDebug {
  page_url?: string;
  page_title?: string;
  selector_hits?: Record<string, number>;
  body_text_sample?: string;
}

export interface AutoImportScanResponse {
  ok: boolean;
  batch_id: string;
  items: AutoImportPreviewItem[];
  dropped: AutoImportDroppedOrder[];
  screen_count?: number;
  ext_debug?: AutoImportExtDebug;
  stats: AutoImportScanStats;
  error_kind?: string;
  error?: string;
}

export interface AutoImportCommitProduct {
  sku: string;
  quantity: number;
}

export interface AutoImportCommitItem {
  external_order_id: string;
  buyer_nickname: string;
  external_created_at?: string | null;
  platform: string; // "xhs" | "xianyu"
  override_duplicate?: boolean;
  products: AutoImportCommitProduct[];
  recipient_name?: string | null;
  recipient_phone?: string | null;
  recipient_address?: string | null;
  notes?: string | null;
}

export interface AutoImportCommitStats {
  新增: number;
  重复跳过: number;
  手动跳过: number;
  SKU匹配率: number;
}

export interface AutoImportCommitResponse {
  ok: boolean;
  stats?: AutoImportCommitStats;
  created_order_ids?: number[];
  total_ms?: number;
  error_kind?: string;
  error?: string;
}

export interface AutoImportSkuSearchHit {
  sku_code: string;
  sku_name: string;
  score: number;
}

export interface AutoImportSkuSearchResponse {
  ok: boolean;
  hits: AutoImportSkuSearchHit[];
  error_kind?: string;
  error?: string;
}

export interface AutoImportProbeXianyuResponse {
  ok: boolean;
  adb_connected: boolean;
  device_serial?: string | null;
  diagnostics: AutoImportDiagnostic[];
  error_kind?: string;
  error?: string;
}

export interface AutoImportTestAdbResponse {
  ok: boolean;
  adb_connected?: boolean;
  device_serial?: string | null;
  android_version?: string | null;
  diagnostics: AutoImportDiagnostic[];
  error_kind?: string;
  error?: string;
}

export interface AutoImportScreencapResponse {
  ok: boolean;
  screen_id?: string;
  seq?: number;
  error_kind?: string;
  error?: string;
}

export interface AutoImportScanStatusScreen {
  seq: number;
  status: string;
  error?: string | null;
}

export interface AutoImportScanStatusResponse {
  batch_id: string;
  screens: AutoImportScanStatusScreen[];
  parsed_orders: unknown[];
}

export interface AutoImportDetectListLayoutResponse {
  ok: boolean;
  card_x: number;
  card_centers_y: number[];
  card_height_px: number;
  error_kind?: string;
  error?: string;
}

export interface AutoImportDetectExpandResponse {
  ok: boolean;
  x: number;
  y: number;
  error_kind?: string;
  error?: string;
}
