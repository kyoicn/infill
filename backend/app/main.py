from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import Base, engine, SessionLocal
from .routers import catalog, orders, inventory, printers, schedule, config
from app.routers import intake
from .services.catalog import ensure_sku_column_exists, ensure_order_notes_column_exists, load_catalog
from .services.migrate import auto_migrate


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
    # 4. 从 YAML 加载目录（旧格式 YAML 会自动 backfill SKU）
    db = SessionLocal()
    try:
        stats = load_catalog(db)
        print(f"目录已加载: {stats}")
    finally:
        db.close()
    yield


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


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# 生产模式下托管前端静态文件
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
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
