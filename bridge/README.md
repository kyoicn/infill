# bridge — infill 打印机状态采集器

mini（macOS）宿主机上跑的 MQTT 中继。把局域网 Bambu Lab 打印机的状态推送
翻译成事件，POST 给容器里的 infill backend。

## 为什么独立

容器在 colima Linux VM 里，跟 Mac 宿主同 LAN 网络隔离，**到不了** 192.168.31.x
的打印机。bridge 跑在 Mac 宿主上没这层隔离，能直连 LAN MQTT。它把 LAN 那一段
拎到容器外面，容器只 consume 事件。

## 数据流

```
[Bambu MQTT 8883 TLS]
       │  push: device/{serial}/report
       ▼
[bridge.py（Mac 宿主，launchd 服务）]
       │  on_message → 标准化 → HTTP POST
       ▼
[infill 容器 /api/internal/printer_state]
       │
       └─ Sampler.on_event(...) → DB sample + WS 广播给前端
```

## 部署

### 一次性首装（mini 上）

```bash
git clone https://github.com/kyoicn/infill.git
cd infill/bridge
./installer.sh init
```

installer 干了：
- 建 `~/.infill-bridge/{bin,log}`
- 从最新 release 拉 `bridge.pyz` 到 `bin/`
- 渲染 `com.infill.bridge.plist`（替换 `@@INSTALL_DIR@@` / `@@HOME@@`）→
  `~/Library/LaunchAgents/`
- `launchctl bootstrap` + `kickstart` 启动服务

### 之后所有更新

**啥都不用做**。GitHub Actions release workflow 自动：
1. CI 构建 `bridge.pyz` → 附到 release assets
2. mini 自托管 runner 的 deploy job：`gh release download` 拉新 .pyz，覆盖到
   `~/.infill-bridge/bin/`，`launchctl kickstart` 重启服务

### 容器里同步开 `SKIP_MQTT_DAEMON`

mini 上的 `~/workspace/infill-deploy/.env` 加一行：

```
SKIP_MQTT_DAEMON=1
```

这样容器 lifespan 跳过自带的 MQTT daemon 启动（反正在 colima 里也连不上 LAN），
sampler 全等 bridge 投喂事件。

不设这条 env 的部署（非 colima 环境，比如 Linux 服务器直跑 docker），容器照常
启动 MQTT daemon —— bridge 仅 mini 这种"容器到不了 LAN"场景需要。

## 配置

bridge 通过 env vars 拿配置，launchd plist 模板里有默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `INFILL_DB_PATH` | `~/workspace/infill-deploy/data/data.db` | infill SQLite 路径 |
| `INFILL_API_URL` | `http://localhost:8000` | 容器后端地址 |

不同路径要覆盖：编辑 `~/Library/LaunchAgents/com.infill.bridge.plist` 的
`EnvironmentVariables` 后 `launchctl kickstart -k gui/$(id -u)/com.infill.bridge`。

## 运行时管理

```bash
./installer.sh status               # 看服务状态
launchctl kickstart -k gui/$(id -u)/com.infill.bridge   # 重启
tail -F ~/.infill-bridge/log/bridge.err.log              # 看日志
./installer.sh uninstall            # 卸载（保留文件）
```

## 故障排查

**bridge 起不来**：看 `~/.infill-bridge/log/bridge.err.log`。常见原因：
- `INFILL_DB_PATH` 指错 → 改 plist
- 数据库里没有"凭证齐全"的打印机 → bridge 会日志 `no configured printers — bridge idle` 然后空转

**事件没到容器**：
- bridge 日志看 `printer X subscribed` 有没有出现
- bridge 日志看 `post event failed` 有没有出现（容器没起 / 端口不对）
- 容器日志看有没有收到 POST：`docker compose logs infill-deploy-app-1 | grep printer_state`

**改了打印机凭证之后 bridge 没反应**：bridge 启动时一次性读 DB；改完凭证手动
重启 bridge：`launchctl kickstart -k gui/$(id -u)/com.infill.bridge`。
（短期 TODO：后端 PUT printer 之后顺手通知 bridge 重连，避免人工 kick。）
