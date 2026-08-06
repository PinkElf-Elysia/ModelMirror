from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.skills.api import set_skill_manager_for_tests
from server.skills.skill_manager import SkillManager


def create_local_skill_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "skill-source"
    skill_dir = repo / "skills" / "pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: PDF Skill",
                "description: Extract and summarize PDF documents.",
                "---",
                "",
                "# PDF Skill",
                "",
                "Use this skill when the user needs PDF extraction or summarization.",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "README.txt").write_text("fixture", encoding="utf-8")

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
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        git_timeout_seconds=20,
    )
    set_skill_manager_for_tests(manager)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client
    set_skill_manager_for_tests(None)


@pytest.mark.asyncio
async def test_install_list_content_and_uninstall_skill(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    repo = create_local_skill_repo(tmp_path)

    install_response = await client.post(
        "/api/skills/install",
        json={"repo_url": str(repo), "sub_path": "skills/pdf"},
    )
    assert install_response.status_code == 200, install_response.text
    installed = install_response.json()
    assert installed["name"] == "PDF Skill"
    assert installed["description"] == "Extract and summarize PDF documents."
    assert installed["sub_path"] == "skills/pdf"

    skill_id = installed["skill_id"]
    installed_dir = tmp_path / "installed" / skill_id
    assert (installed_dir / "SKILL.md").exists()

    list_response = await client.get("/api/skills/installed")
    assert list_response.status_code == 200, list_response.text
    skills = list_response.json()["skills"]
    assert len(skills) == 1
    assert skills[0]["skill_id"] == skill_id

    content_response = await client.get(f"/api/skills/{skill_id}/content")
    assert content_response.status_code == 200, content_response.text
    assert "PDF extraction" in content_response.json()["content"]

    delete_response = await client.delete(f"/api/skills/{skill_id}")
    assert delete_response.status_code == 200, delete_response.text
    assert not installed_dir.exists()

    empty_response = await client.get("/api/skills/installed")
    assert empty_response.status_code == 200
    assert empty_response.json()["skills"] == []


@pytest.mark.asyncio
async def test_install_can_pin_a_verified_commit(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    repo = create_local_skill_repo(tmp_path)
    verified_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    skill_md = repo / "skills" / "pdf" / "SKILL.md"
    skill_md.write_text(
        skill_md.read_text(encoding="utf-8").replace(
            "Extract and summarize PDF documents.",
            "Unreviewed upstream description.",
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "change after audit"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    response = await client.post(
        "/api/skills/install",
        json={
            "repo_url": str(repo),
            "sub_path": "skills/pdf",
            "ref": verified_ref,
        },
    )

    assert response.status_code == 200, response.text
    installed = response.json()
    assert installed["source_ref"] == verified_ref
    assert installed["description"] == "Extract and summarize PDF documents."


def test_upgrade_failure_rolls_back_previous_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = create_local_skill_repo(tmp_path)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        git_timeout_seconds=20,
    )
    first_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    original = manager.install_skill(str(repo), "skills/pdf", first_ref)
    original_content = manager.get_skill_content(original.skill_id)

    skill_md = repo / "skills" / "pdf" / "SKILL.md"
    skill_md.write_text(
        original_content.replace("PDF documents", "changed PDF documents"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "upgrade"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    next_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def fail_metadata_write(_skills: dict[str, dict[str, object]]) -> None:
        raise OSError("simulated metadata failure")

    monkeypatch.setattr(manager, "_write_metadata", fail_metadata_write)
    with pytest.raises(OSError, match="simulated metadata failure"):
        manager.install_skill(str(repo), "skills/pdf", next_ref)

    assert manager.get_skill_content(original.skill_id) == original_content
    restored = manager.list_installed_skills()[0]
    assert restored.source_ref == first_ref
    assert not list((tmp_path / "installed").glob(".*.backup-*"))


def test_upgrade_replace_failure_restores_previous_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = create_local_skill_repo(tmp_path)
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=True,
        git_timeout_seconds=20,
    )
    first_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    original = manager.install_skill(str(repo), "skills/pdf", first_ref)
    original_content = manager.get_skill_content(original.skill_id)

    skill_md = repo / "skills" / "pdf" / "SKILL.md"
    skill_md.write_text(
        original_content.replace("PDF documents", "changed PDF documents"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "upgrade"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    next_ref = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    original_rename = Path.rename

    def fail_staging_replace(path: Path, target: Path) -> Path:
        if path.name.startswith(f".{original.skill_id}.staging-"):
            raise OSError("simulated replacement failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staging_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        manager.install_skill(str(repo), "skills/pdf", next_ref)

    assert manager.get_skill_content(original.skill_id) == original_content
    restored = manager.list_installed_skills()[0]
    assert restored.source_ref == first_ref
    assert not list((tmp_path / "installed").glob(".*.backup-*"))
    assert not list((tmp_path / "installed").glob(".*.staging-*"))


@pytest.mark.asyncio
async def test_install_rejects_non_commit_source_ref(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    repo = create_local_skill_repo(tmp_path)
    response = await client.post(
        "/api/skills/install",
        json={
            "repo_url": str(repo),
            "sub_path": "skills/pdf",
            "ref": "main",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_install_rejects_non_github_sources_by_default(
    tmp_path: Path,
) -> None:
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
        allow_local_repos=False,
    )
    set_skill_manager_for_tests(manager)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/skills/install",
            json={"repo_url": "https://example.com/not-allowed/repo", "sub_path": "pdf"},
        )

    set_skill_manager_for_tests(None)
    assert response.status_code == 400
    assert "github.com" in response.text


@pytest.mark.asyncio
async def test_unknown_skill_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/skills/not-installed/content")
    assert response.status_code == 404

