from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.skills.finder import SkillFinder, _fingerprint
from server.skills.skill_manager import SkillManager
from server.xpert_runtime import (
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeApprovalStore,
    RuntimeInterrupt,
    RuntimeMiddlewareSpec,
    RuntimeToolCall,
    RuntimeToolError,
    SandboxToolsetProvider,
    SandboxWorkspaceStore,
    build_human_in_the_loop_middleware,
)
from server.xpert_runtime.models import ToolCallRequest, ToolCallResponse


class FakeSandboxClient:
    async def request(self, payload: dict[str, object]) -> dict[str, object]:
        assert payload.get("action") == "ensure_workspace"
        return {"ok": True}


def create_skill_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "catalog-source"
    skill_dir = repo / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: PDF Extractor\ndescription: 提取 PDF 合同字段\n---\n\n# PDF\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    source_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, source_ref


def write_runtime_index(path: Path, repo: Path, source_ref: str) -> dict[str, object]:
    candidate_payload: dict[str, object] = {
        "candidateId": "catalog:project:pdf-extractor",
        "sourceType": "catalog",
        "targetType": "project",
        "sourceId": "pdf-extractor",
        "name": "PDF Extractor",
        "category": "内容与办公",
        "kind": "skill",
        "description": "提取 PDF 合同字段",
        "sourceDescription": "Extract PDF contract fields.",
        "searchDescription": "Extract PDF contract fields.",
        "tags": ["pdf", "合同", "提取"],
        "includedSkills": [],
        "pathTerms": ["skills", "pdf"],
        "parentNames": [],
        "publisher": "Fixture",
        "sourceGroup": "测试目录",
        "parentSkillSets": [],
        "installSource": {
            "repoUrl": str(repo),
            "subPath": "skills/pdf",
            "verifiedCommit": source_ref,
        },
        "directoryTreeSha": None,
    }
    candidate = {
        **candidate_payload,
        "candidateFingerprint": _fingerprint(candidate_payload),
        "stableNameOrder": 0,
    }
    fingerprint_payload = {
        "version": 1,
        "rankerVersion": "skill-need-local-v3",
        "memberIndexFingerprint": "fixture",
        "supersededCandidateIds": [],
        "candidates": [candidate],
    }
    index = {**fingerprint_payload, "fingerprint": _fingerprint(fingerprint_payload)}
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return candidate


def provider_fixture(tmp_path: Path):
    repo, source_ref = create_skill_repo(tmp_path)
    index_path = tmp_path / "runtime-index.json"
    candidate = write_runtime_index(index_path, repo, source_ref)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        git_timeout_seconds=20,
    )
    finder = SkillFinder(index_path=index_path, skill_manager=manager)
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "runtime", workspace_root=tmp_path / "workspaces"
        ),
        FakeSandboxClient(),
        skill_manager=manager,
        skill_finder=finder,
    )
    return provider, manager, candidate


def metadata(**updates: object) -> dict[str, object]:
    return {
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
        **updates,
    }


@pytest.mark.asyncio
async def test_find_install_activate_read_and_install_limit(tmp_path: Path) -> None:
    provider, manager, candidate = provider_fixture(tmp_path)
    found = await provider.call_tool(
        RuntimeToolCall("skill_find", {"need": "提取 PDF 合同"}, metadata())
    )
    payload = json.loads(found.output)
    assert payload["results"][0]["availability"] == "missing"
    assert found.metadata["query_hash"]
    assert "need" not in found.metadata

    arguments = {
        "candidate_id": candidate["candidateId"],
        "candidate_fingerprint": candidate["candidateFingerprint"],
    }
    call = RuntimeToolCall("skill_install", arguments, metadata())
    provider.prepare_call(call)
    assert call.metadata["skill_approval"]["target_sha"] == candidate["installSource"]["verifiedCommit"]
    installed = await provider.call_tool(call)
    installed_payload = json.loads(installed.output)
    skill_id = installed_payload["activated_skill_id"]
    assert installed.metadata["catalog_install_increment"] == 1
    assert manager.list_installed_skills()[0].source_ref == candidate["installSource"]["verifiedCommit"]

    with pytest.raises(RuntimeToolError) as next_run_denied:
        await provider.call_tool(
            RuntimeToolCall(
                "skill_read",
                {"skill_id": skill_id},
                metadata(catalog_install_count=1),
            )
        )
    assert next_run_denied.value.code == "skill_denied"

    active_metadata = metadata(active_skill_ids=[skill_id], catalog_install_count=1)
    read = await provider.call_tool(
        RuntimeToolCall("skill_read", {"skill_id": skill_id}, active_metadata)
    )
    assert "PDF Extractor" in read.output
    enabled = await provider.call_tool(
        RuntimeToolCall("skill_enable", arguments, metadata())
    )
    assert enabled.metadata["activated_skill_id"] == skill_id

    with pytest.raises(RuntimeToolError) as limited:
        provider.prepare_call(
            RuntimeToolCall(
                "skill_install", arguments, metadata(catalog_install_count=3)
            )
        )
    assert limited.value.code == "skill_install_limit"


def test_candidate_fingerprint_and_rejection_are_enforced(tmp_path: Path) -> None:
    provider, _manager, candidate = provider_fixture(tmp_path)
    with pytest.raises(RuntimeToolError) as stale:
        provider.prepare_call(
            RuntimeToolCall(
                "skill_install",
                {
                    "candidate_id": candidate["candidateId"],
                    "candidate_fingerprint": "f" * 64,
                },
                metadata(),
            )
        )
    assert stale.value.code == "skill_candidate_stale"

    with pytest.raises(RuntimeToolError) as rejected:
        provider.prepare_call(
            RuntimeToolCall(
                "skill_install",
                {
                    "candidate_id": candidate["candidateId"],
                    "candidate_fingerprint": candidate["candidateFingerprint"],
                },
                metadata(denied_skill_candidate_ids=[candidate["candidateId"]]),
            )
        )
    assert rejected.value.code == "skill_candidate_rejected"


@pytest.mark.asyncio
async def test_skill_install_approval_is_server_resolved_and_not_editable(
    tmp_path: Path,
) -> None:
    provider, _manager, candidate = provider_fixture(tmp_path)
    arguments = {
        "candidate_id": candidate["candidateId"],
        "candidate_fingerprint": candidate["candidateFingerprint"],
    }
    call = RuntimeToolCall("skill_install", arguments, metadata())
    provider.prepare_call(call)
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    middleware = build_human_in_the_loop_middleware(
        RuntimeMiddlewareSpec(
            middleware_id="human_in_the_loop",
            config={"interrupt_on_tools": "skill_install", "allow_edit": True},
            node_id="hitl-1",
        ),
        approvals,
    )
    pipeline = MiddlewarePipeline([middleware])
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        raise AssertionError("approval must interrupt before installation")

    with pytest.raises(RuntimeInterrupt) as interrupted:
        await pipeline.run_tool_call(
            ToolCallRequest(
                tool_name=call.tool_name,
                arguments=call.arguments,
                metadata=call.metadata,
            ),
            handler,
            context,
        )
    approval = approvals.require(interrupted.value.approval_id)
    assert approval.allowed_decisions == ["approve", "reject"]
    assert approval.metadata["skill_approval"]["candidate_id"] == candidate["candidateId"]
    assert "全局安装，仅授权当前 Agent 运行使用" in approval.description
