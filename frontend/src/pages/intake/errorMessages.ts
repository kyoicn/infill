// CUJ-2 recognize 错误的中文措辞映射（errorKind → 用户可读文案）。
// errorKind 由后端在 recognize 响应里返回（ok: false 时）。
export const RECOGNIZE_ERROR_LABELS: Record<string, string> = {
  no_api_key: '未检测到 LLM 提供商 API key — 请配置 .env 后重试',
  http_401: 'HTTP 401 Unauthorized — DeepSeek 拒绝请求，可能是 API key 无效或已用尽额度',
  http_5xx: 'DeepSeek 服务暂时不可用 — 请稍后重试',
  timeout: '连接超时 — 90 秒未收到响应，请检查网络',
  parse_failed: '响应解析失败 — 返回内容不是预期的 JSON 结构',
  schema_invalid: '识别结果格式不正确 — LLM 未按预期 schema 返回',
  image_too_large: '图片过大 — 单张图超过 LLM 接受的最大尺寸，请缩小后重试',
  session_expired: '会话已过期 — 临时文件已被清理，请重新上传',
  network: '网络错误 — 请检查网络连接',
};

// CUJ-5 merge 的 errorKind 映射 — T10 会扩展进一步细化，这里先放占位。
export const MERGE_ERROR_LABELS: Record<string, string> = {
  conflict: '撞名冲突 — 草稿中的组件名 / 盘号与现有目录重复',
  backup_failed: '无法创建备份文件',
  write_failed: '写入 catalog.yaml 失败',
  yaml_invalid: 'YAML 格式校验失败',
  load_failed: '重新加载 catalog 失败 — 已自动回滚',
};
