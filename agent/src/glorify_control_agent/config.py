from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from glorify_control_agent.models import MAX_CONTAINERS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLORIFY_", hide_input_in_errors=True)

    system_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    metrics_url: str
    events_url: str
    pg_statements_url: str | None = None
    token: SecretStr = Field(min_length=1)
    timezone: str = "America/Argentina/Buenos_Aires"
    heartbeat_seconds: float = Field(default=120, ge=1)
    poll_seconds: float = Field(default=15, ge=1)
    flush_seconds: float = Field(default=5, ge=1)
    reminder_seconds: float = Field(default=3600, ge=1)
    daily_check_seconds: float = Field(default=60, ge=1, le=3600)
    daily_hour: int = Field(default=3, ge=0, le=23)
    daily_minute: int = Field(default=0, ge=0, le=59)
    disk_mounts: list[str] = ["/"]
    expected_containers: list[str] = []
    docker_socket: str = "/var/run/docker.sock"
    docker_timeout: float = Field(default=3, gt=0)
    docker_max_containers: int = Field(default=MAX_CONTAINERS, ge=1, le=MAX_CONTAINERS)
    postgres_dsn: SecretStr | None = None
    postgres_poll_seconds: float = Field(default=60, ge=1)
    postgres_timeout: int = Field(default=5, ge=1, le=60)
    spool_path: str = "/var/lib/glorify-control-agent/spool.sqlite3"
    spool_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1024 * 1024)
    spool_max_items: int = Field(default=10000, ge=10)
    spool_retention_seconds: int = Field(default=7 * 86400, ge=60)
    batch_size: int = Field(default=50, ge=1, le=1000)
    http_connect_timeout: float = Field(default=3, gt=0)
    http_timeout: float = Field(default=10, gt=0)
    retry_base_seconds: float = Field(default=5, ge=1)
    retry_max_seconds: float = Field(default=3600, ge=1)

    @field_validator("metrics_url", "events_url", "pg_statements_url")
    @classmethod
    def secure_endpoint(cls, value):
        if value is None:
            return value
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("An HTTPS endpoint without credentials, query or fragment is required")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("disk_mounts")
    @classmethod
    def required_mount(cls, value):
        if not value or any(not path.startswith("/") for path in value):
            raise ValueError("At least one absolute mountpoint is required")
        return value

    @model_validator(mode="after")
    def retry_order(self):
        if self.pg_statements_url is None:
            self.pg_statements_url = self.events_url.rsplit("/", 1)[0] + "/pg-statements"
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry_max_seconds must be >= retry_base_seconds")
        return self
