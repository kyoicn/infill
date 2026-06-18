# PRD 索引

## 产品愿景

面向个人 3D 打印小作坊的生产管理系统。核心价值：晚间盘点 10 分钟，自动生成第二天可直接执行的多打印机排班表 — 操作时间窗口、换料间隔、集中收菜同步、富余生产等约束全部由算法兜底，作坊主无需人工规划。

## 目标用户

单人 3D 打印小作坊主（即项目作者本人）：生产销售 3D 打印小玩具，拥有多台性能一致的打印机（当前 4 台），每天晚间盘点录单、白天按排班表操作打印机。仅中文界面，单用户，无多租户需求。

## 范围边界

- **核心**：产品目录（catalog.yaml 单一数据源）、订单队列、组件库存、排班生成（三种策略）、系统配置
- **规划中**：甘特图视图、排班手动调整、Dashboard 深化、自动导入订单（小红书千帆 Chrome 扩展 + 闲鱼 ADB 截屏）
- **明确不做**：云端多用户、打印机实时监控

## PRD 列表

| PRD | 标题 | 状态 |
|---|---|---|
| [prd-000-catalog](prd-000-catalog.md) | 产品目录（catalog.yaml 只读展示 + 重新加载） | active |
| [prd-001-orders](prd-001-orders.md) | 订单管理（录单 + 待处理队列 + 发货扣库存 + 已发货历史） | active |
| [prd-002-inventory](prd-002-inventory.md) | 组件库存管理（各组件各颜色实时库存查看 + 手动调整 + 富余/缺口展示 + Dashboard 预警） | active |
| [prd-003-schedule](prd-003-schedule.md) | 打印机排班（生成排班·三策略+同步强度+富余+产品过滤 / 列表与甘特图视图 / 批次执行状态流转 / 草稿手动编辑 / 收菜闹钟） | active |
| [prd-004-settings](prd-004-settings.md) | 系统配置（打印机管理·批量增删 / 操作时间窗口按星期几多时段 / 换版时间 / 数据库重置） | active |
| [prd-005-intake](prd-005-intake.md) | 产品录入（从切片截图识别 BOM + 打印盘 + 颜色变体 / 合并入 catalog.yaml） | completed |
| [prd-006-auto-import-orders](prd-006-auto-import-orders.md) | 自动导入订单（小红书千帆 Chrome 扩展 + 闲鱼 ADB 截屏 / LLM 匹配 catalog SKU / 预览校对去重导入） | active |

## 参考文档（Reference docs）

历史/活文档，保留原路径，按需查阅：

- [docs/specs.md](../specs.md) — 原始详细设计规格（业务需求 + 数据模型 + API，开发基准文档）
- [docs/schedule_specs.md](../schedule_specs.md) — 排班算法详细规格（与代码同步维护的活文档）
- [docs/playbook.md](../playbook.md) — 部署与开发模式运行手册
- [docs/project-overview.md](../project-overview.md) — 项目整体状态长篇报告（原 STATUS.md）
