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
    web_base_url: str = "http://localhost:5173"
    # Owner-role URL used only by Alembic (never by the API itself).
    migration_database_url: str | None = None
    required_org_slug: str = "v-rub-suspensions"
    # Better Auth organization slug -> ERP tenant_id. Set as JSON, e.g.
    # ORG_TENANT_MAP={"v-rub-suspensions":"<tenant uuid>"}.
    # ponytail: static map; move to a DB mapping once a second tenant onboards.
    org_tenant_map: dict[str, UUID] = Field(default_factory=dict)
    # 32 random bytes, base64. Encrypts bank account numbers; the same key for everyone who shares
    # a database. Without it the features that store such values answer 503.
    data_encryption_key: str | None = None
    # Notifications and background jobs. "console" only logs (development and tests); the real
    # senders need their credentials here, never in the database.
    mail_provider: str = "console"  # console or smtp
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: str | None = None
    mail_from: str = "HeyyERP <noreply@heyybot.com>"
    whatsapp_provider: str = "console"  # console or cloud (WhatsApp Business Cloud API)
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_template: str = "heyyerp_alert"  # an approved template with two text variables
    whatsapp_template_language: str = "en"
    background_worker: bool = False  # run the notification and SLA jobs inside this process
    worker_interval_seconds: int = Field(default=60, ge=5, le=3600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
