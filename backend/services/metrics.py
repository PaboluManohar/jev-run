"""Request-level timing instrumentation.

Faithful port of ``scripts/mobile-agent/metrics.mjs``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Metric:
    service: str
    endpoint: str
    wall_ms: float = 0
    ok: bool = True


def summarize_metrics(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-endpoint timing statistics from a list of metric dicts."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in metrics:
        key = f"{m.get('service', '')} {m.get('endpoint', '')}"
        groups.setdefault(key, []).append(m)

    result = []
    for endpoint, group in groups.items():
        failures = sum(1 for m in group if not m.get("ok", True))
        summary: dict[str, Any] = {"endpoint": endpoint, "failures": failures}
        for key in ("wallMs", "dnsMs", "tcpMs", "tlsMs", "responseWaitMs", "downloadMs"):
            values = [m[key] for m in group if isinstance(m.get(key), (int, float))]
            summary[key] = _stats(values)
        result.append(summary)
    return result


def _stats(values: list[float]) -> dict[str, Any]:
    """Compute count, total, median, and p95 for a list of numeric values."""
    if not values:
        return {"count": 0, "total": 0, "median": None, "p95": None}
    values = sorted(v for v in values if isinstance(v, (int, float)))
    total = round(sum(values), 1)
    n = len(values)
    median = round((values[(n - 1) // 2] + values[n // 2]) / 2, 1) if n else None
    p95 = values[max(0, int(n * 0.95) - 1)] if n else None
    return {"count": n, "total": total, "median": median, "p95": p95}


class TimedRequest:
    """Wrap an async request function to collect timing metrics."""

    def __init__(
        self,
        service: str,
        metrics: list[dict[str, Any]],
        on_metric: Any = None,
    ) -> None:
        self.service = service
        self.metrics = metrics
        self.on_metric = on_metric

    async def __call__(self, **kwargs: Any) -> Any:
        started = time.monotonic()
        ok = True
        try:
            result = await self._do_request(**kwargs)
            return result
        except Exception:
            ok = False
            raise
        finally:
            wall_ms = round((time.monotonic() - started) * 1000, 1)
            metric = {
                "service": self.service,
                "endpoint": kwargs.get("url", ""),
                "wallMs": wall_ms,
                "ok": ok,
            }
            self.metrics.append(metric)
            if self.on_metric:
                await self.on_metric(metric)

    async def _do_request(self, **kwargs: Any) -> Any:
        raise NotImplementedError("Subclass or compose with an actual HTTP client.")
