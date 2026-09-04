from __future__ import annotations

import importlib.util
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest


MODULE_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MODULE_ROOT / "scripts" / "validate_boundary.py"
SPEC = importlib.util.spec_from_file_location("validate_boundary_base", SCRIPT)
assert SPEC and SPEC.loader
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)


def _write_test_wheel(
    path: Path,
    *,
    name: str,
    version: str,
    license_headers: list[tuple[str, str]],
    symlink_member: bool = False,
) -> str:
    metadata = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    metadata.extend(f"{field}: {value}" for field, value in license_headers)
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata) + "\n")
        if symlink_member:
            member = ZipInfo("payload-link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "target")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _p2r_license_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    actual_first_headers: list[tuple[str, str]] | None = None,
    symlink_member: bool = False,
) -> tuple[dict, Path, list[Path]]:
    module_root = tmp_path / "ai-research"
    lock_path = module_root / boundary.P2R_CONNECTOR_LOCK
    wheel_root = tmp_path / "wheels"
    lock_path.parent.mkdir(parents=True)
    wheel_root.mkdir()
    records: list[dict[str, str]] = []
    wheels: list[Path] = []
    lock_lines = ["# synthetic P2R license gate fixture"]
    reviewed_packages = list(boundary.P2R_REVIEWED_LICENSE_METADATA.items())
    for index, (name, (expected_field, expected_value)) in enumerate(reviewed_packages):
        version = f"1.0.{index}"
        headers = (
            actual_first_headers
            if index == 0 and actual_first_headers is not None
            else [(expected_field, expected_value)]
        )
        wheel = wheel_root / f"locked-{index:02d}.whl"
        digest = _write_test_wheel(
            wheel,
            name=name,
            version=version,
            license_headers=headers,
            symlink_member=index == 0 and symlink_member,
        )
        wheels.append(wheel)
        lock_lines.extend([f"{name}=={version} \\", f"    --hash=sha256:{digest}"])
        records.append(
            {
                "name": name,
                "version": version,
                "sha256": digest,
                "field": expected_field,
                "rawValue": expected_value,
            }
        )
    lock_path.write_text("\n".join(lock_lines) + "\n", encoding="utf-8")
    for index in range(4):
        (wheel_root / f"unlocked-extra-{index}.whl").write_bytes(f"extra-{index}".encode())

    notice_lines = [
        boundary.P2R_LICENSE_NOTICE_HEADING,
        "",
        "| Component | Fixed version | METADATA field | Raw declared value |",
        "| --- | --- | --- | --- |",
        *[
            f"| {record['name']} | {record['version']} | `{record['field']}` | "
            f"`{record['rawValue']}` |"
            for record in records
        ],
        "",
        "## Next section",
    ]
    (module_root / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(notice_lines), encoding="utf-8"
    )
    source_lock = {
        "licenseAudit": {
            "p2rConnectorQualification": {
                "status": "passed_for_local_ephemeral_qualification_only",
                "platform": "linux/x86_64",
                "python": "3.12.13",
                "baseImage": boundary.P2R_CONNECTOR_BASE_IMAGE,
                "requirementsLockSha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                "packageCount": 17,
                "licenseMetadataUnknownCount": 0,
                "licenseEvidence": "locked wheel METADATA raw License or License-Expression",
                "licenseMetadata": records,
                "knownCopyleftOrMultiLicensePackages": (
                    boundary.P2R_KNOWN_COPYLEFT_OR_MULTI_LICENSES
                ),
                "wheelExposure": "exact-hash-isolated-temporary-view",
                "excludedFullTextExtras": ["PyMuPDF"],
                "distributionPolicy": boundary.P2R_CONNECTOR_DISTRIBUTION_POLICY,
                "redistributionCandidate": False,
            }
        }
    }
    monkeypatch.setattr(boundary, "MODULE_ROOT", module_root)
    return source_lock, wheel_root, wheels


def test_main_uses_requested_base_for_pr_scope_and_locked_base_for_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "ai-research"
    module_root.mkdir()
    locked_base = "1" * 40
    boundary_config = {"allowedParentFiles": ["server/main.py"]}
    source_lock = {"modelMirrorBaseCommit": locked_base}
    (module_root / "module-boundary.json").write_text(
        json.dumps(boundary_config), encoding="utf-8"
    )
    (module_root / "source-lock.json").write_text(json.dumps(source_lock), encoding="utf-8")

    calls: dict[str, object] = {}
    monkeypatch.setattr(boundary, "MODULE_ROOT", module_root)
    monkeypatch.setattr(
        boundary,
        "validate_requested_base",
        lambda requested, locked: calls.update(requested=(requested, locked)),
    )
    monkeypatch.setattr(
        boundary,
        "validate_paths",
        lambda base, config: calls.update(paths=(base, config)),
    )
    monkeypatch.setattr(
        boundary,
        "validate_locked_files",
        lambda lock, config: calls.update(locked_files=(lock, config)),
    )
    for name in (
        "validate_runtime_references",
        "validate_metric_names",
        "validate_parent_controls",
        "validate_ldr_distribution_mode",
        "validate_runtime_privacy_defaults",
        "validate_no_secrets",
    ):
        monkeypatch.setattr(boundary, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_boundary.py", "--base", "origin/main", "--distribution-mode", "external-pull"],
    )

    assert boundary.main() == 0
    assert calls["requested"] == ("origin/main", locked_base)
    assert calls["paths"] == ("origin/main", boundary_config)
    assert calls["locked_files"] == (source_lock, boundary_config)


def test_qualification_only_assets_must_be_source_locked() -> None:
    source_lock = {"lockedFiles": {}}
    boundary_config = {
        "qualificationOnlyAssets": ["worker/ai_research_worker/p2r_host.py"]
    }

    with pytest.raises(boundary.BoundaryFailure, match="absent from source-lock"):
        boundary.validate_locked_files(source_lock, boundary_config)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../server/model_router/ai_research_bridge.py",
        "/tmp/bridge.py",
        "C:/tmp/bridge.py",
        "worker//p2r_host.py",
        "worker/./p2r_host.py",
        "worker\\p2r_host.py",
    ],
)
def test_locked_and_qualification_asset_paths_cannot_escape_module(
    unsafe_path: str,
) -> None:
    with pytest.raises(boundary.BoundaryFailure, match="unsafe locked paths"):
        boundary.validate_locked_files(
            {"lockedFiles": {unsafe_path: {}}},
            {"qualificationOnlyAssets": []},
        )

    with pytest.raises(boundary.BoundaryFailure, match="unsafe paths"):
        boundary.validate_locked_files(
            {"lockedFiles": {"worker/p2r_host.py": {}}},
            {"qualificationOnlyAssets": [unsafe_path]},
        )


def test_p2r_license_gate_selects_17_hashes_and_reports_four_extras(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(monkeypatch, tmp_path)

    boundary.validate_p2r_connector_licenses(source_lock, wheel_root)

    output = capsys.readouterr().out
    assert "17 locked wheels" in output
    assert "ignored 4 unlocked wheels" in output
    assert "ignoredWheels" not in source_lock["licenseAudit"]["p2rConnectorQualification"]


@pytest.mark.parametrize(
    ("actual_headers", "expected_error"),
    [
        ([("License", "UNKNOWN")], "license metadata is unknown"),
        (
            [("License", "MIT"), ("License-Expression", "MIT")],
            "license metadata is missing or ambiguous",
        ),
    ],
)
def test_p2r_license_gate_rejects_unknown_or_ambiguous_wheel_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actual_headers: list[tuple[str, str]],
    expected_error: str,
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(
        monkeypatch,
        tmp_path,
        actual_first_headers=actual_headers,
    )

    with pytest.raises(boundary.P2RLicenseFailure, match=expected_error):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


def test_p2r_license_gate_rejects_missing_wheel_and_archive_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_lock, wheel_root, wheels = _p2r_license_fixture(monkeypatch, tmp_path)
    wheels[0].unlink()
    with pytest.raises(boundary.P2RLicenseFailure, match="missing locked wheels"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)

    source_lock, wheel_root, _ = _p2r_license_fixture(
        monkeypatch,
        tmp_path / "symlink-case",
        symlink_member=True,
    )
    with pytest.raises(boundary.P2RLicenseFailure, match="symlink entry"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


def test_p2r_license_gate_rejects_source_lock_and_notice_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(monkeypatch, tmp_path)
    notice_path = boundary.MODULE_ROOT / "THIRD_PARTY_NOTICES.md"
    notice_path.write_text(
        notice_path.read_text(encoding="utf-8").replace("`MPL-2.0`", "`Apache-2.0`", 1),
        encoding="utf-8",
    )
    with pytest.raises(boundary.P2RLicenseFailure, match="notice table drifted"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)

    source_lock, wheel_root, _ = _p2r_license_fixture(
        monkeypatch,
        tmp_path / "ignored-fact-case",
    )
    source_lock["licenseAudit"]["p2rConnectorQualification"]["ignoredWheels"] = []
    with pytest.raises(boundary.P2RLicenseFailure, match="unknown or missing facts"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


@pytest.mark.parametrize(
    "unreviewed_value",
    ["SEE LICENSE IN LICENSE.txt", "Custom", "Proprietary"],
)
def test_p2r_license_gate_rejects_unreviewed_source_lock_and_notice_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unreviewed_value: str,
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(monkeypatch, tmp_path)
    record = source_lock["licenseAudit"]["p2rConnectorQualification"]["licenseMetadata"][0]
    original_value = record["rawValue"]
    record["rawValue"] = unreviewed_value
    notice_path = boundary.MODULE_ROOT / "THIRD_PARTY_NOTICES.md"
    notice_path.write_text(
        notice_path.read_text(encoding="utf-8").replace(
            f"`{original_value}`", f"`{unreviewed_value}`", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(boundary.P2RLicenseFailure, match="reviewed license disposition drifted"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("wheelExposure", "all-cache-wheels"),
        ("distributionPolicy", {"publish": "allowed"}),
        ("redistributionCandidate", True),
    ],
)
def test_p2r_license_gate_rejects_qualification_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(monkeypatch, tmp_path)
    source_lock["licenseAudit"]["p2rConnectorQualification"][key] = value

    with pytest.raises(boundary.P2RLicenseFailure, match=f"fact drifted: {key}"):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


@pytest.mark.parametrize(
    ("linked_name", "expected_error"),
    [
        (
            "p2r-connectors-linux-x86_64.requirements.lock",
            "requirements lock is missing or is a symlink",
        ),
        ("THIRD_PARTY_NOTICES.md", "notice is missing or is a symlink"),
        ("wheels", "wheel root is missing, not a directory, or is a symlink"),
        ("locked-00.whl", "wheel root contains a symlink"),
    ],
)
def test_p2r_license_gate_rejects_link_like_inputs_cross_platform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    linked_name: str,
    expected_error: str,
) -> None:
    source_lock, wheel_root, _ = _p2r_license_fixture(monkeypatch, tmp_path)
    real_is_link_like = boundary._is_link_like
    monkeypatch.setattr(
        boundary,
        "_is_link_like",
        lambda path: path.name == linked_name or real_is_link_like(path),
    )

    with pytest.raises(boundary.P2RLicenseFailure, match=expected_error):
        boundary.validate_p2r_connector_licenses(source_lock, wheel_root)


def test_p2r_license_only_rejects_link_like_source_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "ai-research"
    module_root.mkdir()
    (module_root / "source-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(boundary, "MODULE_ROOT", module_root)
    monkeypatch.setattr(
        boundary,
        "_is_link_like",
        lambda path: path.name == "source-lock.json",
    )

    with pytest.raises(boundary.P2RLicenseFailure, match="source-lock is missing or is a symlink"):
        boundary._load_source_lock(p2r_license_only=True)


def test_p2r_launcher_runs_license_gate_before_docker_or_credentials() -> None:
    launcher = (MODULE_ROOT / "scripts" / "qualify_p2r_connectors.ps1").read_text(
        encoding="utf-8"
    )
    gate = launcher.index("$licenseGateOutput = @(& $pythonFile")

    assert gate < launcher.index("& docker @preflightArguments")
    assert gate < launcher.index('Read-Host "OpenReview username or email"')
    assert gate < launcher.index("$skillRootResolved =")
    assert "$copiedDigest" in launcher
    assert "ExpectedRequirementsLockSha256" in launcher
    assert "source=$isolatedLockPath,target=/lock/requirements.lock" in launcher
    assert 'must report exactly Python 3.12.13' in launcher


def test_scope_allows_only_current_pr_files(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"allowedParentFiles": ["server/main.py"]}
    monkeypatch.setattr(
        boundary,
        "changed_paths",
        lambda base: {"extensions/ai-research/README.md", "server/main.py"},
    )
    monkeypatch.setattr(boundary, "MODULE_ROOT", Path(__file__).parent / "missing-module-root")

    boundary.validate_paths("origin/main", config)


def test_scope_still_rejects_unapproved_current_pr_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boundary, "changed_paths", lambda base: {"server/unapproved.py"})
    monkeypatch.setattr(boundary, "MODULE_ROOT", Path(__file__).parent / "missing-module-root")

    with pytest.raises(boundary.BoundaryFailure, match="outside the approved boundary"):
        boundary.validate_paths("origin/main", {"allowedParentFiles": []})


@pytest.mark.parametrize("returncode", [1, 128])
def test_requested_base_must_descend_from_locked_provenance(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    locked_base = "1" * 40
    requested_sha = "2" * 40
    monkeypatch.setattr(boundary, "git", lambda *args: [requested_sha])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode),
    )

    with pytest.raises(boundary.BoundaryFailure, match="diverged from locked base"):
        boundary.validate_requested_base("origin/main", locked_base)


def test_advanced_requested_base_keeps_locked_provenance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked_base = "1" * 40
    requested_sha = "2" * 40
    monkeypatch.setattr(boundary, "git", lambda *args: [requested_sha])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    boundary.validate_requested_base("origin/main", locked_base)

    stderr = capsys.readouterr().err
    assert f"advanced to {requested_sha}" in stderr
    assert f"provenance remains pinned to {locked_base}" in stderr
