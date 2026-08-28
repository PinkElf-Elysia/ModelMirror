from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable


MODEL_REPOSITORY = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MANIFEST_NAME = "modelmirror-asset-manifest.json"
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class AssetIntegrityError(RuntimeError):
    pass


def initialize(
    root: Path,
    *,
    downloader: Callable[..., object],
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise AssetIntegrityError("model asset root must not be a symlink")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists():
        return verify(root)
    downloader(
        repo_id=MODEL_REPOSITORY,
        revision=MODEL_REVISION,
        local_dir=str(root),
        local_dir_use_symlinks=False,
    )
    files = inventory(root)
    manifest = {
        "schemaVersion": 1,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "files": files,
    }
    atomic_write(manifest_path, canonical_json(manifest))
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        directory.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    root.chmod(0o755)
    return verify(root)


def verify(root: Path) -> dict[str, object]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise AssetIntegrityError("model asset manifest is unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AssetIntegrityError("model asset manifest is malformed") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != 1
        or manifest.get("repository") != MODEL_REPOSITORY
        or manifest.get("revision") != MODEL_REVISION
        or not isinstance(manifest.get("files"), dict)
    ):
        raise AssetIntegrityError("model asset manifest does not match the source lock")
    if inventory(root) != manifest["files"]:
        raise AssetIntegrityError("model asset integrity verification failed")
    return manifest


def inventory(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.name == MANIFEST_NAME:
            continue
        if path.is_symlink():
            raise AssetIntegrityError("model assets must not contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total += size
        if len(result) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise AssetIntegrityError("model assets exceed the fixed inventory limit")
        result[relative] = {"sizeBytes": size, "sha256": digest(path)}
    if not result:
        raise AssetIntegrityError("model asset snapshot is empty")
    return result


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def ensure_models_link(link: Path, target: Path) -> None:
    if not link.is_absolute() or not target.is_absolute():
        raise AssetIntegrityError("model link and target must be absolute")
    if not target.is_dir() or target.is_symlink():
        raise AssetIntegrityError("model link target must be a real directory")
    target_resolved = target.resolve()
    if link.is_symlink():
        if link.resolve() != target_resolved:
            raise AssetIntegrityError("model link points to an unexpected target")
        return
    if link.exists():
        if not link.is_dir() or any(link.iterdir()):
            raise AssetIntegrityError("model link path is not an empty directory")
        link.rmdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    if not link.is_symlink() or link.resolve() != target_resolved:
        raise AssetIntegrityError("model link integrity verification failed")


def main() -> None:
    from huggingface_hub import snapshot_download

    root = Path(
        os.environ.get(
            "AI_RESEARCH_LDR_MODEL_ROOT",
            "/model-assets/sentence-transformers/all-MiniLM-L6-v2",
        )
    )
    manifest = initialize(root, downloader=snapshot_download)
    ensure_models_link(
        Path(os.environ.get("AI_RESEARCH_LDR_MODEL_LINK", "/data/models")),
        root.parents[1],
    )
    print(
        json.dumps(
            {
                "status": "verified",
                "repository": manifest["repository"],
                "revision": manifest["revision"],
                "fileCount": len(manifest["files"]),
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
