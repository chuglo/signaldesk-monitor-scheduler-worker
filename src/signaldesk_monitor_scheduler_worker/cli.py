from __future__ import annotations
import argparse, logging, signal
from signaldesk_service_kit import bounded_timeout
from .clients import ControlClient, MonitorClient
from .settings import Settings
from .worker import SchedulerWorker
from .health import readiness

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--once", action="store_true"); parser.add_argument("--ready", action="store_true")
    args = parser.parse_args(argv)
    try: settings = Settings()
    except Exception: return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    timeout = bounded_timeout(connect=settings.request_connect_timeout_seconds, read=settings.request_read_timeout_seconds, write=settings.request_write_timeout_seconds, pool=settings.request_pool_timeout_seconds)
    if args.ready: return 0 if readiness(str(settings.monitor_api_url), str(settings.control_api_url), timeout=timeout) else 1
    monitor = MonitorClient(str(settings.monitor_api_url), settings.monitor_api_credential.get_secret_value(), timeout=timeout)
    control = ControlClient(str(settings.control_api_url), settings.control_api_credential.get_secret_value(), timeout=timeout)
    worker = SchedulerWorker(monitor, control, backoff_seconds=settings.retry_backoff_seconds, poll_interval_seconds=settings.poll_interval_seconds)
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping; stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        if args.once:
            worker.run_once()
            return 0
        worker.run_forever(lambda: stopping)
        return 0
    finally:
        monitor.client.close()
        control.client.close()
