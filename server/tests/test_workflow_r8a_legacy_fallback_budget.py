"""Each Legacy fallback request consumes authority; HTTP is entirely synthetic."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import server.main as main
import server.xpert_runtime.execution_store as store_module
from server.tests.test_workflow_r8a_budget_journal import LIMITS, create, initialize
from server.tests.test_workflow_r8a_budget_tree import budget, child, tree
from server.xpert_runtime.execution_budget import (
    XpertExecutionBudget, XpertExecutionBudgetExceeded, execution_operation,
    use_execution_budget,
)
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError, WorkflowExecutionStorageError, WorkflowExecutionStore,
)


def success():
    return httpx.Response(200, text=(
        'data: {"choices":[{"delta":{"content":"synthetic"},"finish_reason":"stop"}]}\n\n'
        'data: [DONE]\n\n'
    ))


def unavailable():
    return httpx.Response(404, json={"error": {"message": "model is not available"}})


@pytest.fixture
def transport(monkeypatch):
    requests = []
    responses = []
    original = httpx.AsyncClient

    async def handler(request):
        requests.append(json.loads(request.content)["model"])
        result = responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://synthetic.invalid", "synthetic-key"))
    monkeypatch.setattr(main, "llm_client_kwargs", lambda: {"trust_env": False})
    monkeypatch.setattr(main, "TEXT_FALLBACK_MODEL", "synthetic-fallback")
    return requests, responses


def stream():
    return main._stream_workflow_llm_messages(
        "synthetic-primary", [main.ChatMessage(role="user", content="synthetic")],
        temperature=0, max_tokens=64, token_usage={}, completion_receipt={},
    )


async def consume():
    return "".join([part async for part in stream()])


def counts(store):
    return [store.require(key).execution_budget["usage"]["model_calls"] for key in ("root", "child")]


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("cap", [1, 2])
async def test_fallback_reserves_every_request_at_the_root(tmp_path, transport, durable, cap):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path, cap=cap)
    child(store)
    value = budget(store, "child") if durable else XpertExecutionBudget(1, 1000, max_model_calls=cap)
    with use_execution_budget(value):
        if cap == 1:
            with pytest.raises(XpertExecutionBudgetExceeded):
                await asyncio.wait_for(consume(), 2)
        else:
            assert (await asyncio.wait_for(consume(), 2)).endswith("synthetic")
    assert requests == ["synthetic-primary", "synthetic-fallback"][:cap]
    assert value.model_calls == value.steps_used == cap
    if durable:
        reopened = WorkflowExecutionStore(store.storage_dir)
        assert counts(reopened) == [cap, cap]


@pytest.mark.asyncio
async def test_fallback_keeps_legacy_behavior_without_a_budget(transport):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    assert (await consume()).endswith("synthetic")
    assert requests == ["synthetic-primary", "synthetic-fallback"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["error", "timeout", "incomplete"])
async def test_second_request_failure_remains_charged_without_a_third_attempt(tmp_path, transport, fault):
    requests, responses = transport
    second = {
        "error": unavailable(),
        "timeout": httpx.ReadTimeout("synthetic uncertainty"),
        "incomplete": httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),
    }[fault]
    responses.extend([unavailable(), second])
    store = tree(tmp_path, cap=2)
    child(store)
    with use_execution_budget(budget(store, "child")):
        with pytest.raises((RuntimeError, httpx.ReadTimeout)):
            await consume()
    assert requests == ["synthetic-primary", "synthetic-fallback"]
    reopened = WorkflowExecutionStore(store.storage_dir)
    assert counts(reopened) == [2, 2]
    claimed = reopened.claim("child", worker_id="restart")
    with use_execution_budget(budget(reopened, "child", claimed.lease_token)):
        with pytest.raises(XpertExecutionBudgetExceeded):
            await consume()
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout", "incomplete", "invalid", "length", "http_error"])
async def test_first_request_uncertainty_or_nonfallback_failure_never_replays(tmp_path, transport, fault):
    requests, responses = transport
    responses.append({
        "timeout": httpx.ReadTimeout("synthetic uncertainty"),
        "incomplete": httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),
        "invalid": httpx.Response(200, text='data: invalid\n\n'),
        "length": httpx.Response(200, text='data: {"choices":[{"finish_reason":"length"}]}\n\n'),
        "http_error": httpx.Response(401, json={"error": {"message": "synthetic rejected"}}),
    }[fault])
    store = tree(tmp_path)
    child(store)
    with use_execution_budget(budget(store, "child")):
        with pytest.raises((RuntimeError, httpx.ReadTimeout)):
            await consume()
    assert requests == ["synthetic-primary"]
    assert counts(WorkflowExecutionStore(store.storage_dir)) == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cancel_root", "takeover_child", "close_consumer"])
async def test_fallback_suspension_rechecks_ownership_without_consuming_another_request(tmp_path, transport, fault):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path)
    child(store)
    claimed = store.claim("child", worker_id="old")
    value = budget(store, "child", claimed.lease_token)
    with use_execution_budget(value):
        iterator = stream()
        try:
            assert "synthetic-fallback" in await anext(iterator)
            if fault == "cancel_root":
                store.cancel("root")
            elif fault == "takeover_child":
                store.claim("child", worker_id="new", now=claimed.lease_expires_at + 1)
            if fault != "close_consumer":
                with pytest.raises(WorkflowExecutionConflictError):
                    await anext(iterator)
        finally:
            await iterator.aclose()
    assert requests == ["synthetic-primary"]
    assert counts(store) == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
async def test_second_admission_storage_failure_prevents_dispatch(tmp_path, transport, monkeypatch, phase):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path)
    child(store)
    with use_execution_budget(budget(store, "child")):
        iterator = stream()
        try:
            await anext(iterator)
            before = store.snapshot_path.read_bytes()

            original_replace = store_module.os.replace

            def fail(source, destination):
                if destination == store.snapshot_path:
                    if phase == "after_replace":
                        original_replace(source, destination)
                    raise OSError("synthetic persistence failure")
                return original_replace(source, destination)

            monkeypatch.setattr(store_module.os, "replace", fail)
            with pytest.raises(WorkflowExecutionStorageError):
                await anext(iterator)
            assert not store.available
            assert [store._items[key].execution_budget["usage"]["model_calls"]
                    for key in ("root", "child")] == [1, 1]
            if phase == "before_replace":
                assert store.snapshot_path.read_bytes() == before
            monkeypatch.setattr(store_module.os, "replace", original_replace)
        finally:
            await iterator.aclose()
    assert requests == ["synthetic-primary"]
    reopened = WorkflowExecutionStore(store.storage_dir)
    expected = 2 if phase == "after_replace" else 1
    assert counts(reopened) == [expected, expected]


@pytest.mark.asyncio
async def test_success_without_fallback_only_charges_once(tmp_path, transport):
    requests, responses = transport
    responses.append(success())
    store = tree(tmp_path, cap=1)
    child(store)
    with use_execution_budget(budget(store, "child")):
        assert await consume() == "synthetic"
    assert requests == ["synthetic-primary"]
    assert counts(store) == [1, 1]


@pytest.mark.asyncio
async def test_child_limit_does_not_partially_charge_root_on_fallback(tmp_path, transport):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path, cap=3)
    store.authorize_execution_budget_child("root", "narrow-child")
    create(store, "child")
    initialize(store, "child", limits={**LIMITS, "max_model_calls": 1})
    store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="narrow-child")
    with use_execution_budget(budget(store, "child")):
        with pytest.raises(XpertExecutionBudgetExceeded):
            await consume()
    assert requests == ["synthetic-primary"]
    assert counts(store) == [1, 1]


@pytest.mark.asyncio
async def test_inline_and_durable_parent_are_deduplicated_for_both_requests(tmp_path, transport):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path, cap=2)
    child(store)
    with use_execution_budget(budget(store, "root")), use_execution_budget(budget(store, "child")):
        assert (await asyncio.wait_for(consume(), 2)).endswith("synthetic")
    assert requests == ["synthetic-primary", "synthetic-fallback"]
    assert counts(store) == [2, 2]


@pytest.mark.asyncio
async def test_stream_and_fallback_share_one_concurrency_slot_until_closed(tmp_path, transport):
    requests, responses = transport
    responses.extend([unavailable(), success()])
    store = tree(tmp_path, cap=3, concurrency=1)
    child(store)
    child(store, "sibling")
    value, sibling = budget(store, "child"), budget(store, "sibling")
    entered = asyncio.Event()
    started = asyncio.Event()

    async def other():
        with use_execution_budget(sibling):
            started.set()
            async with execution_operation("model_call"):
                entered.set()

    # Construct the sibling task outside the first child's ContextVar scope.
    with use_execution_budget(value):
        iterator = stream()
        await anext(iterator)
    operation = asyncio.create_task(other())
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not entered.is_set()
        assert counts(store) == [1, 1]
        with use_execution_budget(value):
            assert await asyncio.wait_for(anext(iterator), 2) == "synthetic"
            assert counts(store) == [2, 2]
            assert not entered.is_set()
    finally:
        with use_execution_budget(value):
            await iterator.aclose()
        await asyncio.wait_for(operation, 2)
    assert entered.is_set()
    assert counts(store) == [3, 2]
    assert requests == ["synthetic-primary", "synthetic-fallback"]
