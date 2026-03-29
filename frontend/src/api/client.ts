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
  setInventory: (componentId: number, quantity: number) => request<any>(`/inventory/${componentId}`, { method: 'PUT', body: JSON.stringify({ component_id: componentId, quantity }) }),
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

  // 排班
  getPlans: () => request<any[]>('/schedule/plans'),
  getPlan: (id: number) => request<any>(`/schedule/plans/${id}`),
  generatePlan: (data: any) => request<any>('/schedule/generate', { method: 'POST', body: JSON.stringify(data) }),
  confirmPlan: (id: number) => request<any>(`/schedule/plans/${id}/confirm`, { method: 'POST' }),
  deletePlan: (id: number) => request<any>(`/schedule/plans/${id}`, { method: 'DELETE' }),
  deleteTask: (id: number) => request<any>(`/schedule/tasks/${id}`, { method: 'DELETE' }),
  replaceTaskConfig: (taskId: number, configId: number) => request<any>(`/schedule/tasks/${taskId}/config/${configId}`, { method: 'PUT' }),
  deleteBatch: (id: number) => request<any>(`/schedule/batches/${id}`, { method: 'DELETE' }),
};
