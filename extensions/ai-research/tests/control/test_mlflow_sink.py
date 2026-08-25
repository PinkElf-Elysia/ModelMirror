from __future__ import annotations

import hashlib

import pytest
import requests

from ai_research_control.mlflow_sink import MlflowSink, MlflowSinkError


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


def test_probe_uses_bounded_rest_request(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"experiment": {"experiment_id": "17"}}

    def fake_get(url, *, params, timeout):
        observed.update(url=url, params=params, timeout=timeout)
        return Response()

    monkeypatch.setattr("ai_research_control.mlflow_sink.requests.get", fake_get)
    sink = MlflowSink("http://ai-research-tracking:5000", "fixture")

    assert sink.probe() == "17"
    assert observed["timeout"] == 2
    assert observed["params"] == {"experiment_name": "fixture"}


def test_probe_fails_closed_when_tracking_times_out(monkeypatch) -> None:
    def fake_get(*_args, **_kwargs):
        raise requests.Timeout("offline")

    monkeypatch.setattr("ai_research_control.mlflow_sink.requests.get", fake_get)
    sink = MlflowSink("http://ai-research-tracking:5000", "fixture")

    with pytest.raises(MlflowSinkError, match="probe failed"):
        sink.probe()


def test_probe_creates_missing_experiment_with_bounded_request(monkeypatch) -> None:
    class MissingResponse:
        status_code = 404

    class CreatedResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"experiment_id": "23"}

    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "ai_research_control.mlflow_sink.requests.get",
        lambda *_args, **_kwargs: MissingResponse(),
    )

    def fake_post(url, *, json, timeout):
        observed.update(url=url, json=json, timeout=timeout)
        return CreatedResponse()

    monkeypatch.setattr("ai_research_control.mlflow_sink.requests.post", fake_post)
    sink = MlflowSink("http://ai-research-tracking:5000", "fixture")

    assert sink.probe() == "23"
    assert observed["json"] == {"name": "fixture"}
    assert observed["timeout"] == 2
