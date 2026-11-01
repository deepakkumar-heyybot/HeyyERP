import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from heyyerp_api.api.router import api_router
from heyyerp_api.config import get_settings
from heyyerp_api.db import dispose_database
from heyyerp_api.notify.jobs import worker_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = asyncio.create_task(worker_loop()) if settings.background_worker else None
    yield
    if worker is not None:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    await dispose_database()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="V-Rub multi-plant ERP API",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    docs_url=f"{settings.api_v1_prefix}/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "If-Match"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
