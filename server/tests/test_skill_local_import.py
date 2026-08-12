from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest
import skills.local_import as local_import

from skills.local_import import (
    SkillLocalImportConflictError,
    SkillLocalImportError,
    SkillLocalImportStorageError,
    SkillLocalImportStore,
    normalize_folder_upload,
    normalize_zip_upload,
)
from skills.trust_scanner import SkillTrustTreeEntry, scan_local_skill_trust_receipt, sha256_json


def _skill_markdown(
    name: str = "local-example",
    body: str = "## Workflow\n\n1. Read the input.\n2. Return the result.\n",
) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use this local Skill for a bounded deterministic task.\n"
        "---\n\n"
        f"{body}"
    ).encode()


def _zip(files: dict[str, bytes], *, modes: dict[str, int] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, content in files.items():
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            if modes and path in modes:
                info.create_system = 3
                info.external_attr = modes[path] << 16
            bundle.writestr(info, content)
    return output.getvalue()


def _entry(path: str, content: bytes) -> SkillTrustTreeEntry:
    return SkillTrustTreeEntry(
        path=path,
        mode="100644",
        object_type="blob",
        object_id=hashlib.sha1(content).hexdigest(),
        size=len(content),
        content=content,
    )


def test_local_receipt_uses_local_source_without_changing_git_contract() -> None:
    receipt = scan_local_skill_trust_receipt(
        import_id="skillimport_" + "a" * 32,
        import_revision=1,
        transport_kind="folder",
        transport_digest="b" * 64,
        entries=[_entry("SKILL.md", _skill_markdown())],
    )

    assert receipt["source"] == {
        "kind": "local_import",
        "importId": "skillimport_" + "a" * 32,
        "importRevision": 1,
        "transportKind": "folder",
        "transportDigest": "b" * 64,
    }
    assert "directoryTreeSha" not in receipt
    assert len(receipt["contentTreeDigest"]) == 64
    assert receipt["receiptId"].startswith("trust_local_")
    assert sha256_json(
        {key: value for key, value in receipt.items() if key != "trustFingerprint"}
    ) == receipt["trustFingerprint"]
    assert receipt["installPolicy"] == "allow"


@pytest.mark.parametrize(
    ("path", "content", "code"),
    [
        ("scripts/tool.zip", b"PK\x03\x04payload", "trust_archive_blocked"),
        ("assets/tool.exe", b"MZpayload", "trust_executable_binary_blocked"),
        ("assets/blob.dat", b"\x00\xffpayload", "trust_unknown_binary_blocked"),
    ],
)
def test_local_uninspectable_payloads_are_hard_blocks(
    path: str, content: bytes, code: str
) -> None:
    receipt = scan_local_skill_trust_receipt(
        import_id="skillimport_" + "a" * 32,
        import_revision=1,
        transport_kind="folder",
        transport_digest="b" * 64,
        entries=[_entry("SKILL.md", _skill_markdown()), _entry(path, content)],
    )

    assert code in {finding["code"] for finding in receipt["findings"]}
    assert receipt["trustStatus"] == "blocked"
    assert receipt["installPolicy"] == "block"
    assert receipt["routerEligible"] is False


def test_local_script_policy_allows_valid_code_but_excludes_invalid_code_from_router() -> None:
    markdown = _skill_markdown(
        body="## Workflow\n\n1. Run `python scripts/tool.py`.\n2. Return its output.\n"
    )
    valid = scan_local_skill_trust_receipt(
        import_id="skillimport_" + "a" * 32,
        import_revision=1,
        transport_kind="folder",
        transport_digest="b" * 64,
        entries=[_entry("SKILL.md", markdown), _entry("scripts/tool.py", b"print('ok')\n")],
    )
    invalid = scan_local_skill_trust_receipt(
        import_id="skillimport_" + "b" * 32,
        import_revision=1,
        transport_kind="folder",
        transport_digest="c" * 64,
        entries=[_entry("SKILL.md", markdown), _entry("scripts/tool.py", b"value =\n")],
    )

    assert valid["installPolicy"] == "confirm"
    assert valid["routerEligible"] is True
    assert invalid["installPolicy"] == "confirm"
    assert invalid["routerEligible"] is False


def test_folder_normalization_is_order_independent_and_strips_one_wrapper() -> None:
    first, first_digest, ignored = normalize_folder_upload(
        [
            ("package/references/guide.md", b"guide"),
            ("package/.DS_Store", b"noise"),
            ("package/SKILL.md", _skill_markdown()),
        ]
    )
    second, second_digest, _ = normalize_folder_upload(
        [
            ("package/SKILL.md", _skill_markdown()),
            ("package/.DS_Store", b"noise"),
            ("package/references/guide.md", b"guide"),
        ]
    )

    assert [item.path for item in first] == ["SKILL.md", "references/guide.md"]
    assert [item.path for item in second] == ["SKILL.md", "references/guide.md"]
    assert first_digest == second_digest
    assert ignored == [{"reason": "macos_metadata", "count": 1}]


@pytest.mark.parametrize(
    "path",
    [
        "../SKILL.md",
        "/absolute/SKILL.md",
        "C:/Users/example/SKILL.md",
        "package/.git/config",
        "package/CON/file.md",
        "package/references/item. ",
    ],
)
def test_folder_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(SkillLocalImportError) as error:
        normalize_folder_upload([(path, _skill_markdown())])
    assert error.value.code == "skill_import_path_unsafe"


def test_folder_rejects_case_collisions_and_multiple_roots() -> None:
    with pytest.raises(SkillLocalImportError, match="collide"):
        normalize_folder_upload(
            [
                ("SKILL.md", _skill_markdown()),
                ("References/Guide.md", b"one"),
                ("references/guide.md", b"two"),
            ]
        )
    with pytest.raises(SkillLocalImportError) as error:
        normalize_folder_upload(
            [("one/SKILL.md", _skill_markdown()), ("two/SKILL.md", _skill_markdown())]
        )
    assert error.value.code == "skill_import_multiple_roots"

    with pytest.raises(SkillLocalImportError) as nested:
        normalize_folder_upload(
            [
                ("SKILL.md", _skill_markdown()),
                ("nested/SKILL.md", _skill_markdown(name="nested")),
            ]
        )
    assert nested.value.code == "skill_import_multiple_roots"


def test_zip_preserves_passive_binary_and_executable_mode() -> None:
    archive = _zip(
        {
            "wrapper/SKILL.md": _skill_markdown(
                body="## Workflow\n\n1. Run `python scripts/tool.py`.\n2. Use `assets/pixel.png`.\n"
            ),
            "wrapper/scripts/tool.py": b"print('ok')\n",
            "wrapper/assets/pixel.png": b"\x89PNG\r\n\x1a\nrest",
        },
        modes={"wrapper/scripts/tool.py": stat.S_IFREG | 0o755},
    )

    files, digest, ignored = normalize_zip_upload(archive)

    assert len(digest) == 64
    assert ignored == []
    assert {item.path for item in files} == {
        "SKILL.md",
        "scripts/tool.py",
        "assets/pixel.png",
    }
    assert next(item for item in files if item.path == "scripts/tool.py").mode == "100755"


def test_zip_rejects_symlink_and_bad_compression() -> None:
    symlink = _zip(
        {"SKILL.md": _skill_markdown(), "references/link": b"target"},
        modes={"references/link": stat.S_IFLNK | 0o777},
    )
    with pytest.raises(SkillLocalImportError) as error:
        normalize_zip_upload(symlink)
    assert error.value.code == "skill_import_path_unsafe"

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_BZIP2) as bundle:
        bundle.writestr("SKILL.md", _skill_markdown())
    with pytest.raises(SkillLocalImportError) as error:
        normalize_zip_upload(output.getvalue())
    assert error.value.code == "skill_import_archive_invalid"


def test_zip_rejects_encrypted_entry_before_reading_content() -> None:
    archive = bytearray(_zip({"SKILL.md": _skill_markdown()}))
    central = archive.find(b"PK\x01\x02")
    assert central >= 0
    flags = int.from_bytes(archive[central + 8 : central + 10], "little") | 0x1
    archive[central + 8 : central + 10] = flags.to_bytes(2, "little")

    with pytest.raises(SkillLocalImportError) as exc_info:
        normalize_zip_upload(bytes(archive))
    assert exc_info.value.code == "skill_import_archive_encrypted"


def test_store_publishes_immutable_package_and_deduplicates(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    items = [
        ("local-example/SKILL.md", _skill_markdown()),
        ("local-example/references/guide.md", b"# Guide\n"),
    ]

    created = store.create_from_folder(items)
    duplicate = store.create_from_folder(list(reversed(items)))

    assert created.state == "ready"
    assert created.local_skill_id == "local-example"
    assert duplicate.import_id == created.import_id
    assert duplicate.package_digest == created.package_digest
    assert store.package_directory(created.import_id).is_dir()
    assert store.preview_file(created.import_id, "references/guide.md") == "# Guide\n"
    assert len(store.list_imports()) == 1

    reloaded = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    assert reloaded.require(created.import_id).serialize() == created.serialize()


def test_store_keeps_passive_resources_but_purges_blocked_bytes(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    passive = store.create_from_folder(
        [
            ("SKILL.md", _skill_markdown()),
            ("assets/pixel.png", b"\x89PNG\r\n\x1a\nrest"),
        ]
    )
    assert passive.state == "confirmation_required"
    assert store.package_directory(passive.import_id).joinpath("assets/pixel.png").read_bytes().startswith(b"\x89PNG")
    with pytest.raises(SkillLocalImportError):
        store.preview_file(passive.import_id, "assets/pixel.png")


def test_blocked_import_retains_only_redacted_metadata(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    secret = "sk-" + "exampletoken12345678901234567890"
    record = store.create_from_folder(
        [("SKILL.md", _skill_markdown(body=f"## Workflow\n\n1. TOKEN={secret}\n"))]
    )

    assert record.state == "blocked"
    assert record.file_manifest == []
    assert record.local_skill_id is None
    assert record.package_digest
    assert not (store.packages_root / record.package_digest).exists()
    assert secret not in json.dumps(record.serialize(), ensure_ascii=False)


def test_hidden_env_secret_is_scanned_and_never_retained(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    secret = "sk-" + "hidden-local-secret-value-1234567890"
    record = store.create_from_folder(
        [("SKILL.md", _skill_markdown()), (".env", f"API_KEY={secret}\n".encode())]
    )

    assert record.state == "blocked"
    assert record.file_manifest == []
    assert record.package_digest
    assert not (store.packages_root / record.package_digest).exists()
    assert secret not in json.dumps(record.serialize(), ensure_ascii=False)


def test_store_rescan_uses_optimistic_receipt_and_delete_cleans_bytes(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder([("SKILL.md", _skill_markdown())])

    with pytest.raises(SkillLocalImportConflictError):
        store.rescan(
            record.import_id,
            expected_revision=record.revision + 1,
            expected_package_digest=record.package_digest or "",
            expected_trust_fingerprint=record.trust_fingerprint or "",
        )
    rescanned = store.rescan(
        record.import_id,
        expected_revision=record.revision,
        expected_package_digest=record.package_digest or "",
        expected_trust_fingerprint=record.trust_fingerprint or "",
    )
    assert rescanned.revision == record.revision + 1
    assert rescanned.package_digest == record.package_digest
    assert rescanned.trust_fingerprint != record.trust_fingerprint

    package_dir = store.package_directory(record.import_id)
    store.delete(
        record.import_id,
        expected_revision=rescanned.revision,
        expected_package_digest=rescanned.package_digest,
        expected_trust_fingerprint=rescanned.trust_fingerprint,
    )
    assert not package_dir.exists()
    assert store.list_imports() == []


def test_top_level_corruption_fails_closed_without_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    root.mkdir()
    index = root / "imports.json"
    original = b"{not-json"
    index.write_bytes(original)
    store = SkillLocalImportStore(root, enabled=True)

    assert store.status()["available"] is False
    with pytest.raises(SkillLocalImportStorageError):
        store.create_from_folder([("SKILL.md", _skill_markdown())])
    assert index.read_bytes() == original


def test_interrupted_scan_recovers_as_failed_without_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "imports"
    store = SkillLocalImportStore(root, enabled=True)

    def interrupt(_items: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(local_import, "normalize_folder_upload", interrupt)
    with pytest.raises(KeyboardInterrupt):
        store.create_from_folder([("SKILL.md", _skill_markdown())])

    recovered = SkillLocalImportStore(root, enabled=True).list_imports()
    assert len(recovered) == 1
    assert recovered[0].state == "failed"
    assert recovered[0].revision == 2
    assert recovered[0].error_code == "skill_import_scan_failed"
    assert recovered[0].package_digest is None
    assert not (root / "packages").exists()


def test_startup_removes_unreferenced_published_package(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    store = SkillLocalImportStore(root, enabled=True)
    orphan = store.packages_root / ("a" * 64)
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_bytes(_skill_markdown())

    reloaded = SkillLocalImportStore(root, enabled=True)

    assert reloaded.status()["available"] is True
    assert not orphan.exists()


def test_preview_and_package_directory_fail_closed_after_disk_tamper(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder([("SKILL.md", _skill_markdown())])
    package_root = store.packages_root / str(record.package_digest)
    (package_root / "SKILL.md").write_bytes(_skill_markdown(name="tampered"))

    for operation in (
        lambda: store.package_directory(record.import_id),
        lambda: store.preview_file(record.import_id, "SKILL.md"),
    ):
        with pytest.raises(SkillLocalImportConflictError) as exc_info:
            operation()
        assert exc_info.value.code == "skill_import_package_mismatch"


def test_active_browser_content_is_not_returned_by_preview(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder(
        [
            ("SKILL.md", _skill_markdown(body="Use `assets/template.html`.")),
            (
                "assets/template.html",
                b"<html><body><script>window.example = true</script></body></html>",
            ),
        ]
    )

    assert record.state == "confirmation_required"
    with pytest.raises(SkillLocalImportError) as exc_info:
        store.preview_file(record.import_id, "assets/template.html")
    assert exc_info.value.code == "skill_import_invalid_transport"


def test_stored_package_link_is_never_followed(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)
    record = store.create_from_folder([("SKILL.md", _skill_markdown())])
    package_root = store.packages_root / str(record.package_digest)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_bytes(_skill_markdown())
    shutil.rmtree(package_root)
    try:
        package_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable in this environment.")

    with pytest.raises(SkillLocalImportStorageError) as exc_info:
        store.preview_file(record.import_id, "SKILL.md")
    assert exc_info.value.code == "skill_import_storage_unavailable"


def test_single_bad_record_is_quarantined_without_losing_valid_record(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    store = SkillLocalImportStore(root, enabled=True)
    valid = store.create_from_folder([("SKILL.md", _skill_markdown())])
    payload = json.loads((root / "imports.json").read_text(encoding="utf-8"))
    payload["imports"].append({"importId": "bad", "source": "secret payload omitted"})
    (root / "imports.json").write_text(json.dumps(payload), encoding="utf-8")

    reloaded = SkillLocalImportStore(root, enabled=True)

    assert reloaded.status()["available"] is True
    assert [item.import_id for item in reloaded.list_imports()] == [valid.import_id]
    assert reloaded._quarantine[0]["sizeBytes"] > 0
    assert "source" not in reloaded._quarantine[0]

    rescanned = reloaded.rescan(
        valid.import_id,
        expected_revision=valid.revision,
        expected_package_digest=valid.package_digest or "",
        expected_trust_fingerprint=valid.trust_fingerprint or "",
    )
    restarted = SkillLocalImportStore(root, enabled=True)
    assert restarted.require(valid.import_id).revision == rescanned.revision
    assert restarted._quarantine == reloaded._quarantine


def test_tampered_trust_receipt_is_quarantined_on_load(tmp_path: Path) -> None:
    root = tmp_path / "imports"
    store = SkillLocalImportStore(root, enabled=True)
    store.create_from_folder([("SKILL.md", _skill_markdown())])
    payload = json.loads((root / "imports.json").read_text(encoding="utf-8"))
    payload["imports"][0]["trustReceipt"]["riskLevel"] = "high"
    (root / "imports.json").write_text(json.dumps(payload), encoding="utf-8")

    reloaded = SkillLocalImportStore(root, enabled=True)

    assert reloaded.status()["available"] is True
    assert reloaded.list_imports() == []
    assert len(reloaded._quarantine) == 1
    assert (store.packages_root / str(payload["imports"][0]["packageDigest"])).is_dir()


def test_metadata_failure_rolls_back_new_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=True)

    def fail_save(_records: object) -> None:
        raise SkillLocalImportStorageError(
            "disk full", code="skill_import_storage_unavailable"
        )

    monkeypatch.setattr(store, "_save_records_unlocked", fail_save)
    with pytest.raises(SkillLocalImportStorageError):
        store.create_from_folder([("SKILL.md", _skill_markdown())])
    assert not store.packages_root.exists() or not any(store.packages_root.iterdir())


def test_disabled_store_keeps_status_readable_but_blocks_mutation(tmp_path: Path) -> None:
    store = SkillLocalImportStore(tmp_path / "imports", enabled=False)
    assert store.status()["enabled"] is False
    with pytest.raises(SkillLocalImportError) as error:
        store.create_from_zip(_zip({"SKILL.md": _skill_markdown()}))
    assert error.value.code == "skill_import_disabled"
