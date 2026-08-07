from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from server.coding_project_source.server import ProjectSnapshotBroker, ProjectSourceError
from server.coding_runtime.host_snapshot import (
    HostSnapshotError,
    create_host_snapshot_archive,
    extract_host_snapshot_archive,
    sha256_file,
)


PROJECT_ID = "hostgit_0123456789abcdef0123456789abcdef"


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, check=False, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    project = tmp_path / "source"
    project.mkdir()
    _git(project, "init", "-b", "feature/snapshot-r7m3")
    (project / "src").mkdir()
    (project / "src" / "marker.txt").write_bytes(b"random-q7m4\r\n")
    (project / "AGENTS.md").write_text("Reply briefly.\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=must-not-leak\n", encoding="utf-8")
    _git(project, "add", ".")
    _git(project, "-c", "user.name=Test", "-c", "user.email=test@modelmirror.local", "commit", "-m", "initial")
    return project


def _build(project: Path, archive: Path):
    return create_host_snapshot_archive(
        project,
        archive,
        project_id=PROJECT_ID,
        name="随机快照项目",
        branch="feature/snapshot-r7m3",
        head=_git(project, "rev-parse", "HEAD"),
    )


def test_head_blob_snapshot_preserves_crlf_hides_sensitive_files_and_round_trips(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    archive = tmp_path / "snapshot.tar.gz"
    built = _build(project, archive)
    workspace = tmp_path / "workspace"
    extracted = extract_host_snapshot_archive(
        archive, workspace, expected_project_id=PROJECT_ID, expected_name="随机快照项目",
        expected_branch=built.branch, expected_head=built.head,
    )

    assert (workspace / "src" / "marker.txt").read_bytes() == b"random-q7m4\r\n"
    assert not (workspace / ".env").exists()
    assert built.hidden_files == extracted.hidden_files == 1
    assert built.fingerprint == extracted.fingerprint
    assert built.file_count == extracted.file_count == 2


def test_broker_imports_once_and_removes_the_uploaded_archive(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    upload_id = "a" * 32
    archive = uploads / f"{upload_id}.tar.gz"
    projects_root = tmp_path / "unconfigured-projects"
    projects_root.mkdir()
    broker = ProjectSnapshotBroker(projects_root, tmp_path / "slot", upload_root=uploads)
    built = _build(project, archive)

    lease = broker.import_uploaded(
        upload_id, sha256_file(archive), project_id=PROJECT_ID, name=built.name,
        branch=built.branch, head=built.head,
    )
    assert lease.project_id == PROJECT_ID
    assert not archive.exists()
    assert (tmp_path / "slot" / "current" / "workspace" / "src" / "marker.txt").is_file()
    assert broker.health()["host_imports"] is True
    broker.release(PROJECT_ID, lease.lease_id)
    assert not (tmp_path / "slot" / "current").exists()


def test_broker_rejects_upload_digest_tampering_and_cleans_file(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    upload_id = "b" * 32
    archive = uploads / f"{upload_id}.tar.gz"
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    broker = ProjectSnapshotBroker(projects_root, tmp_path / "slot", upload_root=uploads)
    archive.write_bytes(b"tampered-r8v3")
    with pytest.raises(ProjectSourceError) as error:
        broker.import_uploaded(
            upload_id, "0" * 64, project_id=PROJECT_ID, name="随机项目",
            branch="main", head="a" * 40,
        )
    assert error.value.code == "snapshot_upload_digest_mismatch"
    assert not archive.exists()


def test_broker_refuses_to_clean_an_unmarked_or_misconfigured_upload_directory(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    important = uploads / "developer-notes.txt"
    important.write_text("must remain\n", encoding="utf-8")
    projects_root = tmp_path / "projects"
    projects_root.mkdir()

    with pytest.raises(ProjectSourceError) as error:
        ProjectSnapshotBroker(projects_root, tmp_path / "slot", upload_root=uploads)
    assert error.value.code == "snapshot_upload_root_unsafe"
    assert important.read_text(encoding="utf-8") == "must remain\n"


def test_extractor_rejects_traversal_symlink_and_manifest_digest_mismatch(tmp_path: Path) -> None:
    head = "a" * 40
    for index, member_factory in enumerate(
        (
            lambda: ("files/../outside.txt", b"escape", tarfile.REGTYPE),
            lambda: ("files/link", b"target", tarfile.SYMTYPE),
        )
    ):
        archive = tmp_path / f"unsafe-{index}.tar.gz"
        name, content, member_type = member_factory()
        manifest = {
            "version": 1, "project_id": PROJECT_ID, "name": "随机项目", "branch": "main",
            "head": head, "hidden_files": 0,
            "files": [{"path": name.removeprefix("files/"), "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}],
        }
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.type = member_type
            tar.addfile(info, io.BytesIO(content))
            raw_manifest = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw_manifest)
            tar.addfile(info, io.BytesIO(raw_manifest))
        with pytest.raises(HostSnapshotError):
            extract_host_snapshot_archive(
                archive, tmp_path / f"workspace-{index}", expected_project_id=PROJECT_ID,
                expected_name="随机项目", expected_branch="main", expected_head=head,
            )

    project = _repo(tmp_path)
    valid = tmp_path / "valid.tar.gz"
    built = _build(project, valid)
    with tarfile.open(valid, "r:gz") as tar:
        members = tar.getmembers()
        manifest_member = next(item for item in members if item.name == "manifest.json")
        manifest = json.loads(tar.extractfile(manifest_member).read())
    manifest["files"][0]["sha256"] = "0" * 64
    rewritten = tmp_path / "digest-mismatch.tar.gz"
    with tarfile.open(valid, "r:gz") as source, tarfile.open(rewritten, "w:gz") as target:
        for member in source.getmembers():
            content = source.extractfile(member).read()
            if member.name == "manifest.json":
                content = json.dumps(manifest).encode()
                member.size = len(content)
            target.addfile(member, io.BytesIO(content))
    with pytest.raises(HostSnapshotError) as mismatch:
        extract_host_snapshot_archive(
            rewritten, tmp_path / "digest-workspace", expected_project_id=PROJECT_ID,
            expected_name=built.name, expected_branch=built.branch, expected_head=built.head,
        )
    assert mismatch.value.code == "snapshot_digest_mismatch"
