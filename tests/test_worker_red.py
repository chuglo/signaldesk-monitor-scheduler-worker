from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from signaldesk_monitor_scheduler_worker.clients import ControlClient, MonitorClient
from signaldesk_monitor_scheduler_worker.settings import Settings
from signaldesk_monitor_scheduler_worker.worker import SchedulerWorker


def claim_payload(run_id, org, creator, target="https://example.test"):
    return {"run_id": str(run_id), "monitor_id": str(uuid4()), "organization_id": str(org),
            "creator_id": str(creator), "target": target,
            "scheduled_for": datetime.now(timezone.utc).isoformat(), "lease_token": "lease-secret-token",
            "lease_generation": 3, "lease_expires_at": datetime.now(timezone.utc).isoformat()}


def settings():
    return Settings(monitor_api_url="https://monitor.test", control_api_url="https://control.test",
                    monitor_api_credential="m" * 32, control_api_credential="c" * 32,
                    poll_interval_seconds=1, retry_backoff_seconds=1)


def test_no_due_work_is_success_and_does_not_call_control():
    monitor = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)), base_url="https://monitor.test")
    control_seen = []
    control = httpx.Client(transport=httpx.MockTransport(lambda r: control_seen.append(r) or httpx.Response(500)), base_url="https://control.test")
    assert SchedulerWorker(MonitorClient(settings().monitor_api_url, settings().monitor_api_credential, client=monitor), ControlClient(settings().control_api_url, settings().control_api_credential, client=control)).run_once() is False
    assert control_seen == []


def test_success_uses_authoritative_fields_and_exact_audience_headers():
    run, org, creator = uuid4(), uuid4(), uuid4()
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.host == "monitor.test" and request.url.path.endswith("/claim"):
            return httpx.Response(200, json=claim_payload(run, org, creator))
        return httpx.Response(201, json={"id": str(uuid4()), "organization_id": str(org), "requested_by_user_id": str(creator), "target": "https://example.test", "correlation_id": str(uuid4()), "status": "pending"})
    mc = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://monitor.test")
    cc = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://control.test")
    s = settings()
    assert SchedulerWorker(MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=mc), ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=cc)).run_once()
    assert seen[0].headers["X-SignalDesk-Service-Actor"] == "monitor-scheduler-worker"
    assert seen[1].headers["X-SignalDesk-Service-Actor"] == "monitor-scheduler-worker"
    body = httpx.Request("POST", "https://x", json={}).read()
    assert "lease-secret-token" not in repr(seen)


def test_control_response_loss_replays_same_idempotency_key_and_one_diagnostic():
    run, org, creator, diagnostic = uuid4(), uuid4(), uuid4(), uuid4()
    calls = []
    def handler(request):
        if request.url.host == "monitor.test": return httpx.Response(200, json=claim_payload(run, org, creator))
        calls.append(request)
        if len(calls) == 1: raise httpx.ReadTimeout("lost")
        return httpx.Response(201, json={"id": str(diagnostic), "organization_id": str(org), "requested_by_user_id": str(creator), "target": "https://example.test", "correlation_id": str(uuid4()), "status": "pending"})
    s = settings(); mc = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://monitor.test"); cc = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://control.test")
    assert SchedulerWorker(MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=mc), ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=cc)).run_once()
    assert len(calls) == 2 and {r.headers["Idempotency-Key"] for r in calls} == {f"monitor-run:{run}"}


def test_stale_lease_does_not_retry_attach_or_log_secrets(caplog):
    run, org, creator = uuid4(), uuid4(), uuid4()
    def handler(request):
        if request.url.host == "monitor.test":
            return httpx.Response(200, json=claim_payload(run, org, creator)) if request.url.path.endswith("claim") else httpx.Response(409, json={"detail":"lease is no longer current"})
        return httpx.Response(201, json={"id": str(uuid4()), "organization_id": str(org), "requested_by_user_id": str(creator), "target": "https://example.test", "correlation_id": str(uuid4()), "status": "pending"})
    s=settings(); client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://monitor.test")
    control=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://control.test")
    assert SchedulerWorker(MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=client), ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=control)).run_once()
    assert "lease-secret-token" not in caplog.text


def test_non_retryable_control_rejection_is_not_replayed():
    run, org, creator = uuid4(), uuid4(), uuid4()
    control_calls = []

    def handler(request):
        if request.url.host == "monitor.test":
            return httpx.Response(200, json=claim_payload(run, org, creator))
        control_calls.append(request)
        return httpx.Response(403, json={"detail": "not authorized"})

    s = settings()
    worker = SchedulerWorker(
        MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://monitor.test")),
        ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://control.test")),
    )
    assert worker.run_once()
    assert len(control_calls) == 1


def test_retryable_attachment_server_failure_is_replayed_with_same_lease_fence():
    run, org, creator, diagnostic = uuid4(), uuid4(), uuid4(), uuid4()
    attach_calls = []

    def handler(request):
        if request.url.host == "monitor.test":
            if request.url.path.endswith("/claim"):
                return httpx.Response(200, json=claim_payload(run, org, creator))
            attach_calls.append(request)
            return httpx.Response(503 if len(attach_calls) == 1 else 200, json={"run_id": str(run), "diagnostic_job_id": str(diagnostic)})
        return httpx.Response(201, json={"id": str(diagnostic), "organization_id": str(org), "requested_by_user_id": str(creator), "target": "https://example.test", "correlation_id": str(uuid4()), "status": "pending"})

    s = settings()
    worker = SchedulerWorker(
        MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://monitor.test")),
        ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://control.test")),
        sleep=lambda _: None,
    )
    assert worker.run_once()
    assert len(attach_calls) == 2
    assert {request.content for request in attach_calls} == {attach_calls[0].content}


def test_continuous_polling_uses_configured_poll_interval_not_retry_backoff():
    sleeps = []
    worker = SchedulerWorker(
        monitor=type("Monitor", (), {"claim": lambda self: None})(),
        control=object(),
        sleep=sleeps.append,
        backoff_seconds=1,
        poll_interval_seconds=17,
    )
    calls = iter((False, True))
    worker.run_forever(lambda: next(calls))
    assert sleeps == [17]


def test_malformed_claim_json_is_a_sanitized_worker_failure(caplog):
    s = settings()
    monitor = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"not-json")), base_url="https://monitor.test")
    control = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("control must not be called")), base_url="https://control.test")
    assert SchedulerWorker(MonitorClient(str(s.monitor_api_url), s.monitor_api_credential.get_secret_value(), client=monitor), ControlClient(str(s.control_api_url), s.control_api_credential.get_secret_value(), client=control)).run_once() is False
    assert "not-json" not in caplog.text


@pytest.mark.parametrize("monitor,control", [("x" * 32, "x" * 32), ("x" * 31, "c" * 32), ("x" * 32, "c " * 16)])
def test_settings_reject_blank_or_shared_credentials(monitor, control):
    with pytest.raises(ValueError): Settings(monitor_api_url="https://monitor.test", control_api_url="https://control.test", monitor_api_credential=monitor, control_api_credential=control)
