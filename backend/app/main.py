from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine, SessionLocal
from .routers import catalog, orders, inventory, printers, schedule, config
from .services.catalog import load_catalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时从 YAML 加载目录
    Base.metadata.create_all(bind=engine)
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


@app.get("/api/health")
def health_check():
    return {"status": "ok"}
