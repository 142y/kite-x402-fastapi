"""In-process request metrics with a Prometheus text exposition.

Zero third-party dependencies: counters and one latency histogram are kept
in plain dictionaries behind a lock, and :meth:`Metrics.render` emits the
Prometheus text format (version 0.0.4) that any scraper can consume via
``GET /metrics``. Label values are escaped and label pairs are sorted, so
the output is byte-stable for a given state and safe to diff in tests.

The design deliberately avoids floating-point counters and gauges: the only
float is the histogram sum, which is what Prometheus expects anyway.
"""

from __future__ import annotations

import threading
from typing import Mapping

#: Upper bounds (milliseconds) of the upstream latency histogram buckets.
#: Chosen around the budgets an agent on the Kite network tolerates before
#: timing out: 5 ms steps at the low end, 10 s ceiling at the high end.
DEFAULT_BUCKETS: tuple[float, ...] = (
    5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000,
)


def _escape_label_value(value: str) -> str:
    """Escape a label value per the Prometheus text format."""
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


class Metrics:
    """Thread-safe counters plus latency histograms.

    ``inc(name, **labels)`` bumps a counter; ``observe(name, value, **labels)``
    records one duration (milliseconds) into a cumulative histogram. Names and
    label keys are trusted (module constants), label *values* come from the
    request path class and status code, so cardinality stays bounded.
    """

    def __init__(self, *, buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._buckets = tuple(sorted(buckets))
        # (name, ((label, value), ...)) -> count
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        # (name, ((label, value), ...)) -> [bucket_counts..., count, sum]
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], list[float]
        ] = {}

    # -- recording ---------------------------------------------------------

    def inc(self, name: str, **labels: str) -> None:
        """Increment ``name`` once under ``labels``."""
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Record one observation (milliseconds) into histogram ``name``."""
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            state = self._histograms.get(key)
            if state is None:
                # len(buckets) cumulative buckets + "+Inf" + count + sum
                state = [0.0] * (len(self._buckets) + 3)
                self._histograms[key] = state
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    state[i] += 1
            state[len(self._buckets)] += 1  # +Inf bucket
            state[len(self._buckets) + 1] += 1  # count
            state[len(self._buckets) + 2] += value  # sum

    # -- exposition --------------------------------------------------------

    def render(self) -> str:
        """Render the current state as Prometheus text format 0.0.4.

        Counters first, then histograms, both sorted by name and labels so the
        output is deterministic (tests diff it, and humans diff it too).
        """
        with self._lock:
            counters = sorted(self._counters.items())
            histograms = sorted(self._histograms.items())
        lines: list[str] = []

        last_name: str | None = None
        for (name, labels), value in counters:
            if name != last_name:
                lines.append(f"# TYPE {name} counter")
                last_name = name
            lines.append(f"{name}{_render_labels(labels)} {value}")
        if counters:
            lines.append("")

        last_name = None
        for (name, labels), state in histograms:
            if name != last_name:
                lines.append(f"# TYPE {name} histogram")
                last_name = name
            # "route=\"v1\"," — prefix to splice extra bucket labels into.
            label_prefix = _render_labels(labels, trailing_comma=True)
            label_body = _render_labels(labels, braces_only=True)
            for i, bound in enumerate(self._buckets):
                lines.append(
                    f'{name}_bucket{{{label_prefix}le="{_render_bound(bound)}"}} {_fmt(state[i])}'
                )
            inf = len(self._buckets)
            lines.append(f'{name}_bucket{{{label_prefix}le="+Inf"}} {_fmt(state[inf])}')
            suffix = f"{{{label_body}}}" if label_body else ""
            lines.append(f"{name}_sum{suffix} {_fmt(state[inf + 2])}")
            lines.append(f"{name}_count{suffix} {_fmt(state[inf + 1])}")
        if histograms:
            lines.append("")

        return "\n".join(lines)


def _render_labels(
    labels: tuple[tuple[str, str], ...],
    *,
    trailing_comma: bool = False,
    braces_only: bool = False,
) -> str:
    """Render label pairs, either as ``{a="b"}`` or as a splice-in fragment."""
    if not labels:
        return ""
    body = ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in labels)
    if trailing_comma:
        return body + ","
    if braces_only:
        return body
    return f"{{{body}}}"


def _render_bound(bound: float) -> str:
    return str(int(bound)) if float(bound).is_integer() else repr(bound)


def _fmt(value: float) -> str:
    """Render counts as integers, sums with full precision."""
    if float(value).is_integer():
        return str(int(value))
    return repr(value)


#: Route classes used as the ``route`` label value. Fixed set on purpose:
#: the raw path would explode label cardinality.
ROUTE_LABELS: Mapping[str, str] = {
    "/healthz": "healthz",
    "/readyz": "readyz",
    "/metrics": "metrics",
}


def route_label(path: str) -> str:
    """Map a request path to a fixed-cardinality route label."""
    if path in ROUTE_LABELS:
        return ROUTE_LABELS[path]
    if path == "/v1" or path.startswith("/v1/"):
        return "v1"
    return "other"
