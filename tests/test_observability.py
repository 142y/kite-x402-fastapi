"""Observability contract tests: request IDs, access log, /metrics.

Everything runs against the stub facilitator + mock upstream from
``tests/conftest.py``; the metrics instance is reachable through
``app.state.metrics`` (also how an operator would mount it elsewhere).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from kite_x402.app import create_app
from tests.conftest import BASE_ENV, StubFacilitator, load_settings, make_payment_signature_header

ACCESS_LOGGER = logging.getLogger("kite_x402.access")


@pytest.fixture(autouse=True)
def _quiet_access_log():
    """Keep INFO access lines out of unrelated test output.

    Tests that assert on the log call ``caplog.at_level`` to override this.
    """
    ACCESS_LOGGER.setLevel(logging.CRITICAL)
    yield
    ACCESS_LOGGER.setLevel(logging.CRITICAL)


def make_client(*, access_log: bool = True, upstream_handler=None):  # noqa: ANN201
    """App + TestClient with explicit observability settings."""
    env = dict(BASE_ENV)
    if not access_log:
        env["ACCESS_LOG"] = "false"
    settings = load_settings(env)

    if upstream_handler is None:

        def upstream_handler(request: httpx.Request) -> httpx.Response:  # noqa: ANN001
            return httpx.Response(
                200,
                json={
                    "path": request.url.path,
                    "request_id": request.headers.get("x-request-id"),
                },
            )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))
    app = create_app(
        settings,
        upstream_client=upstream_client,
        facilitator=StubFacilitator(),
    )
    return TestClient(app)


class TestRequestIDs:
    def test_caller_supplied_id_is_echoed_back(self):
        with make_client() as client:
            response = client.get(
                "/v1/forecast",
                headers={
                    "X-Request-ID": "agent-trace-42",
                    "PAYMENT-SIGNATURE": make_payment_signature_header(),
                },
            )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "agent-trace-42"

    def test_missing_id_is_generated_and_returned(self):
        with make_client() as client:
            response = client.get(
                "/v1/forecast",
                headers={"PAYMENT-SIGNATURE": make_payment_signature_header()},
            )
        generated = response.headers["x-request-id"]
        assert response.status_code == 200
        assert len(generated) == 32  # uuid4().hex
        assert int(generated, 16) >= 0  # hex, not arbitrary text

    def test_unpaid_402_also_carries_a_request_id(self):
        with make_client() as client:
            response = client.get("/v1/forecast")
        assert response.status_code == 402
        assert response.headers["x-request-id"]

    def test_id_is_forwarded_to_the_upstream(self):
        with make_client() as client:
            response = client.get(
                "/v1/forecast",
                headers={
                    "X-Request-ID": "agent-trace-43",
                    "PAYMENT-SIGNATURE": make_payment_signature_header(),
                },
            )
        # Same ID: the caller, the wrapper log and the upstream log line up.
        assert response.json()["request_id"] == "agent-trace-43"
        assert response.headers["x-request-id"] == "agent-trace-43"


class TestAccessLog:
    def test_one_json_line_per_request_with_expected_fields(self, caplog):
        with caplog.at_level(logging.INFO, logger="kite_x402.access"):
            with make_client() as client:
                client.get("/v1/forecast")
                client.get(
                    "/v1/forecast",
                    headers={"PAYMENT-SIGNATURE": make_payment_signature_header()},
                )

        lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "kite_x402.access"
        ]
        assert len(lines) == 2
        unpaid = json.loads(lines[0])
        paid = json.loads(lines[1])
        assert unpaid["status"] == 402
        assert unpaid["route"] == "v1"
        assert unpaid["method"] == "GET"
        assert unpaid["msg"] == "access"
        assert unpaid["request_id"]
        assert isinstance(unpaid["duration_ms"], (int, float))
        assert paid["status"] == 200
        assert paid["request_id"] != unpaid["request_id"]

    def test_access_log_can_be_disabled(self, caplog):
        with caplog.at_level(logging.INFO, logger="kite_x402.access"):
            with make_client(access_log=False) as client:
                client.get("/v1/forecast")

        assert not [
            r for r in caplog.records if r.name == "kite_x402.access"
        ]


class TestMetrics:
    def test_metrics_endpoint_speaks_prometheus_text(self):
        with make_client() as client:
            client.get(
                "/v1/forecast",
                headers={"PAYMENT-SIGNATURE": make_payment_signature_header()},
            )
            client.get("/v1/forecast")  # unpaid -> 402
            client.get("/healthz")
            response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        assert "# TYPE kite_http_requests_total counter" in body
        assert 'kite_http_requests_total{route="v1",status="200"} 1' in body
        assert 'kite_http_requests_total{route="v1",status="402"} 1' in body
        assert 'kite_http_requests_total{route="healthz",status="200"} 1' in body

    def test_upstream_latency_histogram_records_paid_requests(self):
        with make_client() as client:
            client.get(
                "/v1/forecast",
                headers={"PAYMENT-SIGNATURE": make_payment_signature_header()},
            )
            body = client.get("/metrics").text

        assert "# TYPE kite_upstream_request_duration_ms histogram" in body
        assert "kite_upstream_request_duration_ms_count 1" in body
        # Buckets are cumulative: the +Inf bucket must also hold the sample.
        assert 'kite_upstream_request_duration_ms_bucket{le="+Inf"} 1' in body

    def test_unreachable_upstream_counts_as_failure(self):
        def unreachable(request: httpx.Request) -> httpx.Response:  # noqa: ANN001
            raise httpx.ConnectError("no route to host")

        with make_client(upstream_handler=unreachable) as client:
            response = client.get(
                "/v1/forecast",
                headers={"PAYMENT-SIGNATURE": make_payment_signature_header()},
            )
            assert response.status_code == 502
            body = client.get("/metrics").text

        assert "kite_upstream_failures_total 1" in body
        # The payment is NOT settled when the upstream is unreachable.
        assert "kite_upstream_request_duration_ms_count" not in body

    def test_unknown_paths_bucket_into_other(self):
        with make_client() as client:
            assert client.get("/nope").status_code == 404
            body = client.get("/metrics").text

        assert 'kite_http_requests_total{route="other",status="404"} 1' in body


class TestMetricsUnit:
    """The exposition format itself, independent of the app wiring."""

    def test_render_is_deterministic_and_sorted(self):
        from kite_x402.metrics import Metrics

        m = Metrics()
        m.inc("b_counter", status="200")
        m.inc("a_counter", status="500")
        m.inc("a_counter", status="200")
        m.observe("z_latency_ms", 7.5)

        first = m.render()
        assert first == m.render()
        lines = first.splitlines()
        # Counters sorted by (name, labels): a_counter before b_counter.
        assert lines.index('a_counter{status="200"} 1') < lines.index('b_counter{status="200"} 1')

    def test_label_values_are_escaped(self):
        from kite_x402.metrics import Metrics

        m = Metrics()
        m.inc("weird", status='a"b\\c\nd')
        assert 'weird{status="a\\"b\\\\c\\nd"} 1' in m.render()
