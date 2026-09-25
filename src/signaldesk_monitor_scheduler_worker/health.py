from __future__ import annotations
import httpx

def liveness() -> dict[str, str]:
    return {"status": "ok"}

def readiness(monitor_url: str, control_url: str, *, timeout: httpx.Timeout) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.get(monitor_url.rstrip("/") + "/healthz").is_success and client.get(control_url.rstrip("/") + "/healthz").is_success
    except httpx.HTTPError:
        return False
