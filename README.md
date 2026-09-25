# SignalDesk monitor scheduler worker

`signaldesk-monitor-scheduler-worker` is a stateless Python 3.11 worker. It claims one due monitor run, creates the authoritative diagnostic with the run UUID as its durable idempotency key, and attaches the diagnostic under the lease fence. It has no database or Redis dependency.

Run once with `uv run signaldesk-monitor-scheduler-worker --once`, or omit `--once` for bounded polling. Configuration is provided by strict `SIGNALDESK_*` environment variables; credentials are audience-specific and are never emitted in logs.
