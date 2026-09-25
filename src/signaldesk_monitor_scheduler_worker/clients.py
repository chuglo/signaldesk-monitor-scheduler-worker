from __future__ import annotations
from datetime import datetime
from uuid import UUID
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

HEADERS_ACTOR = "monitor-scheduler-worker"
class ControlConflict(httpx.HTTPError):
    """A non-retryable authoritative idempotency or tenant conflict."""


class ProtocolError(httpx.HTTPError):
    """A response that does not satisfy the closed internal HTTP contract."""

class Strict(BaseModel): model_config = ConfigDict(extra="forbid", strict=True)
class Claim(Strict):
    run_id: UUID = Field(strict=False); monitor_id: UUID = Field(strict=False); organization_id: UUID = Field(strict=False); creator_id: UUID = Field(strict=False); target: str
    scheduled_for: datetime = Field(strict=False); lease_token: str; lease_generation: int = Field(ge=1); lease_expires_at: datetime = Field(strict=False)
class Diagnostic(Strict):
    id: UUID = Field(strict=False); organization_id: UUID = Field(strict=False); requested_by_user_id: UUID = Field(strict=False); target: str; correlation_id: UUID = Field(strict=False); status: str


def _parse_json(response: httpx.Response, model: type[Strict]) -> Strict:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/json":
        raise ProtocolError("invalid internal response")
    try:
        return model.model_validate_json(response.content)
    except (ValidationError, ValueError) as error:
        raise ProtocolError("invalid internal response") from error

def _headers(credential: str) -> dict[str, str]:
    credential = credential.get_secret_value() if hasattr(credential, "get_secret_value") else credential
    return {"X-SignalDesk-Service-Actor": HEADERS_ACTOR, "X-SignalDesk-Service-Credential": credential}

class MonitorClient:
    def __init__(self, base_url: str, credential: str, *, client: httpx.Client | None = None, timeout: httpx.Timeout | None = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout or httpx.Timeout(5.0, connect=2.0))
        self.headers = _headers(credential)
    def claim(self) -> Claim | None:
        response = self.client.post("/internal/scheduler/claim", headers=self.headers)
        if response.status_code == 204: return None
        response.raise_for_status()
        return _parse_json(response, Claim)  # type: ignore[return-value]
    def attach(self, claim: Claim, diagnostic_id: UUID) -> None:
        response = self.client.post(f"/internal/scheduler/runs/{claim.run_id}/diagnostic", headers=self.headers, json={"diagnostic_job_id": str(diagnostic_id), "lease_token": claim.lease_token, "lease_generation": claim.lease_generation})
        response.raise_for_status()

class ControlClient:
    def __init__(self, base_url: str, credential: str, *, client: httpx.Client | None = None, timeout: httpx.Timeout | None = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=timeout or httpx.Timeout(5.0, connect=2.0))
        self.headers = _headers(credential)
    def create_scheduled_diagnostic(self, claim: Claim) -> Diagnostic:
        response = self.client.post("/internal/monitor-scheduler/diagnostics", headers={**self.headers, "Idempotency-Key": f"monitor-run:{claim.run_id}"}, json={"monitor_run_id": str(claim.run_id), "organization_id": str(claim.organization_id), "requested_by_user_id": str(claim.creator_id), "target": claim.target}, follow_redirects=False)
        if response.status_code == 409: raise ControlConflict("control conflict")
        response.raise_for_status()
        diagnostic = _parse_json(response, Diagnostic)  # type: ignore[assignment]
        if (diagnostic.organization_id != claim.organization_id or diagnostic.requested_by_user_id != claim.creator_id or diagnostic.target != claim.target):
            raise ControlConflict("diagnostic scope conflict")
        return diagnostic
