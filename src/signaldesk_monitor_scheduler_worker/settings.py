from __future__ import annotations
import secrets
from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIGNALDESK_", extra="forbid", strict=True)
    monitor_api_url: AnyHttpUrl
    control_api_url: AnyHttpUrl
    monitor_api_credential: SecretStr
    control_api_credential: SecretStr
    poll_interval_seconds: float = 5.0
    retry_backoff_seconds: float = 1.0
    request_connect_timeout_seconds: float = 2.0
    request_read_timeout_seconds: float = 5.0
    request_write_timeout_seconds: float = 5.0
    request_pool_timeout_seconds: float = 2.0
    service_name: str = "signaldesk-monitor-scheduler-worker"

    @field_validator("monitor_api_credential", "control_api_credential")
    @classmethod
    def credential(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32 or not raw.isascii() or any(c.isspace() for c in raw):
            raise ValueError("service credentials must be at least 32 ASCII characters without whitespace")
        return value

    @model_validator(mode="after")
    def valid(self) -> "Settings":
        if secrets.compare_digest(self.monitor_api_credential.get_secret_value(), self.control_api_credential.get_secret_value()):
            raise ValueError("service credentials must be distinct")
        intervals = (self.poll_interval_seconds, self.retry_backoff_seconds, self.request_connect_timeout_seconds, self.request_read_timeout_seconds, self.request_write_timeout_seconds, self.request_pool_timeout_seconds)
        if any(x <= 0 or x > 60 for x in intervals): raise ValueError("intervals and timeouts must be positive and bounded")
        return self
