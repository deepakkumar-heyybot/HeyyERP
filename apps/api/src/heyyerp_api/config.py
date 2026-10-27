from functools import lru_cache
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "heyyERP API"
    app_env: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/heyyerp"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    log_level: str = "INFO"
    auth_base_url: str = "http://localhost:3001"
    required_org_slug: str = "v-rub-suspensions"
    # Better Auth organization slug -> ERP tenant_id. Set as JSON, e.g.
    # ORG_TENANT_MAP={"v-rub-suspensions":"<tenant uuid>"}.
    # ponytail: static map; move to a DB mapping once a second tenant onboards.
    org_tenant_map: dict[str, UUID] = Field(default_factory=dict)


@lru_cache
def get_settings() -> Settings:
    return Settings()
