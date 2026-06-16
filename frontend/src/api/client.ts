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

export const api = {
  // 目录（只读，数据源为 catalog.yaml）
  getComponents: () => request<any[]>('/components'),
  getProducts: () => request<any[]>('/products'),
  getAllConfigs: () => request<any[]>('/components/configs/all'),
  reloadCatalog: () => request<any>('/catalog/reload', { method: 'POST' }),

  // 订单
  getOrders: (status?: string) => request<any[]>(`/orders${status ? `?status=${status}` : ''}`),
  createOrder: (data: any) => request<any>('/orders', { method: 'POST', body: JSON.stringify(data) }),
  shipOrder: (id: number) => request<any>(`/orders/${id}/ship`, { method: 'POST' }),
  deleteOrder: (id: number) => request<any>(`/orders/${id}`, { method: 'DELETE' }),

  // 库存
  getInventory: () => request<any[]>('/inventory'),
  adjustInventory: (data: any) => request<any>('/inventory/adjust', { method: 'POST', body: JSON.stringify(data) }),
  setInventory: (inventoryId: number, data: { component_id: number; color?: string; quantity: number }) => request<any>(`/inventory/${inventoryId}`, { method: 'PUT', body: JSON.stringify(data) }),
  getSurplus: () => request<any[]>('/inventory/surplus'),

  // 打印机
  getPrinters: () => request<any[]>('/printers'),
  createPrinter: (data: any) => request<any>('/printers', { method: 'POST', body: JSON.stringify(data) }),
  updatePrinter: (id: number, data: any) => request<any>(`/printers/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deletePrinter: (id: number) => request<any>(`/printers/${id}`, { method: 'DELETE' }),

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

  // 目录 CRUD（写操作走 /catalog/*，URL 中的中文名需 encodeURIComponent）
  catalog: {
    addComponent: (body: any) =>
      request<any>('/catalog/components', { method: 'POST', body: JSON.stringify(body) }),
    updateComponent: (name: string, body: any) =>
      request<any>(`/catalog/components/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deleteComponent: (name: string) =>
      request<any>(`/catalog/components/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    addPlate: (body: any) =>
      request<any>('/catalog/plates', { method: 'POST', body: JSON.stringify(body) }),
    updatePlate: (plateName: string, body: any) =>
      request<any>(`/catalog/plates/${encodeURIComponent(plateName)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deletePlate: (plateName: string) =>
      request<any>(`/catalog/plates/${encodeURIComponent(plateName)}`, { method: 'DELETE' }),
    addProduct: (body: any) =>
      request<any>('/catalog/products', { method: 'POST', body: JSON.stringify(body) }),
    updateProduct: (name: string, body: any) =>
      request<any>(`/catalog/products/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(body) }),
    deleteProduct: (name: string) =>
      request<any>(`/catalog/products/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  },
};
