import os
from contextlib import asynccontextmanager
from pathlib import Path


# --- 开发期：从仓库根的 .env 加载环境变量（不覆盖已有值；Docker 生产由 -e 传入）---
# 跳过 pytest 上下文，避免污染单测里的 monkeypatch.delenv 场景。
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_PATH.is_file() and "PYTEST_CURRENT_TEST" not in os.environ and "PYTEST_VERSION" not in os.environ:
    for _line in _ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import Base, engine, SessionLocal
from .routers import catalog, orders, inventory, printers, schedule, config
from .routers import printer_status as printer_status_router
from app.routers import intake
from app.routers import auto_import as auto_import_router
from .services.catalog import (
    ensure_sku_column_exists,
    ensure_order_notes_column_exists,
    ensure_order_auto_import_schema_exists,
    load_catalog,
)
from .services.migrate import auto_migrate
from .services.printer_mqtt_daemon import MqttDaemon
from .services.printer_status_sampler import Broadcaster, Sampler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 先补齐缺失的列（旧数据库兼容）
    auto_migrate(engine)
    # 2. 再创建可能缺失的整张表
    Base.metadata.create_all(bind=engine)
    # 3. v0.3.0：确保 components / print_configs / products 三表有 sku 列（旧 DB 升级）
    altered = ensure_sku_column_exists(engine)
    if altered:
        print(f"已为 {altered} 表补齐 sku 列")
    # 3b. v0.3.1：确保 orders 表有 notes 列（旧 DB 升级）
    if ensure_order_notes_column_exists(engine):
        print("已为 orders 表补齐 notes 列")
    # 3c. v0.4.0 (prd-006)：补齐 orders 表 platform/external_order_id/buyer_nickname/external_created_at
    #     + 创建 (platform, external_order_id) partial unique index
    if ensure_order_auto_import_schema_exists(engine):
        print("已为 orders 表补齐 auto-import 字段 + partial unique index")
    # 4. 从 YAML 加载目录（旧格式 YAML 会自动 backfill SKU）
    db = SessionLocal()
    try:
        stats = load_catalog(db)
        print(f"目录已加载: {stats}")
    finally:
        db.close()

    # 5. prd-007：起 MQTT 守护进程 + 心跳循环；三对象挂到 app.state 供 router 取
    broadcaster = Broadcaster()
    sampler = Sampler(broadcaster)
    daemon = MqttDaemon(sampler)
    app.state.printer_status_broadcaster = broadcaster
    app.state.printer_status_sampler = sampler
    app.state.printer_status_daemon = daemon
    daemon.startup()  # 同步：paho loop_start 非阻塞
    await sampler.start_heartbeat_loop()
    try:
        yield
    finally:
        sampler.stop_heartbeat_loop()
        daemon.shutdown()


app = FastAPI(title="3D打印排班系统", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(inventory.router)
app.include_router(printers.router)
app.include_router(schedule.router)
app.include_router(config.router)
app.include_router(intake.router)
app.include_router(auto_import_router.router)
app.include_router(printer_status_router.router)
app.include_router(printer_status_router.ws_router)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# 生产模式下托管前端静态文件
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# Chrome 扩展分发目录（CUJ-4 下载链接走这里）
EXTENSIONS_DIR = STATIC_DIR / "extensions"
if EXTENSIONS_DIR.is_dir():
    app.mount("/static/extensions", StaticFiles(directory=EXTENSIONS_DIR), name="static-extensions")
if STATIC_DIR.is_dir() and (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="static-assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        if path.startswith("api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file = STATIC_DIR / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html")
