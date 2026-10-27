from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from heyyerp_api.api.router import api_router
from heyyerp_api.config import get_settings
from heyyerp_api.db import dispose_database

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
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
app.include_router(api_router, prefix=settings.api_v1_prefix)
