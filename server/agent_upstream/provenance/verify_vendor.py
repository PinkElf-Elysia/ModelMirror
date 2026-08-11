from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


UPSTREAM_REPOSITORY = "Prism-Shadow/penguin-harness"
UPSTREAM_REVISION = "047505dccc0cc16ad92be11011347d635f33ceb0"
VENDORED_PREFIXES = ("packages/core/", "packages/skills/")
VENDORED_ROOT_FILES = {
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
    "tsconfig.json",
}


def _git_blob(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _selected(path: str) -> bool:
    return path in VENDORED_ROOT_FILES or path.startswith(VENDORED_PREFIXES)


def _is_generated_path(path: Path, vendor_root: Path) -> bool:
    """Ignore package-manager/build output without following Windows junctions."""

    relative = path.relative_to(vendor_root)
    return any(part in {"node_modules", "dist"} for part in relative.parts)


def _source_files(vendor_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in vendor_root.rglob("*"):
        if _is_generated_path(path, vendor_root):
            continue
        try:
            if path.is_file():
                files.append(path)
        except OSError:
            # Generated package-manager junctions can be inaccessible on Windows.
            continue
    return files


def _load_tree(path: Path) -> dict[str, str]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("truncated"):
        raise ValueError("GitHub tree response is truncated")
    return {
        item["path"]: item["sha"]
        for item in payload.get("tree", [])
        if item.get("type") == "blob" and _selected(str(item.get("path", "")))
    }


def _manifest(vendor_root: Path, tree: dict[str, str]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for file_path in sorted(_source_files(vendor_root)):
        relative = file_path.relative_to(vendor_root).as_posix()
        expected = tree.get(relative)
        if expected is None:
            raise ValueError(f"Vendored path is absent from the fixed upstream tree: {relative}")
        actual = _git_blob(file_path.read_bytes())
        if actual != expected:
            raise ValueError(f"Vendored blob differs from fixed upstream: {relative}")
        entries.append(
            {
                "upstream_path": relative,
                "upstream_blob_sha1": expected,
                "local_path": f"server/agent_upstream/vendor/penguin_harness/{relative}",
                "modified": False,
            }
        )
    missing = sorted(set(tree) - {entry["upstream_path"] for entry in entries})
    if missing:
        raise ValueError(f"Fixed upstream files are missing from vendor: {missing[:5]}")
    return {
        "schema_version": 1,
        "repository": UPSTREAM_REPOSITORY,
        "revision": UPSTREAM_REVISION,
        "license": "Apache-2.0",
        "files": entries,
    }


def verify(manifest_path: Path, vendor_root: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("repository") != UPSTREAM_REPOSITORY:
        raise ValueError("Vendor repository does not match the pinned source")
    if manifest.get("revision") != UPSTREAM_REVISION:
        raise ValueError("Vendor revision does not match the pinned source")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Vendor manifest contains no files")
    expected_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("modified") is not False:
            raise ValueError("Vendor manifest contains a modified or invalid entry")
        upstream_path = entry.get("upstream_path")
        expected_blob = entry.get("upstream_blob_sha1")
        if not isinstance(upstream_path, str) or not isinstance(expected_blob, str):
            raise ValueError("Vendor manifest entry is incomplete")
        file_path = vendor_root / Path(upstream_path)
        if not file_path.is_file():
            raise ValueError(f"Vendored file is missing: {upstream_path}")
        if _git_blob(file_path.read_bytes()) != expected_blob:
            raise ValueError(f"Vendored file drifted: {upstream_path}")
        expected_paths.add(upstream_path)
    actual_paths = {
        path.relative_to(vendor_root).as_posix() for path in _source_files(vendor_root)
    }
    extra = sorted(actual_paths - expected_paths)
    missing = sorted(expected_paths - actual_paths)
    if extra or missing:
        raise ValueError(f"Vendor file set drifted: extra={extra[:5]} missing={missing[:5]}")


def main() -> None:
    package_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree-json", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=package_root / "provenance" / "vendor-manifest.json",
    )
    parser.add_argument(
        "--vendor-root",
        type=Path,
        default=package_root / "vendor" / "penguin_harness",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        if args.tree_json is None:
            parser.error("--tree-json is required with --write")
        manifest = _manifest(args.vendor_root, _load_tree(args.tree_json))
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    verify(args.manifest, args.vendor_root)
    count = len(json.loads(args.manifest.read_text(encoding="utf-8"))["files"])
    print(f"verified {count} vendored files")


if __name__ == "__main__":
    main()
