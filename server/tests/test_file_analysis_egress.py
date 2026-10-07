"""Actual file-analysis sender tests: no provider network or paid requests."""
from __future__ import annotations

import asyncio
import json
import traceback

import httpx
import pytest

from server.file_assets import analysis
from server.model_router import egress


URL = "https://provider.example/v1/chat/completions"
KEY = "synthetic-secret-not-for-network"
PAYLOAD = {"model": "exact/model", "messages": [{"content": "synthetic-private-body"}]}


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, body=b'{"choices":[]}', failure=None):
        self.body = body
        self.failure = failure
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        if self.failure == "wait":
            await asyncio.Event().wait()
        elif self.failure:
            raise self.failure
        yield self.body

    async def aclose(self):
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    """Keep the real HTTPX client and Egress; replace only DNS and the socket transport."""
    requests, clients, transports, resolutions = [], [], [], []
    state = {"ips": ["8.8.8.8", "1.1.1.1"], "status": 200,
             "stream": TrackedStream(), "failure": None}
    real_client = httpx.AsyncClient

    def resolve(host, port):
        resolutions.append((host, port))
        return state["ips"]

    async def handle(request):
        requests.append(request)
        if state["failure"]:
            raise state["failure"]
        return httpx.Response(state["status"], stream=state["stream"],
                              headers={"Location": "https://second.example/unsafe"})

    def transport(**kwargs):
        transports.append(kwargs)
        return httpx.MockTransport(handle)

    def client(**kwargs):
        clients.append(dict(kwargs))
        kwargs.setdefault("transport", httpx.MockTransport(handle))
        return real_client(**kwargs)

    monkeypatch.delenv(egress.INTERNAL_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(egress, "_system_resolver", resolve)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    state.update(requests=requests, clients=clients, transports=transports, resolutions=resolutions)
    return state


@pytest.mark.asyncio
async def test_actual_post_is_pinned_with_original_host_sni_and_no_environment_proxy(wire, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    await analysis._http_request(URL, KEY, PAYLOAD)
    assert wire["resolutions"] == [("provider.example", 443)]
    assert len(wire["requests"]) == 1
    request = wire["requests"][0]
    assert request.url.host == "1.1.1.1"
    assert request.headers["host"] == "provider.example"
    assert request.extensions["sni_hostname"] == "provider.example"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == PAYLOAD
    assert wire["clients"][0]["trust_env"] is False
    assert wire["clients"][0]["follow_redirects"] is False
    assert wire["transports"] == [{"retries": 0}]
    assert request.extensions["timeout"] == {"connect": 10.0, "read": 150.0, "write": 150.0, "pool": 150.0}
    assert wire["stream"].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("ips", [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"],
                                     ["198.18.0.1"], ["1.1.1.1", "127.0.0.1"]])
async def test_protected_or_mixed_dns_never_posts(wire, ips):
    wire["ips"] = ips
    with pytest.raises(analysis.FileAnalysisError):
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert wire["requests"] == []


@pytest.mark.asyncio
async def test_reauthorizes_each_post_and_rebinding_cannot_reuse_prior_approval(wire):
    await analysis._http_request(URL, KEY, PAYLOAD)
    wire["ips"] = ["127.0.0.1"]
    with pytest.raises(analysis.FileAnalysisError):
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert len(wire["resolutions"]) == 2
    assert len(wire["requests"]) == 1


@pytest.mark.asyncio
async def test_exact_internal_allowlist_preserves_host_and_port(wire, monkeypatch):
    monkeypatch.setenv(egress.INTERNAL_ALLOWLIST_ENV, "internal.example:8080")
    wire["ips"] = ["10.0.0.5"]
    await analysis._http_request("http://internal.example:8080/v1/chat/completions", KEY, PAYLOAD)
    request = wire["requests"][0]
    assert str(request.url) == "http://10.0.0.5:8080/v1/chat/completions"
    assert request.headers["host"] == "internal.example:8080"
    assert "sni_hostname" not in request.extensions


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_connection_failure_never_rotates_ip_or_retries_and_error_is_sanitized(wire, failure):
    wire["failure"] = failure(f"{KEY} {URL} synthetic-private-body")
    with pytest.raises(analysis.FileAnalysisError) as caught:
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert len(wire["requests"]) == 1
    assert wire["requests"][0].url.host == "1.1.1.1"
    rendered = "".join(traceback.format_exception(caught.value))
    for secret in (KEY, URL, "synthetic-private-body"):
        assert secret not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 308, 401, 429, 500])
async def test_http_failure_does_not_follow_or_retry_and_closes(wire, status):
    wire["status"] = status
    with pytest.raises(analysis.FileAnalysisError):
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert len(wire["requests"]) == 1
    assert wire["stream"].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b"[]"])
async def test_invalid_json_closes_without_replaying(wire, body):
    wire["stream"] = TrackedStream(body)
    with pytest.raises(analysis.FileAnalysisError):
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert len(wire["requests"]) == 1
    assert wire["stream"].closed


@pytest.mark.asyncio
async def test_interrupted_body_closes_without_replaying(wire):
    wire["stream"] = TrackedStream(failure=httpx.ReadError("synthetic upstream interruption"))
    with pytest.raises(analysis.FileAnalysisError):
        await analysis._http_request(URL, KEY, PAYLOAD)
    assert wire["stream"].closed
    assert len(wire["requests"]) == 1


@pytest.mark.asyncio
async def test_cancelled_read_closes_without_replaying(wire):
    wire["stream"] = TrackedStream(failure="wait")
    task = asyncio.create_task(analysis._http_request(URL, KEY, PAYLOAD))
    await asyncio.wait_for(wire["stream"].started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire["stream"].closed
    assert len(wire["requests"]) == 1


@pytest.mark.asyncio
async def test_ocr_annotations_in_error_envelope_still_accepted(wire):
    value = {"error": {"metadata": {"file_annotations": [{"type": "file"}]}}}
    wire["status"] = 400
    wire["stream"] = TrackedStream(json.dumps(value).encode())
    payload = analysis._ocr_payload(model_id="exact/model", pdf_bytes=b"synthetic")
    assert await analysis._http_request(URL, KEY, payload) == value
    assert len(wire["requests"]) == 1
    assert wire["stream"].closed


def execution_arguments(mode):
    public = analysis.FileAnalysisTarget(
        target_id="synthetic-target", mode=mode, connection_id="synthetic-connection",
        connection_name="Synthetic", model_id="exact/model", model_name="Exact model",
        provider="openrouter", paid=True, cost_disclosure="Synthetic test only",
    )
    return dict(content=b"synthetic-pdf", format_id="pdf", source_filename="synthetic.pdf",
                source_sha256="a" * 64, selected_pages=(1, 3), prompt="Synthetic instruction",
                target=analysis.ResolvedFileAnalysisTarget(public=public, url=URL, api_key=KEY),
                asset_id="synthetic-file")


@pytest.mark.asyncio
async def test_vision_executor_uses_secure_sender_once_per_page_without_replaying_failure(wire, monkeypatch):
    monkeypatch.setattr(analysis, "_render_page", lambda *args, **kwargs: b"synthetic-image")
    wire["failure"] = httpx.ConnectError("synthetic")
    with pytest.raises(analysis.FileAnalysisError) as caught:
        await analysis.FileAnalysisExecutor().execute(**execution_arguments(analysis.FileAnalysisMode.VISION))
    assert caught.value.error_code == "analysis_no_usable_result"
    # Two distinct planned pages, never a second address or retry for either.
    assert len(wire["requests"]) == len(wire["resolutions"]) == 2
    assert all(request.url.host == "1.1.1.1" for request in wire["requests"])


@pytest.mark.asyncio
async def test_ocr_executor_sends_the_subset_once(wire, monkeypatch):
    subsets = []

    def subset(content, *, pages):
        subsets.append(pages)
        return b"synthetic-subset-pdf"

    monkeypatch.setattr(analysis, "_subset_pdf", subset)
    wire["failure"] = httpx.ConnectError("synthetic")
    with pytest.raises(analysis.FileAnalysisError):
        await analysis.FileAnalysisExecutor().execute(**execution_arguments(analysis.FileAnalysisMode.PROVIDER_OCR))
    assert subsets == [(1, 3)]
    assert len(wire["requests"]) == len(wire["resolutions"]) == 1
    assert wire["requests"][0].url.host == "1.1.1.1"


@pytest.mark.asyncio
async def test_cancelling_vision_read_closes_response_and_never_dispatches_next_page(wire, monkeypatch):
    monkeypatch.setattr(analysis, "_render_page", lambda *args, **kwargs: b"synthetic-image")
    wire["stream"] = TrackedStream(failure="wait")
    task = asyncio.create_task(analysis.FileAnalysisExecutor().execute(
        **execution_arguments(analysis.FileAnalysisMode.VISION)))
    await asyncio.wait_for(wire["stream"].started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire["stream"].closed
    assert len(wire["requests"]) == 1


@pytest.mark.asyncio
async def test_cancelling_during_authorization_never_sends(wire, monkeypatch):
    started = asyncio.Event()

    async def pending_authorization(self, url):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(egress.ProviderEgressPolicy, "authorize", pending_authorization)
    task = asyncio.create_task(analysis._http_request(URL, KEY, PAYLOAD))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire["requests"] == []
