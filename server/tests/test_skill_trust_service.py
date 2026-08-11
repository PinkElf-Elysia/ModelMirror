from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from server.skills.api import (
    SkillInstallRequest,
    get_skill_content as get_skill_content_api,
    install_skill as install_skill_api,
    set_skill_manager_for_tests,
)
from server.skills.skill_manager import InstalledSkill, SkillManager, SkillValidationError
from server.skills.finder import SkillFinder, _fingerprint
from server.skills.trust_scanner import (
    SkillTrustTreeEntry,
    build_skill_trust_index,
    scan_skill_trust_receipt,
    sha256_json,
)
from server.skills.trust_service import (
    SkillRuntimeEnvironment,
    SkillTrustAcknowledgementStore,
    SkillTrustError,
    SkillTrustService,
)
from server.xpert_runtime import (
    RuntimeToolCall,
    RuntimeToolError,
    SandboxToolsetProvider,
    SandboxWorkspaceStore,
)


class _SandboxClient:
    async def request(self, payload: dict[str, object]) -> dict[str, object]:
        assert payload.get("action") == "ensure_workspace"
        return {"ok": True}


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(
    tmp_path: Path,
    *,
    with_script: bool = False,
    suspicious_binary: bool = False,
) -> tuple[Path, str, str]:
    repo = tmp_path / (
        "binary-repo"
        if suspicious_binary
        else "script-repo"
        if with_script
        else "safe-repo"
    )
    skill_dir = repo / "skills" / "safe-skill"
    skill_dir.mkdir(parents=True)
    body = (
        "## Workflow\n\n1. Run `python scripts/render.py`.\n2. Return the result.\n"
        if with_script
        else "## Workflow\n\n1. Read the input.\n2. Return the result.\n"
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: safe-skill\n"
        "description: Deterministic local guidance for a bounded task.\n"
        "---\n\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    if with_script:
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        (scripts / "render.py").write_text(
            "print('ok')\n", encoding="utf-8", newline="\n"
        )
    if suspicious_binary:
        assets = skill_dir / "assets"
        assets.mkdir()
        (assets / "payload.dat").write_bytes(b"\x00\xff\x00\xff")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "trust@example.com")
    _git(repo, "config", "user.name", "Trust Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD:skills/safe-skill")
    return repo, commit, tree


def _write_index(
    tmp_path: Path,
    repo: Path,
    commit: str,
    tree: str,
) -> tuple[Path, dict]:
    package_dir = repo / "skills" / "safe-skill"
    entries = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        entries.append(
            SkillTrustTreeEntry(
                path=path.relative_to(package_dir).as_posix(),
                mode="100644",
                object_type="blob",
                object_id=hashlib.sha1(content).hexdigest(),
                size=len(content),
                content=content,
            )
        )
    source = {
        "repoUrl": str(repo),
        "subPath": "skills/safe-skill",
        "verifiedCommit": commit,
    }
    candidate = {
        "candidateId": "catalog:project:safe-skill",
        "installSource": source,
    }
    receipt = scan_skill_trust_receipt(
        repo_url=source["repoUrl"],
        sub_path=source["subPath"],
        verified_commit=commit,
        directory_tree_sha=tree,
        entries=entries,
    )
    index = build_skill_trust_index(candidates=[candidate], receipts=[receipt])
    path = tmp_path / "skill_trust_index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path, receipt


def _service(tmp_path: Path, index_path: Path, *, mode: str) -> SkillTrustService:
    return SkillTrustService(
        index_path=index_path,
        mode=mode,
        acknowledgement_store=SkillTrustAcknowledgementStore(
            path=tmp_path / f"ack-{mode}.json"
        ),
    )


def _runtime_index(
    path: Path,
    *,
    receipt: dict,
    trust_index_fingerprint: str,
) -> dict:
    source = dict(receipt["source"])
    payload = {
        "candidateId": "catalog:project:safe-skill",
        "sourceType": "catalog",
        "targetType": "project",
        "sourceId": "safe-skill",
        "name": "Safe Skill",
        "category": "Utilities",
        "kind": "skill",
        "description": "Deterministic local guidance.",
        "sourceDescription": "Deterministic local guidance.",
        "searchDescription": "Deterministic local guidance.",
        "tags": ["safe", "local"],
        "includedSkills": [],
        "pathTerms": ["safe-skill"],
        "parentNames": [],
        "publisher": "Fixture",
        "sourceGroup": "Fixture",
        "parentSkillSets": [],
        "installSource": source,
        "directoryTreeSha": receipt["directoryTreeSha"],
        "trust": {
            key: receipt[key]
            for key in (
                "receiptId",
                "trustFingerprint",
                "riskLevel",
                "trustStatus",
                "installPolicy",
                "compatibilityStatus",
                "routerEligible",
            )
        },
    }
    candidate = {
        **payload,
        "candidateFingerprint": _fingerprint(payload),
        "stableNameOrder": 0,
    }
    fingerprint_payload = {
        "version": 2,
        "rankerVersion": "skill-need-local-v3",
        "memberIndexFingerprint": "fixture",
        "catalogFingerprint": "3" * 64,
        "trustIndexFingerprint": trust_index_fingerprint,
        "supersededCandidateIds": [],
        "candidates": [candidate],
    }
    index = {
        **fingerprint_payload,
        "fingerprint": _fingerprint(fingerprint_payload),
    }
    path.write_text(json.dumps(index), encoding="utf-8")
    return candidate


def test_low_risk_enforce_install_and_exact_checkout(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    service = _service(tmp_path, index_path, mode="enforce")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=service,
    )

    installed = manager.install_skill(
        str(repo), "skills/safe-skill", commit
    )

    assert installed.trust_state == "receipt_matched"
    assert installed.trust_receipt_id == receipt["receiptId"]
    assert installed.trust_package_digest == receipt["packageDigest"]
    assert installed.trust_directory_tree_sha == tree
    assert manager.require_activation(installed.skill_id).skill_id == installed.skill_id
    assert not (tmp_path / "installed" / installed.skill_id / ".git").exists()


def test_conditional_receipt_requires_exact_acknowledgement(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path, with_script=True)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    service = _service(tmp_path, index_path, mode="enforce")
    skill_id = SkillManager._build_skill_id(str(repo), "skills/safe-skill")

    with pytest.raises(SkillTrustError) as denied:
        service.install_decision(
            skill_id=skill_id,
            repo_url=str(repo),
            sub_path="skills/safe-skill",
            source_ref=commit,
        )
    assert denied.value.code == "skill_trust_ack_required"

    service.acknowledge(
        skill_id=skill_id,
        trust_fingerprint=receipt["trustFingerprint"],
        confirmed=True,
    )
    decision, _ = service.install_decision(
        skill_id=skill_id,
        repo_url=str(repo),
        sub_path="skills/safe-skill",
        source_ref=commit,
    )
    assert decision.allowed is True
    assert decision.acknowledgement_satisfied is True
    service.revoke(skill_id)
    with pytest.raises(SkillTrustError) as revoked:
        service.install_decision(
            skill_id=skill_id,
            repo_url=str(repo),
            sub_path="skills/safe-skill",
            source_ref=commit,
        )
    assert revoked.value.code == "skill_trust_ack_required"


def test_suspicious_installable_skill_allows_manual_ack_but_router_rejects(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path, suspicious_binary=True)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    service = _service(tmp_path, index_path, mode="enforce")
    skill_id = SkillManager._build_skill_id(str(repo), "skills/safe-skill")
    candidate = {
        "candidateId": "catalog:project:safe-skill",
        "installSource": dict(receipt["source"]),
        "trust": {
            key: receipt[key]
            for key in (
                "receiptId",
                "trustFingerprint",
                "riskLevel",
                "trustStatus",
                "installPolicy",
                "compatibilityStatus",
                "routerEligible",
            )
        },
    }

    assert receipt["riskLevel"] == "critical"
    assert receipt["installPolicy"] == "confirm"
    assert receipt["routerEligible"] is False
    with pytest.raises(SkillTrustError) as router_denied:
        service.candidate_decision(
            candidate,
            skill_id=skill_id,
            require_router_eligible=True,
        )
    assert router_denied.value.code == "skill_trust_policy_blocked"

    service.acknowledge(
        skill_id=skill_id,
        trust_fingerprint=receipt["trustFingerprint"],
        confirmed=True,
    )
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=service,
    )
    installed = manager.install_skill(str(repo), "skills/safe-skill", commit)
    assert installed.trust_risk_level == "critical"
    assert manager.require_activation(installed.skill_id).skill_id == installed.skill_id


def test_audit_records_missing_receipt_while_enforce_fails_closed(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    audit = _service(tmp_path, missing, mode="audit")
    decision, receipt = audit.install_decision(
        skill_id="safe-skill",
        repo_url="https://github.com/example/skills",
        sub_path="safe-skill",
        source_ref="a" * 40,
    )
    assert decision.allowed is True
    assert decision.reason_codes == ("skill_trust_index_unavailable",)
    assert receipt is None

    enforce = _service(tmp_path, missing, mode="enforce")
    with pytest.raises(SkillTrustError) as denied:
        enforce.install_decision(
            skill_id="safe-skill",
            repo_url="https://github.com/example/skills",
            sub_path="safe-skill",
            source_ref="a" * 40,
        )
    assert denied.value.code == "skill_trust_index_unavailable"


def test_tampered_index_is_unavailable_without_breaking_audit_mode(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, _receipt = _write_index(tmp_path, repo, commit, tree)
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["catalogFingerprint"] = "f" * 64
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    enforce = _service(tmp_path, index_path, mode="enforce")
    with pytest.raises(SkillTrustError) as denied:
        enforce.resolve_source(str(repo), "skills/safe-skill", commit)
    assert denied.value.code == "skill_trust_index_unavailable"

    audit = _service(tmp_path, index_path, mode="audit")
    decision, receipt = audit.install_decision(
        skill_id="safe-skill",
        repo_url=str(repo),
        sub_path="skills/safe-skill",
        source_ref=commit,
    )
    assert decision.allowed is True
    assert receipt is None


def test_exact_legacy_install_is_migrated_idempotently(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    installed_dir = tmp_path / "installed"
    off_manager = SkillManager(
        installed_dir=installed_dir,
        tmp_dir=tmp_path / "tmp-off",
        allow_local_repos=True,
        trust_service=_service(tmp_path, index_path, mode="off"),
    )
    legacy = off_manager.install_skill(str(repo), "skills/safe-skill", commit)
    assert legacy.trust_state == "off"

    enforce_manager = SkillManager(
        installed_dir=installed_dir,
        tmp_dir=tmp_path / "tmp-enforce",
        allow_local_repos=True,
        trust_service=_service(tmp_path, index_path, mode="enforce"),
    )
    migrated = enforce_manager.list_installed_skills()[0]
    assert migrated.trust_state == "receipt_matched"
    assert migrated.trust_fingerprint == receipt["trustFingerprint"]
    first_verified_at = migrated.trust_verified_at
    assert enforce_manager.list_installed_skills()[0].trust_verified_at == first_verified_at


def test_runtime_capability_check_and_ephemeral_router_authorization(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path, with_script=True)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    service = _service(tmp_path, index_path, mode="enforce")
    source = receipt["source"]
    environment = SkillRuntimeEnvironment(
        tool_names=frozenset({"skill_read", "skill_stage"}),
        sandbox_commands=frozenset(),
    )

    with pytest.raises(SkillTrustError) as incompatible:
        service.install_decision(
            skill_id="safe-skill",
            repo_url=source["repoUrl"],
            sub_path=source["subPath"],
            source_ref=source["verifiedCommit"],
            ephemeral_trust_fingerprint=receipt["trustFingerprint"],
            environment=environment,
        )
    assert incompatible.value.code == "skill_runtime_incompatible"

    compatible = SkillRuntimeEnvironment(
        tool_names=frozenset(
            {"skill_read", "skill_stage", "sandbox_shell"}
        ),
        sandbox_commands=frozenset({"python"}),
    )
    decision, _ = service.install_decision(
        skill_id="safe-skill",
        repo_url=source["repoUrl"],
        sub_path=source["subPath"],
        source_ref=source["verifiedCommit"],
        ephemeral_trust_fingerprint=receipt["trustFingerprint"],
        environment=compatible,
    )
    assert decision.allowed is True


def test_blocked_receipt_and_changed_candidate_fail_closed(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    blocked = index["receipts"][0]
    blocked.update(
        {
            "riskLevel": "critical",
            "trustStatus": "blocked",
            "installPolicy": "block",
            "compatibilityStatus": "unsupported",
            "routerEligible": False,
            "findings": [
                {
                    "code": "trust_scan_incomplete",
                    "severity": "critical",
                    "message": "Trust scan is incomplete.",
                }
            ],
        }
    )
    blocked["trustFingerprint"] = sha256_json(
        {key: value for key, value in blocked.items() if key != "trustFingerprint"}
    )
    index["fingerprint"] = sha256_json(
        {key: value for key, value in index.items() if key != "fingerprint"}
    )
    index_path.write_text(json.dumps(index), encoding="utf-8")
    service = _service(tmp_path, index_path, mode="enforce")
    with pytest.raises(SkillTrustError) as denied:
        service.install_decision(
            skill_id="safe-skill",
            repo_url=str(repo),
            sub_path="skills/safe-skill",
            source_ref=commit,
        )
    assert denied.value.code == "skill_trust_policy_blocked"

    candidate = {
        "candidateId": "catalog:project:safe-skill",
        "installSource": receipt["source"],
        "trust": {
            "receiptId": receipt["receiptId"],
            "trustFingerprint": "0" * 64,
            "riskLevel": receipt["riskLevel"],
            "trustStatus": receipt["trustStatus"],
            "installPolicy": receipt["installPolicy"],
            "compatibilityStatus": receipt["compatibilityStatus"],
            "routerEligible": receipt["routerEligible"],
        },
    }
    with pytest.raises(SkillTrustError) as stale:
        service.candidate_decision(candidate)
    assert stale.value.code == "skill_trust_candidate_stale"


def test_package_mismatch_blocks_and_legacy_is_not_deleted(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, _receipt = _write_index(tmp_path, repo, commit, tree)
    service = _service(tmp_path, index_path, mode="enforce")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=service,
    )
    installed = manager.install_skill(str(repo), "skills/safe-skill", commit)
    assert (
        manager.require_activation(installed.skill_id).trust_state
        == "receipt_matched"
    )
    skill_md = manager.get_skill_directory(installed.skill_id) / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\ntampered\n")

    assert skill_md.exists()
    with pytest.raises(SkillValidationError) as denied:
        manager.require_activation(installed.skill_id)
    assert denied.value.code == "skill_trust_receipt_missing"
    assert (
        manager.get_installed_skill(installed.skill_id).trust_state
        == "unverified_legacy"
    )


@pytest.mark.asyncio
async def test_router_approval_authorizes_only_the_current_run(tmp_path: Path) -> None:
    repo, commit, tree = _repo(tmp_path, with_script=True)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    trust_index = json.loads(index_path.read_text(encoding="utf-8"))
    runtime_path = tmp_path / "runtime-index.json"
    candidate = _runtime_index(
        runtime_path,
        receipt=receipt,
        trust_index_fingerprint=trust_index["fingerprint"],
    )
    service = _service(tmp_path, index_path, mode="enforce")
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=service,
    )
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "runtime", workspace_root=tmp_path / "workspaces"
        ),
        _SandboxClient(),
        skill_manager=manager,
        skill_finder=SkillFinder(index_path=runtime_path, skill_manager=manager),
        trust_service=service,
    )
    arguments = {
        "candidate_id": candidate["candidateId"],
        "candidate_fingerprint": candidate["candidateFingerprint"],
    }
    base_metadata = {
        "task_id": "task-1",
        "run_id": "run-1",
        "node_id": "agent-1",
        "skills_config": {
            "catalog_search": True,
            "catalog_install": True,
            "max_catalog_installs": 3,
        },
        "active_skill_ids": [],
        "denied_skill_candidate_ids": [],
        "catalog_install_count": 0,
        "sandbox_config": {"allowed_commands": "python,rg"},
        "skill_runtime_environment": {
            "tool_names": [
                "skill_read",
                "skill_stage",
                "skill_install",
                "sandbox_shell",
            ],
            "tool_providers": ["skill", "sandbox"],
        },
    }
    install_call = RuntimeToolCall(
        "skill_install", arguments, dict(base_metadata)
    )
    provider.prepare_call(install_call)
    assert install_call.metadata["skill_approval"]["trust"]["allowed"] is True
    result = await provider.call_tool(install_call)
    authorization = result.metadata["trust_authorization"]
    skill_id = authorization["skill_id"]
    assert service.acknowledgements.get(skill_id) is None

    without_run_auth = {
        **base_metadata,
        "active_skill_ids": [skill_id],
        "catalog_install_count": 1,
    }
    with pytest.raises(RuntimeToolError) as denied:
        await provider.call_tool(
            RuntimeToolCall(
                "skill_read", {"skill_id": skill_id}, without_run_auth
            )
        )
    assert denied.value.code == "skill_trust_ack_required"

    with_run_auth = {
        **without_run_auth,
        "skill_trust_authorizations": {
            skill_id: authorization["trust_fingerprint"]
        },
    }
    read = await provider.call_tool(
        RuntimeToolCall("skill_read", {"skill_id": skill_id}, with_run_auth)
    )
    assert "safe-skill" in read.output


@pytest.mark.asyncio
async def test_install_api_keeps_request_shape_and_returns_structured_trust_error(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, _receipt = _write_index(tmp_path, repo, commit, tree)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=_service(tmp_path, index_path, mode="enforce"),
    )
    set_skill_manager_for_tests(manager)
    try:
        with pytest.raises(HTTPException) as denied:
            await install_skill_api(
                SkillInstallRequest(
                    repo_url=str(repo),
                    sub_path="skills/safe-skill",
                )
            )
    finally:
        set_skill_manager_for_tests(None)
    assert denied.value.status_code == 400
    assert denied.value.detail["code"] == "skill_trust_receipt_missing"
    assert set(denied.value.detail) == {"code", "message", "details"}


@pytest.mark.asyncio
async def test_content_view_remains_available_but_chat_activation_is_gated(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, _receipt = _write_index(tmp_path, repo, commit, tree)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        trust_service=_service(tmp_path, index_path, mode="enforce"),
    )
    installed = manager.install_skill(str(repo), "skills/safe-skill", commit)
    package = manager.get_skill_directory(installed.skill_id)
    (package / "SKILL.md").write_text(
        (package / "SKILL.md").read_text(encoding="utf-8") + "\ntampered\n",
        encoding="utf-8",
    )
    manager._trust_reconciled = False
    set_skill_manager_for_tests(manager)
    try:
        viewed = await get_skill_content_api(installed.skill_id, purpose="view")
        assert "tampered" in viewed.content
        with pytest.raises(HTTPException) as blocked:
            await get_skill_content_api(installed.skill_id, purpose="activate")
    finally:
        set_skill_manager_for_tests(None)
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "skill_trust_receipt_missing"


@pytest.mark.asyncio
async def test_finder_keeps_installed_non_git_skill_actionable_in_enforce_mode(
    tmp_path: Path,
) -> None:
    repo, commit, tree = _repo(tmp_path)
    index_path, receipt = _write_index(tmp_path, repo, commit, tree)
    trust_index = json.loads(index_path.read_text(encoding="utf-8"))
    runtime_path = tmp_path / "runtime-index.json"
    _runtime_index(
        runtime_path,
        receipt=receipt,
        trust_index_fingerprint=trust_index["fingerprint"],
    )
    local_skill = InstalledSkill(
        skill_id="workspace-local-formatter",
        name="Workspace Local Formatter",
        description="Format local notes without external access.",
        repo_url="workspace://draft-local",
        sub_path="",
        installed_at=1.0,
        source_kind="workspace_draft",
    )

    class _InstalledOnlyManager:
        trust_service = _service(tmp_path, index_path, mode="enforce")

        @staticmethod
        def list_installed_skills() -> list[InstalledSkill]:
            return [local_skill]

        @staticmethod
        def get_installed_skill(skill_id: str) -> InstalledSkill:
            assert skill_id == local_skill.skill_id
            return local_skill

    manager = _InstalledOnlyManager()
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "runtime", workspace_root=tmp_path / "workspaces"
        ),
        _SandboxClient(),
        skill_manager=manager,
        skill_finder=SkillFinder(index_path=runtime_path, skill_manager=manager),
        trust_service=manager.trust_service,
    )
    result = await provider.call_tool(
        RuntimeToolCall(
            "skill_find",
            {"need": "format local notes"},
            {
                "task_id": "task-local",
                "run_id": "run-local",
                "node_id": "agent-local",
                "skills_config": {"catalog_search": True},
                "active_skill_ids": [],
            },
        )
    )
    dynamic = next(
        item
        for item in json.loads(result.output)["results"]
        if item["candidateId"] == f"installed:{local_skill.skill_id}"
    )
    assert dynamic["trustDecision"]["trustStatus"] == "not_applicable"
    assert dynamic["trustActionable"] is True
