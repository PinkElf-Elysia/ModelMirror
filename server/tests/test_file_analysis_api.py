from __future__ import annotations

import base64
import asyncio
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.file_assets.analysis import (
    FileAnalysisExecutor,
    FileAnalysisMode,
    FileAnalysisTarget,
    ResolvedFileAnalysisTarget,
)
from server.file_assets.api import router
from server.file_assets.service import FileAssetService, get_file_asset_service
from server.file_assets.service import ChatFileSelection


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _TargetResolver:
    def __init__(self) -> None:
        self.public = FileAnalysisTarget(
            target_id="analysis_target_exact",
            mode=FileAnalysisMode.VISION,
            connection_id="conn_exact",
            connection_name="Exact connection",
            model_id="vendor/vision-model",
            model_name="Exact vision model",
            provider="newapi",
            paid=False,
            cost_disclosure="The selected connection may charge for token use.",
        )

    async def list_targets(self):
        return (self.public,)

    async def resolve(self, target_id: str):
        assert target_id == self.public.target_id
        return ResolvedFileAnalysisTarget(
            public=self.public,
            url="https://exact.invalid/v1/chat/completions",
            api_key="test-only",
        )


def _client(tmp_path: Path, requester_override=None):
    calls: list[dict] = []

    async def requester(_url: str, _key: str, payload: dict):
        calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"ocr_text":"recognized","visual_summary":"diagram",'
                        '"tables":[],"charts":[],"warnings":[]}'
                    }
                }
            ]
        }

    service = FileAssetService(
        storage_dir=tmp_path,
        mode="shadow",
        analysis_target_resolver=_TargetResolver(),  # type: ignore[arg-type]
        analysis_executor=FileAnalysisExecutor(requester_override or requester),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_file_asset_service] = lambda: service
    return TestClient(app), service, calls


def _upload(client: TestClient) -> dict:
    response = client.post(
        "/api/files",
        data={
            "purpose": "chat",
            "scope_id": "chat-analysis",
            "input_kind": "visual_analysis",
        },
        files={"file": ("synthetic.png", _ONE_PIXEL_PNG, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _analysis_payload(**updates):
    payload = {
        "scope_id": "chat-analysis",
        "mode": "vision",
        "target_id": "analysis_target_exact",
        "selected_pages": [1],
        "prompt": "Read the synthetic label",
        "paid_acknowledged": False,
    }
    payload.update(updates)
    return payload


def _preflight_payload(**updates):
    payload = _analysis_payload(**updates)
    payload.pop("paid_acknowledged")
    return payload


def test_analysis_api_requires_exact_confirmation_and_recovers_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")
    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "true")
    client, _service, calls = _client(tmp_path)
    with client:
        asset = _upload(client)
        asset_id = asset["asset_id"]
        targets = client.get("/api/files/analysis-targets")
        assert targets.status_code == 200
        assert [item["target_id"] for item in targets.json()["items"]] == [
            "analysis_target_exact"
        ]

        wrong_scope = client.post(
                f"/api/files/{asset_id}/analysis-preflight",
                json=_preflight_payload(scope_id="other-scope"),
        )
        assert wrong_scope.status_code == 404
        assert calls == []

        preflight = client.post(
                f"/api/files/{asset_id}/analysis-preflight",
                json=_preflight_payload(),
        )
        assert preflight.status_code == 200
        assert preflight.json()["selected_pages"] == [1]
        assert preflight.json()["paid_confirmation_required"] is False
        assert calls == []

        unconfirmed = client.post(
            f"/api/files/{asset_id}/analyses",
            json=_analysis_payload(confirmation_revision=1),
        )
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["detail"]["code"] == "analysis_confirmation_invalid"
        assert calls == []

        confirmation = client.post(
            f"/api/files/{asset_id}/analysis-confirm",
            json=_analysis_payload(),
        )
        assert confirmation.status_code == 200
        revision = confirmation.json()["confirmation_revision"]

        mismatch = client.post(
            f"/api/files/{asset_id}/analyses",
            json=_analysis_payload(
                prompt="Changed after confirmation",
                confirmation_revision=revision,
            ),
        )
        assert mismatch.status_code == 409
        assert calls == []

        created = client.post(
            f"/api/files/{asset_id}/analyses",
            json=_analysis_payload(confirmation_revision=revision),
        )
        assert created.status_code == 202, created.text
        analysis_id = created.json()["analysis_id"]
        completed = None
        for _ in range(100):
            response = client.get(
                f"/api/files/{asset_id}/analyses/{analysis_id}",
                params={"scope_id": "chat-analysis"},
            )
            assert response.status_code == 200
            completed = response.json()
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["result"]["sections"][0]["text"] == "recognized"
        assert len(calls) == 1
        duplicate = client.post(
            f"/api/files/{asset_id}/analyses",
            json=_analysis_payload(confirmation_revision=revision),
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["analysis_id"] == analysis_id
        time.sleep(0.02)
        assert len(calls) == 1

        wrong_prompt = client.post(
            f"/api/files/{asset_id}/confirm",
            params={"purpose": "chat", "scope_id": "chat-analysis"},
            json={
                "handling": "extract",
                "analysis_artifact_id": completed["result_artifact_id"],
                "analysis_prompt": "changed after analysis",
            },
        )
        assert wrong_prompt.status_code == 409
        send_confirmation = client.post(
            f"/api/files/{asset_id}/confirm",
            params={"purpose": "chat", "scope_id": "chat-analysis"},
            json={
                "handling": "extract",
                "analysis_artifact_id": completed["result_artifact_id"],
                "analysis_prompt": "Read the synthetic label",
            },
        )
        assert send_confirmation.status_code == 200
        send_revision = send_confirmation.json()["confirmation_revision"]
        resolved = _service.resolve_chat_inputs(
            (
                ChatFileSelection(
                    asset_id=asset_id,
                    handling="extract",
                    confirmation_revision=send_revision,
                    analysis_artifact_id=completed["result_artifact_id"],
                    analysis_prompt="Read the synthetic label",
                ),
            ),
            scope_id="chat-analysis",
        )
        assert resolved[0].analysis_artifact is not None
        assert resolved[0].analysis_artifact.sections[0].text == "recognized"
        assert resolved[0].parsed_document is None

        listed = client.get(
            "/api/files/analyses",
            params={"purpose": "chat", "scope_id": "chat-analysis"},
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["analysis_id"] == analysis_id
        assert (
            client.get(
                f"/api/files/{asset_id}/analyses/{analysis_id}",
                params={"scope_id": "other-scope"},
            ).status_code
            == 404
        )


def test_restart_interrupts_non_terminal_analysis_without_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "true")
    client, service, calls = _client(tmp_path)
    with client:
        asset = _upload(client)
        confirmation = client.post(
            f"/api/files/{asset['asset_id']}/analysis-confirm",
            json=_analysis_payload(),
        ).json()
        preflight = service.repository.confirm_analysis(
            "local",
            asset["asset_id"],
            scope_id="chat-analysis",
            mode="vision",
            target_id="analysis_target_exact",
            config_digest=confirmation["config_digest"],
            prompt_sha256=confirmation["prompt_sha256"],
            paid_acknowledged=False,
            expires_at=confirmation["expires_at"],
        )
        assert preflight is not None
        job = service.repository.create_analysis_job(
            "local",
            asset["asset_id"],
            scope_id="chat-analysis",
            mode="vision",
            target_id="analysis_target_exact",
            config_digest=confirmation["config_digest"],
            prompt_sha256=confirmation["prompt_sha256"],
            paid_acknowledged=False,
            confirmation_revision=preflight.revision,
            selected_pages=(1,),
        )
        assert job is not None
        assert calls == []

    restarted = FileAssetService(storage_dir=tmp_path, mode="shadow")
    restarted.get_asset(
        asset["asset_id"], purpose="chat", scope_id="chat-analysis"
    )
    interrupted = restarted.repository.get_analysis_job("local", job.id)
    assert interrupted is not None
    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "analysis_interrupted"
    assert calls == []


def test_cancel_aborts_an_inflight_provider_request_and_persists_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CHAT_ONE_SHOT_VISION_ENABLED", "true")
    provider_started = threading.Event()
    provider_cancelled = threading.Event()

    async def blocking_requester(_url: str, _key: str, _payload: dict):
        provider_started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            provider_cancelled.set()
            raise

    client, _service, _calls = _client(
        tmp_path, requester_override=blocking_requester
    )
    with client:
        asset = _upload(client)
        confirmation = client.post(
            f"/api/files/{asset['asset_id']}/analysis-confirm",
            json=_analysis_payload(),
        ).json()
        created = client.post(
            f"/api/files/{asset['asset_id']}/analyses",
            json=_analysis_payload(
                confirmation_revision=confirmation["confirmation_revision"]
            ),
        )
        assert created.status_code == 202
        analysis_id = created.json()["analysis_id"]
        assert provider_started.wait(timeout=5)

        cancelled = client.delete(
            f"/api/files/{asset['asset_id']}/analyses/{analysis_id}",
            params={"scope_id": "chat-analysis"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert provider_cancelled.wait(timeout=2)
        persisted = client.get(
            f"/api/files/{asset['asset_id']}/analyses/{analysis_id}",
            params={"scope_id": "chat-analysis"},
        )
        assert persisted.json()["status"] == "cancelled"
        assert persisted.json()["result_artifact_id"] is None
