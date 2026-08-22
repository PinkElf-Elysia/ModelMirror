from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RAG_RUNTIME_CONTRACT_VERSION = "rag-runtime-v1"


def _fingerprint_payload(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_rag_runtime_identity(
    source_dir: Path | None = None,
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hash the loaded RAG package source contract without exposing source text."""

    root = (source_dir or Path(__file__).resolve().parent).resolve()
    source_hashes = [
        {
            "path": f"server/rag/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.glob("*.py"), key=lambda item: item.name)
        if path.is_file()
    ]
    if not source_hashes:
        raise RuntimeError("RAG runtime source identity is unavailable.")
    identity = {
        "version": RAG_RUNTIME_CONTRACT_VERSION,
        "source_hashes": source_hashes,
        "settings": json.loads(json.dumps(settings or {}, sort_keys=True)),
    }
    return {**identity, "fingerprint": _fingerprint_payload(identity)}


def is_valid_rag_runtime_identity(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    source_hashes = value.get("source_hashes")
    settings = value.get("settings")
    if (
        value.get("version") != RAG_RUNTIME_CONTRACT_VERSION
        or not isinstance(source_hashes, list)
        or not source_hashes
        or not isinstance(settings, dict)
    ):
        return False
    normalized: list[dict[str, str]] = []
    for item in source_hashes:
        if not isinstance(item, dict):
            return False
        path = str(item.get("path") or "")
        sha256 = str(item.get("sha256") or "")
        if not path.startswith("server/rag/") or len(sha256) != 64:
            return False
        try:
            int(sha256, 16)
        except ValueError:
            return False
        normalized.append({"path": path, "sha256": sha256})
    if normalized != sorted(normalized, key=lambda item: item["path"]):
        return False
    if len({item["path"] for item in normalized}) != len(normalized):
        return False
    identity = {
        "version": RAG_RUNTIME_CONTRACT_VERSION,
        "source_hashes": normalized,
        "settings": settings,
    }
    return str(value.get("fingerprint") or "") == _fingerprint_payload(identity)


_RAG_RUNTIME_SOURCE_HASHES = build_rag_runtime_identity()["source_hashes"]


def rag_runtime_identity(
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = {
        "version": RAG_RUNTIME_CONTRACT_VERSION,
        "source_hashes": json.loads(json.dumps(_RAG_RUNTIME_SOURCE_HASHES)),
        "settings": json.loads(json.dumps(settings or {}, sort_keys=True)),
    }
    return {**identity, "fingerprint": _fingerprint_payload(identity)}
