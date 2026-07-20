import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.database import database_is_ready, initialize_database
from app.services.storage import ensure_storage
from app.services.conversations import reconcile_interrupted_messages

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    ensure_storage()
    reconcile_interrupted_messages()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


async def probe(client: httpx.AsyncClient, name: str, url: str) -> tuple[str, str]:
    try:
        response = await client.get(url)
        response.raise_for_status()
        return name, "ok"
    except (httpx.HTTPError, httpx.TimeoutException):
        return name, "unavailable"


@app.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        results = await asyncio.gather(
            probe(client, "ollama", f"{settings.ollama_base_url}/api/tags"),
            probe(client, "qdrant", f"{settings.qdrant_url}/readyz"),
        )
    services = dict(results)
    services["sqlite"] = "ok" if database_is_ready() else "unavailable"
    ready = all(value == "ok" for value in services.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "services": services}
