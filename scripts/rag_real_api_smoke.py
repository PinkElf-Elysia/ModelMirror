from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import SqliteLexicalStore
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


def _gateway_base() -> str:
    url = os.getenv("LLM_GATEWAY_URL", "").strip().rstrip("/")
    suffix = "/chat/completions"
    return url[: -len(suffix)] if url.endswith(suffix) else url


async def _embedding_models(base: str, key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        response = await client.get(
            f"{base}/embeddings/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        response.raise_for_status()
        payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [
        str(item.get("id") or "")
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "")
    ]


def _embedding_model(model_ids: list[str]) -> str:
    configured = (
        os.getenv("RAG_REAL_SMOKE_EMBEDDING_MODEL", "").strip()
        or os.getenv("EMBEDDING_MODEL", "").strip()
    )
    if configured:
        return configured
    preferred = [
        "openai/text-embedding-3-small",
        "text-embedding-3-small",
        "baai/bge-m3",
        "qwen/qwen3-embedding-8b",
    ]
    by_lower = {item.lower(): item for item in model_ids}
    for candidate in preferred:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    candidates = [
        item
        for item in model_ids
        if any(token in item.lower() for token in ("embedding", "embed", "bge-m3"))
    ]
    return candidates[0] if candidates else preferred[0]


def _embedding_route(gateway_base: str, gateway_key: str) -> tuple[str, str, str]:
    configured_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    configured_base = os.getenv("EMBEDDING_API_BASE", "").strip().rstrip("/")
    if configured_key and configured_base:
        return configured_base, configured_key, "configured"

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        return "https://openrouter.ai/api/v1", openrouter_key, "openrouter"
    return gateway_base, gateway_key, "gateway"


def _safe_failure(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": "rag-p0-real-api-smoke-v1",
        "status": "failed",
        "error_type": exc.__class__.__name__,
        "secrets_recorded": False,
        "shared_storage_mutated": False,
    }


async def _run() -> dict[str, Any]:
    gateway_key = os.getenv("LLM_GATEWAY_KEY", "").strip()
    gateway_base = _gateway_base()
    chat_model = (
        os.getenv("RAG_RERANK_LLM_MODEL", "").strip()
        or os.getenv("OPENROUTER_TEXT_FALLBACK_MODEL", "").strip()
    )
    if not gateway_key or not gateway_base or not chat_model:
        raise RuntimeError("Required real API smoke configuration is unavailable.")

    embedding_base, embedding_key, embedding_route = _embedding_route(
        gateway_base,
        gateway_key,
    )
    model_ids = await _embedding_models(embedding_base, embedding_key)
    embedding_model = _embedding_model(model_ids)
    embedder = EmbeddingClient(
        api_base=embedding_base,
        api_key=embedding_key,
        model=embedding_model,
        dimension=384,
    )
    embedder.embedding_mode = ""

    with tempfile.TemporaryDirectory(prefix="modelmirror-rag-real-smoke-") as root:
        root_path = Path(root)
        storage = root_path / "storage"
        service = RagService(
            storage_dir=storage,
            uploads_dir=root_path / "uploads",
            embedder=embedder,
            vector_store=LocalJsonVectorStore(storage / "vectors.json"),
            lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
            llm_enabled=False,
        )
        kb = service.create_knowledge_base("real-api-smoke")
        relevant = await service.upload_document(
            kb["id"],
            "release.txt",
            (
                "ORBIT-SAFFRON production release requires a signed safety review "
                "before deployment."
            ).encode("utf-8"),
            pipeline_only=True,
        )
        distractor = await service.upload_document(
            kb["id"],
            "office.txt",
            "Office badges are renewed every twelve months.".encode("utf-8"),
            pipeline_only=True,
        )
        draft = service.update_pipeline_draft(
            kb["id"],
            {},
            embedding_profile={
                "provider": "openai_compatible",
                "model": embedding_model,
            },
            retrieval_profile={
                "mode": "hybrid",
                "top_k": 2,
                "score_threshold": 0.0,
                "candidate_multiplier": 2,
                "rerank_enabled": True,
                "rerank_provider": "llm",
                "rerank_model": chat_model,
                "rerank_top_n": 2,
            },
        )
        job = service.create_pipeline_job(
            kb["id"],
            draft_version=draft["version"],
            source_document_ids=[relevant["id"], distractor["id"]],
        )
        if not await KnowledgePipelineExecutor(service).run_once():
            raise RuntimeError("Pipeline executor did not claim the smoke job.")
        completed = service.get_pipeline_job(job["job_id"])
        if completed["status"] != "succeeded":
            raise RuntimeError("Real embedding pipeline did not succeed.")
        version_id = str(completed["candidate_version_id"])
        result = await service.query_pipeline_version(
            version_id,
            "What review is required before ORBIT-SAFFRON deployment?",
            retrieval={"mode": "hybrid", "top_k": 2},
            generate_answer=False,
        )
        retrieval = dict(result.get("retrieval") or {})
        sources = list(result.get("sources") or [])
        if not sources or retrieval.get("rerank_provider_used") != "llm":
            raise RuntimeError("Real retrieval or LLM rerank was not applied.")
        evidence = service.pipeline_version_evidence(version_id)
        return {
            "schema_version": "rag-p0-real-api-smoke-v1",
            "status": "passed",
            "embedding": {
                "provider": retrieval.get("embedding_provider"),
                "model": retrieval.get("embedding_model"),
                "dimension": retrieval.get("embedding_dimension"),
                "route": embedding_route,
                "expected_api_calls": 2,
            },
            "rerank": {
                "provider": retrieval.get("rerank_provider_used"),
                "model": retrieval.get("rerank_model_used"),
                "applied": retrieval.get("rerank_applied"),
                "expected_llm_calls": 1,
            },
            "retrieval": {
                "source_count": len(sources),
                "top_document": str(sources[0].get("document_name") or ""),
                "version_fingerprint": evidence["version_fingerprint"],
            },
            "secrets_recorded": False,
            "shared_storage_mutated": False,
        }


def main() -> int:
    try:
        result = asyncio.run(_run())
    except Exception as exc:
        result = _safe_failure(exc)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
