from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from server.coding_worker.parity import load_public_fixture_bundle
from server.coding_worker.parity_runner import ParityCheckRequest
from server.coding_worker.parity_sidecar import (
    ParitySidecarError,
    _approval_allowed,
    _canonical_sha256,
    _checker,
    _export_workspace,
    _opencode_configuration,
    _safe_extract_tar,
)


FIXTURES = Path(__file__).parent / "fixtures"
ASSETS = FIXTURES / "coding_worker_v17_parity_assets.json"


def _tar(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in sorted(entries.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _checker_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ParityCheckRequest, Path]:
    export_root = tmp_path / "exports"
    export_root.mkdir(parents=True)
    monkeypatch.setenv("CODING_PARITY_EXPORT_ROOT", str(export_root))
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "answer.py").write_text("ANSWER = 42\n", encoding="utf-8")
    artifact, final_tree, _manifest = _export_workspace(
        run_id="run_checker_test", repository=repository
    )
    spec = {
        "check_argv": [
            "python",
            "-c",
            "from answer import ANSWER; assert ANSWER == 42",
        ],
        "diff_argv": ["python", "-c", "raise SystemExit(0)"],
        "cwd": ".",
        "timeout_seconds": 30,
    }
    check_id = "checks_unit"
    bundle = _tar(
        {
            "checks.json": json.dumps(
                {"schema_version": 1, "checks": {check_id: spec}},
                sort_keys=True,
            ).encode("utf-8")
        }
    )
    bundle_path = tmp_path / "checker.bundle"
    bundle_path.write_bytes(bundle)
    monkeypatch.setenv("CODING_PARITY_CHECKER_BUNDLE", str(bundle_path))
    request = ParityCheckRequest(
        run_id="run_checker_test",
        task_id="task_checker_test",
        attempt=1,
        hidden_check_bundle_id=check_id,
        hidden_check_sha256=_canonical_sha256(spec),
        hidden_checker_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        initial_tree_hash="1" * 64,
        final_tree_hash=final_tree,
        workspace_export=artifact,
    )
    return request, bundle_path


def test_checker_alone_reads_sealed_bundle_and_bound_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _bundle = _checker_request(tmp_path, monkeypatch)

    receipt = _checker(request)

    assert receipt.hidden_checks_passed is True
    assert receipt.allowed_diff is True
    assert receipt.workspace_export_sha256 == request.workspace_export.sha256


def test_checker_rejects_tampered_bundle_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, bundle = _checker_request(tmp_path, monkeypatch)
    bundle.write_bytes(bundle.read_bytes() + b"tampered")
    with pytest.raises(ParitySidecarError, match="bundle binding"):
        _checker(request)

    request, _bundle = _checker_request(tmp_path / "second", monkeypatch)
    export = (
        Path(os.environ["CODING_PARITY_EXPORT_ROOT"])
        / f"{request.workspace_export.artifact_id}.tar"
    )
    export.write_bytes(export.read_bytes() + b"tampered")
    with pytest.raises(ParitySidecarError, match="export binding"):
        _checker(request)


def test_archive_extraction_rejects_traversal(tmp_path: Path) -> None:
    content = _tar({"../outside.py": b"print('unsafe')\n"})
    destination = tmp_path / "extract"
    destination.mkdir()
    with pytest.raises(ParitySidecarError, match="entry is unsafe"):
        _safe_extract_tar(content, destination)
    assert not (tmp_path / "outside.py").exists()


def test_worker_approval_matches_only_frozen_visible_command() -> None:
    fixture = load_public_fixture_bundle(ASSETS).fixtures[0]
    allowed = list(fixture.visible_checks[0].argv)
    assert _approval_allowed({"request": {"argv": allowed}}, fixture) is True
    assert (
        _approval_allowed(
            {"request": {"argv": ["python", "-m", "pytest", "-q", "--lf"]}},
            fixture,
        )
        is False
    )


def test_native_configuration_denies_unfrozen_tools_and_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_PARITY_MODEL_ID", "test-model")
    monkeypatch.setenv("CODING_PARITY_MODEL_BASE_URL", "http://new-api:3000/v1")
    fixture = load_public_fixture_bundle(ASSETS).fixtures[0]

    config = _opencode_configuration(fixture)

    permission = config["permission"]
    assert permission["bash"]["*"] == "deny"
    assert permission["webfetch"] == permission["websearch"] == "deny"
    assert permission["skill"] == "deny"
    assert config["plugin"] == [] and config["mcp"] == {}


def test_compose_profile_keeps_checker_and_controller_off_network() -> None:
    compose = (Path(__file__).parents[2] / "docker-compose.coding-worker-v17-parity.yml").read_text(
        encoding="utf-8"
    )
    assert "coding-worker-parity-checker:" in compose
    assert "coding-worker-parity-controller:" in compose
    assert compose.count("network_mode: none") == 2
    assert "docker.sock" not in compose
    controller = compose.split("coding-worker-parity-controller:", 1)[1]
    assert "checker.bundle" not in controller
    assert "CODING_PARITY_ROUTE_KEY" not in controller
