"""Prometheus metrics. Uses the default registry so `/metrics` exposes
everything including process/GC metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests completed.",
    labelnames=("method", "path", "status"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency.",
    labelnames=("method", "path"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

transfer_total = Counter(
    "ledger_transfer_total",
    "Completed money movements by kind.",
    labelnames=("kind", "currency"),
)

transfer_amount_sum = Counter(
    "ledger_transfer_amount_sum",
    "Sum of transferred amounts by kind / currency (float).",
    labelnames=("kind", "currency"),
)

rate_limit_rejects_total = Counter(
    "rate_limit_rejects_total",
    "Requests rejected by the rate limiter.",
)

outbox_pending = Gauge(
    "outbox_pending",
    "Number of outbox events currently PENDING.",
)

webhook_delivery_total = Counter(
    "webhook_delivery_total",
    "Webhook delivery attempts.",
    labelnames=("result",),
)
