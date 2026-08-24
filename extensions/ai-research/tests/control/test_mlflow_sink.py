from __future__ import annotations

import hashlib

from ai_research_control.mlflow_sink import MlflowSink


def test_otlp_export_returns_mlflow_readable_trace_id(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 200

    def fake_post(url, *, headers, data, timeout):
        observed.update(url=url, headers=headers, data=data, timeout=timeout)
        return Response()

    monkeypatch.setattr("ai_research_control.mlflow_sink.requests.post", fake_post)
    sink = MlflowSink("http://ai-research-tracking:5000", "fixture")
    trace_id = sink._export_trace("17", "ar0_test")

    expected = hashlib.sha256(b"ar0_test").digest()[:16].hex()
    assert trace_id == f"tr-{expected}"
    assert observed["headers"]["x-mlflow-experiment-id"] == "17"
    assert observed["data"]
