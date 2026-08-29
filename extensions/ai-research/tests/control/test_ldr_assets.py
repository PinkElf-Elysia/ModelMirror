from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "init_ldr_assets.py"
SPEC = importlib.util.spec_from_file_location("init_ldr_assets", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_fixed_revision_download_is_inventoried_and_reused(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def download(**kwargs: object) -> None:
        calls.append(kwargs)
        root = Path(str(kwargs["local_dir"]))
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "model.safetensors").write_bytes(b"fixed-model-bytes")

    root = tmp_path / "model"
    first = module.initialize(root, downloader=download)
    second = module.initialize(
        root,
        downloader=lambda **_: (_ for _ in ()).throw(
            AssertionError("verified assets must not redownload")
        ),
    )
    assert first == second
    assert calls == [
        {
            "repo_id": module.MODEL_REPOSITORY,
            "revision": module.MODEL_REVISION,
            "local_dir": str(root),
            "local_dir_use_symlinks": False,
        }
    ]


def test_single_byte_tamper_and_symlink_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "model"

    def download(**kwargs: object) -> None:
        Path(str(kwargs["local_dir"]), "model.safetensors").write_bytes(b"model")

    module.initialize(root, downloader=download)
    (root / "model.safetensors").chmod(0o644)
    (root / "model.safetensors").write_bytes(b"modeL")
    with pytest.raises(module.AssetIntegrityError, match="integrity"):
        module.verify(root)

    symlink_root = tmp_path / "symlink-model"
    symlink_root.mkdir()
    try:
        (symlink_root / "link").symlink_to(root / "model.safetensors")
    except OSError:
        pytest.skip("symlinks are unavailable on this Windows host")
    with pytest.raises(module.AssetIntegrityError, match="symlink"):
        module.inventory(symlink_root)


def test_models_link_replaces_only_an_empty_directory_and_is_idempotent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "model-assets"
    target.mkdir()
    link = tmp_path / "data" / "models"
    link.mkdir(parents=True)

    try:
        module.ensure_models_link(link, target)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows host")
    module.ensure_models_link(link, target)

    assert link.is_symlink()
    assert link.resolve() == target.resolve()

    link.unlink()
    link.mkdir()
    (link / "unexpected").write_text("preserve", encoding="utf-8")
    with pytest.raises(module.AssetIntegrityError, match="not an empty"):
        module.ensure_models_link(link, target)
