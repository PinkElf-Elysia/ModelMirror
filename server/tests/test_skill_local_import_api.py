from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skills.local_import import SkillLocalImportStore
from skills.local_import_api import configure_skill_local_import, router


def _markdown(name: str = "upload-example") -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use this uploaded Skill for a bounded local task.\n"
        "---\n\n"
        "## Workflow\n\n1. Read the input.\n2. Return the result.\n"
    ).encode()


def _zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, content in files.items():
            bundle.writestr(path, content)
    return output.getvalue()


@pytest.fixture()
def app_client(tmp_path: Path):
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    configure_skill_local_import(store)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store
    configure_skill_local_import(None)


def test_status_is_readable_while_disabled(tmp_path: Path) -> None:
    configure_skill_local_import(
        SkillLocalImportStore(tmp_path / "imports", enabled=False)
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        status = client.get("/api/skills/imports/status")
        listing = client.get("/api/skills/imports")
    configure_skill_local_import(None)

    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["available"] is True
    assert listing.status_code == 404
    assert listing.json()["detail"]["code"] == "skill_import_disabled"


def test_zip_upload_detail_preview_rescan_and_delete(app_client) -> None:
    client, _store = app_client
    response = client.post(
        "/api/skills/imports",
        data={"transport_kind": "zip"},
        files={
            "archive": (
                "skill.zip",
                _zip(
                    {
                        "wrapper/SKILL.md": _markdown(),
                        "wrapper/references/guide.md": b"# Guide\n",
                    }
                ),
                "application/zip",
            )
        },
    )

    assert response.status_code == 200, response.text
    created = response.json()
    assert created["state"] == "ready"
    assert created["transportKind"] == "zip"
    assert created["localSkillId"] == "upload-example"
    import_id = created["importId"]

    listing = client.get("/api/skills/imports")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert "trustReceipt" not in listing.json()["imports"][0]

    detail = client.get(f"/api/skills/imports/{import_id}")
    assert detail.status_code == 200
    assert detail.json()["trustReceipt"]["source"]["kind"] == "local_import"

    preview = client.get(
        f"/api/skills/imports/{import_id}/file",
        params={"path": "references/guide.md"},
    )
    assert preview.status_code == 200
    assert preview.json()["content"] == "# Guide\n"

    rescan = client.post(
        f"/api/skills/imports/{import_id}/rescan",
        json={
            "expected_revision": created["revision"],
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
        },
    )
    assert rescan.status_code == 200
    rescanned = rescan.json()
    assert rescanned["revision"] == created["revision"] + 1

    deleted = client.request(
        "DELETE",
        f"/api/skills/imports/{import_id}",
        json={
            "expected_revision": rescanned["revision"],
            "expected_package_digest": rescanned["packageDigest"],
            "expected_trust_fingerprint": rescanned["trustFingerprint"],
        },
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/skills/imports/{import_id}").status_code == 404


def test_folder_upload_uses_server_validated_path_manifest(app_client) -> None:
    client, _store = app_client
    response = client.post(
        "/api/skills/imports",
        data={
            "transport_kind": "folder",
            "paths_json": json.dumps(
                ["folder/SKILL.md", "folder/assets/pixel.png"]
            ),
        },
        files=[
            ("files", ("ignored-client-name.md", _markdown(), "text/markdown")),
            ("files", ("ignored.png", b"\x89PNG\r\n\x1a\nrest", "image/png")),
        ],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "confirmation_required"
    assert {item["path"] for item in payload["fileManifest"]} == {
        "SKILL.md",
        "assets/pixel.png",
    }
    binary_preview = client.get(
        f"/api/skills/imports/{payload['importId']}/file",
        params={"path": "assets/pixel.png"},
    )
    assert binary_preview.status_code == 400
    assert binary_preview.json()["detail"]["code"] == "skill_import_invalid_transport"


def test_folder_manifest_mismatch_and_traversal_are_structured(app_client) -> None:
    client, _store = app_client
    mismatch = client.post(
        "/api/skills/imports",
        data={"transport_kind": "folder", "paths_json": json.dumps([])},
        files=[("files", ("SKILL.md", _markdown(), "text/markdown"))],
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"]["code"] == "skill_import_invalid_transport"

    traversal = client.post(
        "/api/skills/imports",
        data={
            "transport_kind": "folder",
            "paths_json": json.dumps(["../SKILL.md"]),
        },
        files=[("files", ("SKILL.md", _markdown(), "text/markdown"))],
    )
    assert traversal.status_code == 400
    assert traversal.json()["detail"]["code"] == "skill_import_path_unsafe"


def test_wrong_optimistic_receipt_returns_structured_conflict(app_client) -> None:
    client, _store = app_client
    created = client.post(
        "/api/skills/imports",
        data={"transport_kind": "zip"},
        files={
            "archive": (
                "skill.zip",
                _zip({"SKILL.md": _markdown()}),
                "application/zip",
            )
        },
    ).json()

    response = client.post(
        f"/api/skills/imports/{created['importId']}/rescan",
        json={
            "expected_revision": created["revision"] + 1,
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "skill_import_stale"


def test_blocked_upload_does_not_echo_or_retain_secret(app_client) -> None:
    client, store = app_client
    secret = "sk-" + "apiuploadexampletoken123456789012345"
    response = client.post(
        "/api/skills/imports",
        data={"transport_kind": "folder", "paths_json": json.dumps(["SKILL.md"])},
        files=[
            (
                "files",
                (
                    "SKILL.md",
                    _markdown().replace(b"1. Read the input.", f"1. TOKEN={secret}".encode()),
                    "text/markdown",
                ),
            )
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "blocked"
    assert payload["fileManifest"] == []
    assert secret not in response.text
    assert not (store.packages_root / payload["packageDigest"]).exists()
