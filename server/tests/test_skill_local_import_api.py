from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skills.api import router as skills_router
from skills.api import set_skill_manager_for_tests
from skills.local_import import SkillLocalImportStore
from skills.local_import_api import (
    configure_skill_local_import,
    router as import_router,
)
from skills.skill_manager import SkillManager
from skills.trust_service import SkillTrustAcknowledgementStore, SkillTrustService


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
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "skill-tmp",
        local_import_store=store,
        trust_service=SkillTrustService(
            mode="enforce",
            acknowledgement_store=SkillTrustAcknowledgementStore(
                path=tmp_path / "trust-acknowledgements.json"
            ),
        ),
    )
    configure_skill_local_import(store)
    set_skill_manager_for_tests(manager)
    app = FastAPI()
    app.include_router(import_router)
    app.include_router(skills_router)
    with TestClient(app) as client:
        yield client, store, manager
    configure_skill_local_import(None)
    set_skill_manager_for_tests(None)


def test_status_is_readable_while_disabled(tmp_path: Path) -> None:
    configure_skill_local_import(
        SkillLocalImportStore(tmp_path / "imports", enabled=False)
    )
    app = FastAPI()
    app.include_router(import_router)
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
    client, _store, _manager = app_client
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

    trust_detail = client.get(
        f"/api/skills/trust/{created['receiptId']}"
    )
    assert trust_detail.status_code == 200, trust_detail.text
    assert trust_detail.json()["receipt"]["receiptId"] == created["receiptId"]
    assert trust_detail.json()["receipt"]["source"]["importId"] == import_id

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
    client, _store, _manager = app_client
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
    client, _store, _manager = app_client
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
    client, _store, _manager = app_client
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
    client, store, _manager = app_client
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


def test_install_endpoint_persists_projection_and_confirmation(app_client) -> None:
    client, store, manager = app_client
    created = client.post(
        "/api/skills/imports",
        data={
            "transport_kind": "folder",
            "paths_json": json.dumps(["SKILL.md", "scripts/check.py"]),
        },
        files=[
            (
                "files",
                (
                    "SKILL.md",
                    _markdown("local-script").replace(
                        b"1. Read the input.",
                        b"1. Run `python scripts/check.py`.",
                    ),
                    "text/markdown",
                ),
            ),
            ("files", ("check.py", b"print('ok')\n", "text/x-python")),
        ],
    ).json()

    pending = client.post(
        f"/api/skills/imports/{created['importId']}/install",
        json={
            "expected_revision": created["revision"],
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
            "confirmed": False,
        },
    )
    assert pending.status_code == 409
    assert pending.json()["detail"]["code"] == "skill_trust_ack_required"

    installed = client.post(
        f"/api/skills/imports/{created['importId']}/install",
        json={
            "expected_revision": created["revision"],
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
            "confirmed": True,
        },
    )
    assert installed.status_code == 200, installed.text
    payload = installed.json()
    assert payload["import"]["state"] == "installed"
    assert payload["installed"]["source_kind"] == "local_import"
    assert payload["installed"]["trust_activation_allowed"] is True
    assert manager.trust_service.acknowledgements.is_acknowledged(
        "local-script", created["trustFingerprint"]
    )
    assert store.require(created["importId"]).installed_skill_id == "local-script"


def test_uninstall_retry_repairs_import_projection_and_revokes_ack(app_client) -> None:
    client, store, manager = app_client
    created = client.post(
        "/api/skills/imports",
        data={
            "transport_kind": "folder",
            "paths_json": json.dumps(["SKILL.md", "scripts/check.py"]),
        },
        files=[
            (
                "files",
                (
                    "SKILL.md",
                    _markdown("local-uninstall-retry").replace(
                        b"1. Read the input.",
                        b"1. Run `python scripts/check.py`.",
                    ),
                    "text/markdown",
                ),
            ),
            ("files", ("check.py", b"print('ok')\n", "text/x-python")),
        ],
    ).json()
    installed = client.post(
        f"/api/skills/imports/{created['importId']}/install",
        json={
            "expected_revision": created["revision"],
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
            "confirmed": True,
        },
    )
    assert installed.status_code == 200, installed.text
    skill_id = installed.json()["installed"]["skill_id"]
    assert manager.trust_service.acknowledgements.is_acknowledged(
        skill_id, created["trustFingerprint"]
    )

    # Simulate a request interrupted after global files were removed but
    # before the Import Store projection and acknowledgement were cleared.
    manager.uninstall_skill(skill_id)
    retried = client.delete(f"/api/skills/{skill_id}")

    assert retried.status_code == 200, retried.text
    current = store.require(created["importId"])
    assert current.installed_skill_id is None
    assert current.state == "confirmation_required"
    assert not manager.trust_service.acknowledgements.is_acknowledged(
        skill_id, created["trustFingerprint"]
    )


def test_install_endpoint_requires_explicit_replace_digest(app_client) -> None:
    client, _store, _manager = app_client

    def upload(body: bytes) -> dict:
        return client.post(
            "/api/skills/imports",
            data={
                "transport_kind": "folder",
                "paths_json": json.dumps(["SKILL.md"]),
            },
            files=[("files", ("SKILL.md", body, "text/markdown"))],
        ).json()

    first = upload(_markdown("local-replace"))
    first_install = client.post(
        f"/api/skills/imports/{first['importId']}/install",
        json={
            "expected_revision": first["revision"],
            "expected_package_digest": first["packageDigest"],
            "expected_trust_fingerprint": first["trustFingerprint"],
        },
    ).json()
    second = upload(
        _markdown("local-replace").replace(b"Return the result", b"Return the revised result")
    )
    preview = client.get(f"/api/skills/imports/{second['importId']}")
    assert preview.status_code == 200, preview.text
    replacement = preview.json()["replacementPreview"]
    assert replacement["required"] is True
    assert replacement["allowed"] is True
    changed = next(
        item for item in replacement["changes"] if item["path"] == "SKILL.md"
    )
    assert changed["kind"] == "text"
    assert "revised result" in changed["diff"]

    required = client.post(
        f"/api/skills/imports/{second['importId']}/install",
        json={
            "expected_revision": second["revision"],
            "expected_package_digest": second["packageDigest"],
            "expected_trust_fingerprint": second["trustFingerprint"],
        },
    )
    assert required.status_code == 409
    assert required.json()["detail"]["code"] == "skill_import_replace_required"

    replaced = client.post(
        f"/api/skills/imports/{second['importId']}/install",
        json={
            "expected_revision": second["revision"],
            "expected_package_digest": second["packageDigest"],
            "expected_trust_fingerprint": second["trustFingerprint"],
            "expected_installed_digest": first_install["installed"]["content_digest"],
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["installed"]["content_digest"] == second["packageDigest"]


def test_install_storage_failure_uses_stable_service_error(
    app_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _store, manager = app_client
    created = client.post(
        "/api/skills/imports",
        data={
            "transport_kind": "folder",
            "paths_json": json.dumps(["SKILL.md"]),
        },
        files=[
            ("files", ("SKILL.md", _markdown("local-storage-failure"), "text/markdown"))
        ],
    ).json()

    def fail_metadata(_payload):
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_write_metadata", fail_metadata)
    response = client.post(
        f"/api/skills/imports/{created['importId']}/install",
        json={
            "expected_revision": created["revision"],
            "expected_package_digest": created["packageDigest"],
            "expected_trust_fingerprint": created["trustFingerprint"],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "skill_import_storage_unavailable"
