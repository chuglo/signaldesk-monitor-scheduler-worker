from __future__ import annotations
import logging
import time
from collections.abc import Callable
import httpx
from .clients import Claim, ControlClient, ControlConflict, MonitorClient

log = logging.getLogger("signaldesk_monitor_scheduler_worker")
class SchedulerWorker:
    def __init__(self, monitor: MonitorClient, control: ControlClient, *, sleep: Callable[[float], None] = time.sleep, backoff_seconds: float = 1.0, poll_interval_seconds: float = 5.0):
        self.monitor, self.control, self.sleep = monitor, control, sleep
        self.backoff_seconds, self.poll_interval_seconds = backoff_seconds, poll_interval_seconds

    @staticmethod
    def _retryable(error: httpx.HTTPError) -> bool:
        return isinstance(error, httpx.TransportError) or (
            isinstance(error, httpx.HTTPStatusError) and 500 <= error.response.status_code < 600
        )
    def run_once(self) -> bool:
        try: claim = self.monitor.claim()
        except httpx.HTTPError: log.error("outcome=claim_failed"); return False
        if claim is None: log.info("outcome=no_work"); return False
        log.info("run_id=%s outcome=claimed", claim.run_id)
        diagnostic = None
        for attempt in range(2):
            try:
                diagnostic = self.control.create_scheduled_diagnostic(claim)
                break
            except ControlConflict:
                log.error("run_id=%s outcome=control_conflict", claim.run_id)
                return True
            except httpx.HTTPError as error:
                if attempt == 0 and self._retryable(error):
                    self.sleep(self.backoff_seconds)
                else:
                    log.error("run_id=%s outcome=diagnostic_failed", claim.run_id)
                    return True
        for attempt in range(2):
            try:
                self.monitor.attach(claim, diagnostic.id)
                log.info("run_id=%s diagnostic_id=%s outcome=attached", claim.run_id, diagnostic.id)
                break
            except httpx.HTTPError as error:
                if attempt == 0 and self._retryable(error):
                    self.sleep(self.backoff_seconds)
                    continue
                log.warning("run_id=%s diagnostic_id=%s outcome=attach_failed", claim.run_id, diagnostic.id)
                break
        return True
    def run_forever(self, stop: Callable[[], bool]) -> None:
        while not stop():
            self.run_once()
            self.sleep(self.poll_interval_seconds)
