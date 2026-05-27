from fastapi import FastAPI

from app.api.v1.tenants import router as tenants_router

app = FastAPI(title="一码通", version="0.1.0")
app.include_router(tenants_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/health/detail")
async def health_detail():
    checks = {"postgres": "unknown", "redis": "unknown", "minio": "unknown"}
    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    try:
        import redis as redis_lib

        r = redis_lib.from_url("redis://localhost:6379/0")
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "version": "0.1.0", "services": checks}
