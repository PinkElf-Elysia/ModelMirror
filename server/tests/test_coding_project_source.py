from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from server.coding_project_source.server import (
    CodingProjectSourceServer,
    ProjectSnapshotBroker,
    ProjectSourceError,
    SnapshotLimits,
)
from server.coding_runtime.patch_policy import snapshot_fingerprint
from server.coding_runtime.projects import load_project_manifest


def _git(path: Path, *arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ("git", *arguments),
        cwd=path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=text,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() if text else result.stdout


def _repository(root: Path, relative: str, marker: str) -> Path:
    project = root.joinpath(*relative.split("/"))
    project.mkdir(parents=True)
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text(f"marker: {marker}\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(
        project,
        "-c",
        "user.name=ModelMirror Test",
        "-c",
        "user.email=test@modelmirror.local",
        "commit",
        "-m",
        "initial",
    )
    return project


def _manifest(root: Path, projects: list[tuple[str, str]]) -> None:
    (root / ".modelmirror-coding-projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": [{"name": name, "path": path} for name, path in projects],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_snapshot_uses_head_blob_bytes_and_never_changes_source_repository(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    slot = tmp_path / "slot"
    root.mkdir()
    project = _repository(root, "alpha/source", "A-4nQ7")
    (project / ".gitattributes").write_text("crlf.txt text eol=lf\n", encoding="utf-8")
    (project / "crlf.txt").write_bytes(b"first\r\nsecond\r\n")
    _git(project, "add", ".gitattributes", "crlf.txt")
    _git(
        project,
        "-c",
        "user.name=ModelMirror Test",
        "-c",
        "user.email=test@modelmirror.local",
        "commit",
        "-m",
        "add crlf fixture",
    )
    assert b"\r\n" in (project / "crlf.txt").read_bytes()
    _manifest(root, [("Alpha", "alpha/source")])
    entry = load_project_manifest(root)[0]
    head = str(_git(project, "rev-parse", "HEAD"))
    expected = _git(project, "cat-file", "blob", "HEAD:crlf.txt", text=False)
    status_before = _git(project, "status", "--porcelain=v2", "--untracked-files=all")

    broker = ProjectSnapshotBroker(root, slot)
    lease = broker.acquire(entry.project_id, head)
    snapshot = slot / "current" / "workspace"

    assert (snapshot / "crlf.txt").read_bytes() == expected
    assert lease.fingerprint == snapshot_fingerprint(snapshot)
    assert _git(project, "status", "--porcelain=v2", "--untracked-files=all") == status_before == ""


def test_single_slot_never_contains_files_from_two_projects(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    slot = tmp_path / "slot"
    root.mkdir()
    alpha = _repository(root, "team/alpha", "ALPHA-r8W2")
    beta = _repository(root, "team/beta", "BETA-k3P9")
    _manifest(root, [("Alpha", "team/alpha"), ("Beta", "team/beta")])
    alpha_entry, beta_entry = load_project_manifest(root)
    broker = ProjectSnapshotBroker(root, slot)

    alpha_lease = broker.acquire(alpha_entry.project_id, str(_git(alpha, "rev-parse", "HEAD")))
    assert b"ALPHA-r8W2" in (slot / "current" / "workspace" / "README.md").read_bytes()
    with pytest.raises(ProjectSourceError) as busy:
        broker.acquire(beta_entry.project_id, str(_git(beta, "rev-parse", "HEAD")))
    assert busy.value.code == "snapshot_busy"

    assert broker.release(alpha_entry.project_id, alpha_lease.lease_id) is True
    beta_lease = broker.acquire(beta_entry.project_id, str(_git(beta, "rev-parse", "HEAD")))
    snapshot_content = (slot / "current" / "workspace" / "README.md").read_bytes()
    assert b"BETA-k3P9" in snapshot_content
    assert b"ALPHA-r8W2" not in snapshot_content
    assert beta_lease.project_id == beta_entry.project_id


def test_snapshot_hides_sensitive_and_executable_configuration_files(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    slot = tmp_path / "slot"
    root.mkdir()
    project = _repository(root, "team/safe", "SAFE-w6D1")
    fixtures = {
        "AGENTS.md": "Always explain changes in plain language.\n",
        "nested/AGENTS.md": "Do not expose me.\n",
        ".env": "RANDOM_SECRET=hidden-2xH8\n",
        "opencode.json": '{"permission":"allow"}\n',
        ".mcp.json": '{"servers":{}}\n',
        "docs/guide.md": "visible guide\n",
    }
    for path, content in fixtures.items():
        target = project.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(project, "add", ".")
    _git(
        project,
        "-c",
        "user.name=ModelMirror Test",
        "-c",
        "user.email=test@modelmirror.local",
        "commit",
        "-m",
        "add safety fixtures",
    )
    _manifest(root, [("Safe", "team/safe")])
    entry = load_project_manifest(root)[0]
    broker = ProjectSnapshotBroker(root, slot)

    lease = broker.acquire(entry.project_id, str(_git(project, "rev-parse", "HEAD")))
    snapshot = slot / "current" / "workspace"

    assert (snapshot / "AGENTS.md").is_file()
    assert (snapshot / "docs" / "guide.md").is_file()
    assert not (snapshot / "nested" / "AGENTS.md").exists()
    assert not (snapshot / ".env").exists()
    assert not (snapshot / "opencode.json").exists()
    assert not (snapshot / ".mcp.json").exists()
    assert lease.hidden_files == 4


def test_limit_failure_cleans_the_snapshot_slot(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    slot = tmp_path / "slot"
    root.mkdir()
    project = _repository(root, "team/large", "LARGE-j5T0")
    (project / "extra.txt").write_text("second file\n", encoding="utf-8")
    _git(project, "add", "extra.txt")
    _git(
        project,
        "-c",
        "user.name=ModelMirror Test",
        "-c",
        "user.email=test@modelmirror.local",
        "commit",
        "-m",
        "add limit fixture",
    )
    _manifest(root, [("Large", "team/large")])
    entry = load_project_manifest(root)[0]
    broker = ProjectSnapshotBroker(
        root,
        slot,
        limits=SnapshotLimits(max_files=1, max_bytes=64, max_file_bytes=64, max_agents_bytes=32),
    )

    with pytest.raises(ProjectSourceError) as error:
        broker.acquire(entry.project_id, str(_git(project, "rev-parse", "HEAD")))

    assert error.value.code == "snapshot_file_limit_exceeded"
    assert list(slot.iterdir()) == []
    assert _git(project, "status", "--porcelain=v2", "--untracked-files=all") == ""


@pytest.mark.asyncio
async def test_socket_protocol_accepts_only_fixed_project_actions(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    slot = tmp_path / "slot"
    root.mkdir()
    project = _repository(root, "team/protocol", "PROTOCOL-c9V4")
    _manifest(root, [("Protocol", "team/protocol")])
    entry = load_project_manifest(root)[0]
    server = CodingProjectSourceServer(broker=ProjectSnapshotBroker(root, slot))

    listed = await server._dispatch({"action": "list"})
    assert listed["projects"][0]["id"] == entry.project_id
    acquired = await server._dispatch(
        {
            "action": "acquire",
            "project_id": entry.project_id,
            "expected_head": str(_git(project, "rev-parse", "HEAD")),
        }
    )
    assert acquired["lease"]["project_id"] == entry.project_id
    with pytest.raises(ProjectSourceError) as shell:
        await server._dispatch({"action": "shell", "command": "type C:\\secret.txt"})
    assert shell.value.code == "unsupported_action"
    with pytest.raises(ProjectSourceError) as injected_path:
        await server._dispatch(
            {"action": "acquire", "project_id": entry.project_id, "expected_head": "a" * 40, "path": "../other"}
        )
    assert injected_path.value.code == "invalid_request"


def test_project_source_compose_isolates_root_socket_and_snapshot() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.coding-projects.yml").read_text(encoding="utf-8"))
    dockerfile = (root / "server/coding_project_source/Dockerfile").read_text(encoding="utf-8")
    broker = compose["services"]["coding-project-source"]
    server = compose["services"]["server"]
    runtime = compose["services"]["coding-runtime"]

    assert broker["profiles"] == ["coding-projects"]
    assert broker["network_mode"] == "none"
    assert broker["user"] == "65532:65532"
    assert broker["read_only"] is True
    assert broker["cap_drop"] == ["ALL"]
    assert broker["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in broker
    assert "/var/run/docker.sock" not in repr(broker)
    assert "CODING_AGENT_GATEWAY_KEY" not in repr(broker)
    root_mount = broker["volumes"][2]
    assert root_mount["target"] == "/projects-root"
    assert root_mount["read_only"] is True
    assert root_mount["bind"]["create_host_path"] is False
    assert "/projects-root" not in repr(server)
    assert "/projects-root" not in repr(runtime)
    assert runtime["volumes"][0]["target"] == "/project-snapshots"
    assert runtime["volumes"][0]["read_only"] is True
    snapshot_volume = compose["volumes"]["coding_project_snapshot"]
    assert snapshot_volume["driver"] == "local"
    assert snapshot_volume["driver_opts"] == {
        "type": "tmpfs",
        "device": "tmpfs",
        "o": "size=256m,uid=65532,gid=65532,mode=0700",
    }
    assert "apt-get install --yes --no-install-recommends git" in dockerfile
    assert "COPY server/coding_runtime/commands.py" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_project_source.server"]' in dockerfile
