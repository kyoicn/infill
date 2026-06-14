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

// CUJ-5 merge 的 errorKind 映射 — 见 prd-005 §CUJ-5 与 schemas_intake.MergeResponse.error_kind。
export const MERGE_ERROR_LABELS: Record<string, string> = {
  conflict: '撞名冲突 — 草稿中的组件名 / 盘号与现有目录重复',
  backup_failed: '备份失败 — 无法写入 catalog.yaml.bak.xxx 文件，请检查磁盘空间与权限',
  write_failed: '写入失败 — append 到 catalog.yaml 时出错',
  yaml_invalid: 'YAML 格式校验失败 — 写入后的 catalog.yaml 无法被重新解析（可能是字符编码或结构问题）',
  load_failed: '重新加载失败 — 写入成功但 load_catalog(db) 报错，常见原因是引用了不存在的组件或字段缺失',
};

// CUJ-5 错误建议（按 errorKind 给出排查方向）。
export const ERROR_SUGGESTIONS: Record<string, string> = {
  conflict: '返回上一步给撞名组件 / 盘号 / 变体改名',
  backup_failed: '检查 data/ 目录权限和磁盘空间',
  write_failed: '检查 data/ 目录权限和磁盘空间，或查看后端日志',
  yaml_invalid: '联系开发者 — 这通常是 bug',
  load_failed: '返回上一步检查命名是否撞名，或在产品基名旁加版本号（如『床头柜 v2』）',
};
