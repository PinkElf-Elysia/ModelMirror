from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.xpert_runtime.core_middlewares import RuntimeMiddlewareSpec
from server.xpert_runtime.file_memory_middleware import (
    build_xpert_file_memory_middleware,
)
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import MiddlewareContext, ModelCallRequest
from server.xperts.context import XpertContextConflictError, XpertContextStore


def test_file_memory_persists_categories_index_signals_and_conflicts(tmp_path: Path) -> None:
    store = XpertContextStore(tmp_path / "runtime")
    records = [
        store.create_memory(
            "xpert-1",
            scope="xpert",
            memory_type=memory_type,
            title=f"{memory_type} title",
            summary=f"{memory_type} summary",
            content=f"Durable {memory_type} content",
            tags=[memory_type],
        )
        for memory_type in ("user", "feedback", "project", "reference")
    ]

    index = store.file_memory_index("xpert-1")
    assert index["active_count"] == 4
    assert all(f"## {memory_type}" in index["content"] for memory_type in ("user", "feedback", "project", "reference"))
    assert "memory://xpert/" in index["content"]
    index_revision = index["index_revision"]
    assert store.file_memory_index("xpert-1")["index_revision"] == index_revision

    first = records[0]
    updated = store.update_memory(
        "xpert-1",
        first.memory_id,
        revision=first.revision,
        summary="Corrected preference",
    )
    assert updated.revision == 2
    with pytest.raises(XpertContextConflictError):
        store.update_memory(
            "xpert-1",
            first.memory_id,
            revision=1,
            summary="stale",
        )

    store.get_memory("xpert-1", first.memory_id, record_detail_read=True)
    signals = store.file_memory_signals("xpert-1", memory_id=first.memory_id)
    assert {item["signal_type"] for item in signals} >= {"correction", "detail_read"}

    reloaded = XpertContextStore(tmp_path / "runtime")
    restored = reloaded.get_memory("xpert-1", first.memory_id)
    assert restored.summary == "Corrected preference"
    payload = reloaded.memory_payload(restored)
    assert payload["canonical_ref"].startswith("memory://xpert/")
    assert "body_key" not in payload


def test_lazy_migration_preserves_legacy_id_and_conversation_memory(tmp_path: Path) -> None:
    context_dir = tmp_path / "runtime" / "xpert_context"
    context_dir.mkdir(parents=True)
    snapshot = {
        "version": "xpert-context-v1",
        "conversations": [],
        "assets": [],
        "memories": [
            {
                "memory_id": "legacy-xpert",
                "xpert_id": "xpert-1",
                "scope": "xpert",
                "content": "Legacy durable decision",
                "conversation_id": None,
                "tags": ["old"],
                "source_type": "user",
                "source_id": None,
                "status": "active",
                "created_at": 1.0,
                "updated_at": 2.0,
            },
            {
                "memory_id": "conversation-only",
                "xpert_id": "xpert-1",
                "scope": "conversation",
                "content": "Private conversation note",
                "conversation_id": "conversation-1",
                "tags": [],
                "source_type": "user",
                "source_id": None,
                "status": "active",
                "created_at": 1.0,
                "updated_at": 2.0,
            },
        ],
        "candidates": [],
    }
    (context_dir / "context.json").write_text(json.dumps(snapshot), encoding="utf-8")

    store = XpertContextStore(tmp_path / "runtime")
    migrated = store.get_memory("xpert-1", "legacy-xpert")
    assert migrated.memory_id == "legacy-xpert"
    assert migrated.memory_type == "project"
    assert "legacy-import" in migrated.tags
    assert store.get_memory("xpert-1", "conversation-only").scope == "conversation"

    reloaded = XpertContextStore(tmp_path / "runtime")
    assert reloaded.get_memory("xpert-1", "legacy-xpert").memory_id == "legacy-xpert"
    raw = json.loads(reloaded.snapshot_path.read_text(encoding="utf-8"))
    assert [item["memory_id"] for item in raw["memories"]] == ["conversation-only"]


@pytest.mark.asyncio
async def test_file_memory_middleware_injects_layered_context_once_and_honors_budget(
    tmp_path: Path,
) -> None:
    store = XpertContextStore(tmp_path / "runtime")
    conversation = store.create_conversation("xpert-1")
    for index in range(3):
        store.create_memory(
            "xpert-1",
            scope="xpert",
            memory_type="project",
            title=f"Aurora {index}",
            summary="Aurora launch decision",
            content=("Aurora details " * 300),
        )
    middleware = build_xpert_file_memory_middleware(
        RuntimeMiddlewareSpec(
            node_id="memory",
            middleware_id="xpert_file_memory",
            config={
                "recall_mode": "deterministic",
                "max_selected": 2,
                "digest_limit": 3,
                "max_detail_chars_per_turn": 1500,
                "max_detail_chars_per_session": 2000,
            },
        ),
        store,
    )
    pipeline = MiddlewarePipeline([middleware])
    context = MiddlewareContext(
        metadata={
            "xpert_id": "xpert-1",
            "conversation_id": conversation.conversation_id,
            "runtime_run_type": "xpert",
            "run_id": "run-1",
        }
    )
    request = ModelCallRequest(
        model_id="model",
        messages=[
            {"role": "system", "content": "Answer carefully."},
            {"role": "user", "content": "What did we decide for Aurora?"},
        ],
    )

    prepared = await pipeline.before_model(request, context)
    assert len(prepared.messages) == 2
    assert "Memory index" in prepared.messages[1]["content"]
    assert "Selected details" in prepared.messages[1]["content"]
    assert context.metadata["xpert_file_memory"]["detail_chars"] <= 1500

    repeated = await pipeline.before_model(prepared, context)
    assert len(repeated.messages) == 2
    restored = store.get_conversation("xpert-1", conversation.conversation_id)
    assert restored.memory_detail_chars_used <= 1500


@pytest.mark.asyncio
async def test_file_memory_selector_failure_falls_back_and_app_policy_can_disable(
    tmp_path: Path,
) -> None:
    store = XpertContextStore(tmp_path / "runtime")
    store.create_memory(
        "xpert-1",
        scope="xpert",
        content="The preferred release color is green.",
        title="Release color",
    )

    async def fail_selector(*_args):
        raise RuntimeError("selector unavailable")

    middleware = build_xpert_file_memory_middleware(
        RuntimeMiddlewareSpec(
            node_id="memory",
            middleware_id="xpert_file_memory",
            config={"recall_mode": "hybrid", "selector_model_id": "selector"},
        ),
        store,
    )
    request = ModelCallRequest(
        model_id="model",
        messages=[{"role": "user", "content": "Which release color?"}],
    )
    context = MiddlewareContext(
        metadata={
            "xpert_id": "xpert-1",
            "runtime_run_type": "xpert",
            "middleware_model_text": fail_selector,
        }
    )
    prepared = await MiddlewarePipeline([middleware]).before_model(request, context)
    assert "preferred release color" in prepared.messages[-1]["content"]
    assert any("selector fallback" in item for item in context.metadata["middleware_warnings"])

    app_context = MiddlewareContext(
        metadata={
            "xpert_id": "xpert-1",
            "runtime_run_type": "xpert_app",
            "app_policy": {"allow_xpert_memory": False},
        }
    )
    app_prepared = await MiddlewarePipeline([middleware]).before_model(request, app_context)
    assert app_prepared.messages == request.messages
