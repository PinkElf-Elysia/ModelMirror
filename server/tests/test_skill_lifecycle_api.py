from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.skills.api import (
    router,
    set_skill_lifecycle_service_for_tests,
    set_skill_lifecycle_store_for_tests,
    set_skill_manager_for_tests,
)
from server.skills.lifecycle import (
    SkillLifecycleMigrationService,
    SkillLifecycleStore,
)
from server.skills.package_validation import compute_package_digest
from server.skills.skill_manager import InstalledSkill, SkillManager
from server.skills.trust_scanner import SkillTrustTreeEntry, scan_skill_trust_receipt


def _client(tmp_path: Path, *, enabled: bool) -> tuple[TestClient, SkillLifecycleStore]:
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=enabled)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=store,
    )
    service = SkillLifecycleMigrationService(store=store, manager=manager)
    set_skill_manager_for_tests(manager)
    set_skill_lifecycle_service_for_tests(service)
    set_skill_lifecycle_store_for_tests(store)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), store


def teardown_function() -> None:
    set_skill_lifecycle_service_for_tests(None)
    set_skill_lifecycle_store_for_tests(None)
    set_skill_manager_for_tests(None)


def test_status_is_always_readable_and_disabled_migration_is_hidden(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, enabled=False)

    status = client.get("/api/skills/lifecycle/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["version"] == "skill-lifecycle-v1"
    assert client.get("/api/skills/lifecycle/migration").status_code == 200

    response = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": True}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "skill_lifecycle_disabled"


def test_migration_requires_explicit_confirmation(tmp_path: Path) -> None:
    client, store = _client(tmp_path, enabled=True)

    rejected = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": False}
    )
    assert rejected.status_code == 409
    assert (
        rejected.json()["detail"]["code"]
        == "skill_lifecycle_confirmation_required"
    )

    accepted = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": True}
    )
    assert accepted.status_code == 200
    assert accepted.json()["counts"]["total"] == 0
    assert store.status()["counts"]["versions"] == 0


def test_corrupt_installed_metadata_returns_structured_unavailable(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, enabled=True)
    metadata = tmp_path / "installed" / "installed.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("{not-json", encoding="utf-8")

    response = client.get("/api/skills/lifecycle/migration")
    assert response.status_code == 503
    assert (
        response.json()["detail"]["code"]
        == "skill_lifecycle_installed_metadata_unavailable"
    )
    assert client.get("/api/skills/installed").status_code == 503
    assert metadata.read_text(encoding="utf-8") == "{not-json"


def test_versions_endpoint_returns_current_immutable_snapshot(tmp_path: Path) -> None:
    client, store = _client(tmp_path, enabled=True)
    files = {
        "SKILL.md": b"---\nname: api-skill\ndescription: API lifecycle test.\n---\n\n# API\n",
        "references/a.txt": b"a\n",
    }
    digest = compute_package_digest(
        files["SKILL.md"], {"references/a.txt": files["references/a.txt"]}
    )
    installed = InstalledSkill(
        skill_id="api-skill",
        name="api-skill",
        description="API lifecycle test.",
        repo_url="https://github.com/example/skills.git",
        sub_path="api-skill",
        installed_at=1.0,
        source_ref="a" * 40,
        content_digest=digest,
        trust_state="receipt_matched",
        trust_receipt_id="receipt-api",
        trust_fingerprint="b" * 64,
        trust_status="verified",
        trust_install_policy="allow",
        trust_compatibility_status="portable",
        trust_router_eligible=True,
    )
    state = store.record_migrated_current(installed=installed, files=files)

    response = client.get("/api/skills/api-skill/versions")

    assert response.status_code == 200
    assert response.json()["state"]["current_version_id"] == state.current_version_id
    assert response.json()["versions"][0]["package_digest"] == digest
    assert "trust_receipt_snapshot" not in response.json()["versions"][0]
    assert response.json()["versions"][0]["trust_evidence_frozen"] is False


def test_lifecycle_states_include_uninstalled_recovery_history(tmp_path: Path) -> None:
    client, store = _client(tmp_path, enabled=True)
    files = {
        "SKILL.md": b"---\nname: recovery-skill\ndescription: Recover one Skill version.\n---\n\n# Recovery\n",
    }
    digest = compute_package_digest(files["SKILL.md"], {})
    installed = InstalledSkill(
        skill_id="recovery-skill",
        name="recovery-skill",
        description="Recover one Skill version.",
        repo_url="workspace://draft/recovery",
        sub_path="",
        installed_at=1.0,
        source_kind="workspace_draft",
        source_id="draft-recovery",
        source_revision=1,
        content_digest=digest,
    )
    active = store.record_migrated_current(installed=installed, files=files)
    state = store.mark_uninstalled(
        "recovery-skill", expected_revision=active.revision
    )

    response = client.get("/api/skills/lifecycle/skills")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"]["enabled"] is True
    assert len(payload["items"]) == 1
    assert payload["items"][0]["skill_id"] == "recovery-skill"
    assert payload["items"][0]["status"] == "uninstalled"
    assert payload["items"][0]["current_version_id"] is None
    assert payload["items"][0]["recovery_version_id"] == state.recovery_version_id


def test_rollback_endpoint_switches_one_exact_git_version(tmp_path: Path) -> None:
    client, store = _client(tmp_path, enabled=True)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        lifecycle_store=store,
    )
    set_skill_manager_for_tests(manager)

    def make_version(marker: str, commit: str, tree: str):
        files = {
            "SKILL.md": (
                "---\nname: api-skill\n"
                "description: Use this API Skill for exact rollback tests.\n"
                "---\n\n# API Skill\n\n"
                f"Return `{marker}`.\n"
            ).encode()
        }
        receipt = scan_skill_trust_receipt(
            repo_url="https://github.com/example/skills.git",
            sub_path="api-skill",
            verified_commit=commit,
            directory_tree_sha=tree,
            entries=[
                SkillTrustTreeEntry(
                    path="SKILL.md",
                    mode="100644",
                    object_type="blob",
                    object_id=hashlib.sha1(files["SKILL.md"]).hexdigest(),
                    size=len(files["SKILL.md"]),
                    content=files["SKILL.md"],
                )
            ],
        )
        installed = InstalledSkill(
            skill_id="api-skill",
            name="api-skill",
            description="Use this API Skill for exact rollback tests.",
            repo_url="https://github.com/example/skills.git",
            sub_path="api-skill",
            installed_at=1.0,
            source_ref=commit,
            content_digest=receipt["packageDigest"],
            trust_state="receipt_matched",
            trust_receipt_id=receipt["receiptId"],
            trust_fingerprint=receipt["trustFingerprint"],
            trust_risk_level=receipt["riskLevel"],
            trust_status=receipt["trustStatus"],
            trust_install_policy=receipt["installPolicy"],
            trust_compatibility_status=receipt["compatibilityStatus"],
            trust_router_eligible=receipt["routerEligible"],
            trust_package_digest=receipt["packageDigest"],
            trust_directory_tree_sha=receipt["directoryTreeSha"],
        )
        return files, receipt, installed

    first_files, first_receipt, first_installed = make_version(
        "one", "a" * 40, "b" * 40
    )
    first_state = store.record_migrated_current(
        installed=first_installed,
        files=first_files,
        trust_receipt_snapshot=first_receipt,
    )
    second_files, second_receipt, second_installed = make_version(
        "two", "c" * 40, "d" * 40
    )
    second_state = store.record_migrated_current(
        installed=second_installed,
        files=second_files,
        trust_receipt_snapshot=second_receipt,
    )
    target = manager.installed_dir / "api-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(second_files["SKILL.md"])
    manager._write_metadata({"api-skill": asdict(second_installed)})
    first_version = store.require_version(first_state.current_version_id or "")

    response = client.post(
        f"/api/skills/api-skill/versions/{first_version.version_id}/rollback",
        json={
            "expected_state_revision": second_state.revision,
            "expected_current_version_id": second_state.current_version_id,
            "expected_package_digest": first_version.package_digest,
            "confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"]["current_version_id"] == first_version.version_id
    assert "`one`" in manager.get_skill_content("api-skill")
