from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.mcp.hub import (
    HubError,
    MCPHubService,
    MCPHubStore,
    normalize_registry_entry,
    stable_digest,
)
from server.mcp.hub_contracts import (
    HubReviewedContractV1,
    contract_export,
    stable_contract_id,
)
from server.mcp.hub_review import MCPHubReviewService, MCPHubReviewStore
from server.mcp.hub_trusted import (
    MCPHubTrustedChannelService,
    MCPHubTrustedStore,
    configure_mcp_hub_trusted,
    router,
)


TOOLS = [
    {
        "name": "search",
        "description": "Search public metadata",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 80}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


def registry_entry(
    *,
    name: str = "io.example/trusted",
    version: str = "1.0.0",
    url: str = "https://trusted.example.com/mcp",
) -> dict[str, Any]:
    return {
        "server": {
            "name": name,
            "version": version,
            "title": "Trusted public search",
            "description": "Anonymous public metadata search",
            "publisher": {"name": "Trusted Publisher"},
            "remotes": [{"type": "streamable-http", "url": url}],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": "active",
                "isLatest": True,
            }
        },
    }


class FakeBridge:
    def __init__(self) -> None:
        self.tools = [dict(item) for item in TOOLS]
        self.closed: list[str] = []
        self.revoked: list[str] = []
        self.authorize_error: HubError | None = None
        self.call_count = 0

    async def authorize(self, candidate_id: str, url: str) -> str:
        if self.authorize_error is not None:
            raise self.authorize_error
        assert candidate_id.startswith("mcphub_")
        assert url.startswith("https://")
        return "a" * 64

    async def revoke(self, capability: str) -> None:
        self.revoked.append(capability)

    async def open(
        self,
        candidate_id: str,
        url: str,
        capability: str,
        session_owner: str,
    ) -> dict[str, Any]:
        assert session_owner == f"hub:tenant-a:owner-a:{candidate_id}"
        return {"session_id": "hubsession_" + "b" * 32, "tools": self.tools}

    async def list_tools(self, session_id: str) -> dict[str, Any]:
        return {"tools": self.tools}

    async def call(
        self, session_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.call_count += 1
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def reset(self) -> None:
        return None


@pytest.fixture(autouse=True)
def enable_trusted_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HUB_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REMOTE_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_REVIEW_FACTORY_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_TRUSTED_CHANNEL_ENABLED", "true")
    monkeypatch.setenv("MCP_HUB_AUTO_REVIEW_ENABLED", "false")


def normalized_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {
            "name": item["name"],
            "description": item.get("description", ""),
            "input_schema": item["input_schema"],
            "schema_digest": stable_digest(item["input_schema"]),
        }
        for item in tools
    ]
    return sorted(result, key=lambda item: item["name"])


def make_contract(source_digest: str) -> HubReviewedContractV1:
    tools = normalized_tools(TOOLS)
    return HubReviewedContractV1(
        contract_id=stable_contract_id(
            "io.example/trusted", "1.0.0", "https://trusted.example.com/mcp"
        ),
        server_name="io.example/trusted",
        version="1.0.0",
        remote_url="https://trusted.example.com/mcp",
        origin="https://trusted.example.com",
        source_digest=source_digest,
        schema_digest=stable_digest(tools),
        tool_schema_digests={item["name"]: item["schema_digest"] for item in tools},
        allowed_tools=["search"],
        tool_effects={"search": "read"},
        limits={"timeout_seconds": 20, "result_bytes": 262144},
        evidence_digest="e" * 64,
        published_at=1.0,
    )


def make_stack(
    tmp_path: Path,
    *,
    with_contract: bool = True,
) -> tuple[
    MCPHubService,
    MCPHubReviewService,
    MCPHubTrustedChannelService,
    FakeBridge,
    HubReviewedContractV1 | None,
]:
    hub_store = MCPHubStore(tmp_path)
    server = normalize_registry_entry(registry_entry())
    hub_store.replace_snapshot("hub_sync_seed", [server], '"seed"')
    bridge = FakeBridge()
    hub = MCPHubService(
        hub_store,
        tenant_id="tenant-a",
        owner_id="owner-a",
        bridge=bridge,
        reviewed_contracts=None,
    )
    repository_dir = tmp_path / "repo-contracts"
    repository_dir.mkdir()
    contract = make_contract(server["source_digest"]) if with_contract else None
    if contract is not None:
        (repository_dir / "trusted.json").write_bytes(contract_export(contract))
    review = MCPHubReviewService(
        hub,
        MCPHubReviewStore(hub_store),
        signing_key="trusted-test-signing-key-with-32-bytes",
        repository_dir=repository_dir,
    )
    trusted = MCPHubTrustedChannelService(
        hub,
        review,
        MCPHubTrustedStore(hub_store),
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    hub.set_trusted_service(trusted)
    return hub, review, trusted, bridge, contract


@pytest.mark.asyncio
async def test_trusted_activation_revalidates_without_probe_candidate_and_is_idempotent(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    initial = trusted.list_servers()["items"]
    assert initial[0]["availability_state"] == "stale"
    assert initial[0]["contract_source"] == "repository"

    active = await trusted.activate(contract.contract_id, contract.contract_fingerprint)
    repeated = await trusted.activate(contract.contract_id, contract.contract_fingerprint)

    assert active["state"] == "active"
    assert repeated["candidate_id"] == active["candidate_id"]
    assert len(hub.store.list_candidates("tenant-a", "owner-a")) == 1
    assert len(bridge.closed) == 2
    assert len(bridge.revoked) == 2
    assert trusted.get_server(contract.contract_id)["availability_state"] == "ready"
    assert len(hub.runtime_tools()) == 1


@pytest.mark.asyncio
async def test_concurrent_trusted_activation_serializes_one_candidate(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None

    first, second = await asyncio.gather(
        trusted.activate(contract.contract_id, contract.contract_fingerprint),
        trusted.activate(contract.contract_id, contract.contract_fingerprint),
    )

    assert first["candidate_id"] == second["candidate_id"]
    assert len(hub.store.list_candidates("tenant-a", "owner-a")) == 1
    assert len(bridge.closed) == 2


@pytest.mark.asyncio
async def test_environment_denial_is_not_mislabeled_as_contract_drift(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    bridge.authorize_error = HubError(
        "synthetic DNS denied",
        code="hub_dns_private_or_synthetic_denied",
        status_code=409,
    )

    with pytest.raises(HubError, match="synthetic DNS denied"):
        await trusted.revalidate(contract.contract_id, contract.contract_fingerprint)

    item = trusted.get_server(contract.contract_id)
    assert item["availability_state"] == "environment_blocked"
    assert item["health_error_code"] == "hub_dns_private_or_synthetic_denied"
    assert hub.store.list_candidates("tenant-a", "owner-a") == []
    assert trusted.activation_guard(contract.contract_id, contract.contract_fingerprint) == (
        False,
        "hub_trusted_environment_blocked",
    )


@pytest.mark.asyncio
async def test_manual_revalidation_is_rate_limited_without_reopening_bridge(
    tmp_path: Path,
) -> None:
    _hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    await trusted.revalidate(contract.contract_id, contract.contract_fingerprint)

    with pytest.raises(HubError) as exc_info:
        await trusted.revalidate(
            contract.contract_id,
            contract.contract_fingerprint,
            enforce_manual_rate_limit=True,
        )

    assert exc_info.value.code == "hub_trusted_recheck_rate_limited"
    assert exc_info.value.status_code == 429
    assert len(bridge.closed) == 1


@pytest.mark.asyncio
async def test_missing_registry_snapshot_is_stale_and_skips_network_maintenance(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    hub.store.set_meta("snapshot_at", "0")

    assert trusted.get_server(contract.contract_id)["availability_state"] == "stale"
    await trusted.run_maintenance()
    assert bridge.closed == []


@pytest.mark.asyncio
async def test_missing_registry_snapshot_cannot_be_mislabeled_as_source_drift(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    hub.store.set_meta("snapshot_at", "0")

    with pytest.raises(HubError) as exc_info:
        await trusted.activate(contract.contract_id, contract.contract_fingerprint)

    assert exc_info.value.code == "hub_registry_snapshot_missing"
    current = trusted.get_server(contract.contract_id)
    assert current["availability_state"] == "stale"
    assert current["health_error_code"] == "hub_registry_snapshot_missing"
    assert bridge.closed == []

    hub.store.set_meta("snapshot_at", str(time.time()))
    await trusted.run_maintenance()
    assert trusted.get_server(contract.contract_id)["availability_state"] == "ready"
    assert len(bridge.closed) == 1


def test_trusted_store_isolates_health_and_metrics_by_owner(tmp_path: Path) -> None:
    hub, _review, trusted, _bridge, contract = make_stack(tmp_path)
    assert contract is not None
    trusted.store.set_health(
        "tenant-a",
        "owner-a",
        contract.contract_id,
        contract.contract_fingerprint,
        state="ready",
    )
    trusted.store.record_event("tenant-a", "owner-a", "trusted_list_view")

    assert trusted.store.health("tenant-a", "owner-b", contract.contract_id) is None
    assert trusted.store.metrics("tenant-a", "owner-b", 0)["total"] == 0
    assert hub.store.list_candidates("tenant-a", "owner-b") == []


def test_review_run_trigger_migrates_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "hub.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE hub_review_runs (run_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, "
            "owner_id TEXT NOT NULL, status TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, "
            "error_code TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        db.execute(
            "INSERT INTO hub_review_runs VALUES('legacy','tenant-a','owner-a','completed',0,'',1,1)"
        )

    MCPHubReviewStore(path)

    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(hub_review_runs)")}
        trigger = db.execute(
            "SELECT trigger FROM hub_review_runs WHERE run_id='legacy'"
        ).fetchone()[0]
    assert "trigger" in columns
    assert trigger == "manual"


@pytest.mark.asyncio
async def test_schema_drift_removes_runtime_tool_and_disconnects_candidate(
    tmp_path: Path,
) -> None:
    hub, _review, trusted, bridge, contract = make_stack(tmp_path)
    assert contract is not None
    candidate = await trusted.activate(contract.contract_id, contract.contract_fingerprint)
    await hub._open_candidate(
        hub.store.require_candidate(candidate["candidate_id"], "tenant-a", "owner-a")
    )
    assert hub.runtime_tools()

    bridge.tools = [
        {
            **TOOLS[0],
            "input_schema": {
                **TOOLS[0]["input_schema"],
                "properties": {"query": {"type": "string", "maxLength": 40}},
            },
        }
    ]
    with pytest.raises(HubError) as exc_info:
        await trusted.revalidate(contract.contract_id, contract.contract_fingerprint)

    assert exc_info.value.code == "hub_schema_drift"
    assert trusted.get_server(contract.contract_id)["availability_state"] == "drifted"
    assert hub.get_candidate(candidate["candidate_id"])["state"] == "drifted"
    assert hub.runtime_tools() == []
    assert candidate["candidate_id"] not in hub._live


@pytest.mark.asyncio
async def test_auto_review_stops_before_representative_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_HUB_AUTO_REVIEW_ENABLED", "true")
    _hub, review, trusted, bridge, _contract = make_stack(
        tmp_path, with_contract=False
    )

    await trusted.run_maintenance()
    run = review.store.list_runs("tenant-a", "owner-a")[0]
    await review._tasks[run["run_id"]]
    current = review.store.require_run(run["run_id"], "tenant-a", "owner-a")

    assert current["trigger"] == "automatic"
    assert current["status"] == "awaiting_operator"
    assert current["items"][0]["state"] == "awaiting_call_approval"
    assert bridge.call_count == 0
    await trusted.run_maintenance()
    assert len(review.store.list_runs("tenant-a", "owner-a")) == 1


@pytest.mark.asyncio
async def test_trusted_api_rejects_client_url_and_wrong_fingerprint(
    tmp_path: Path,
) -> None:
    _hub, _review, trusted, _bridge, contract = make_stack(tmp_path)
    assert contract is not None
    configure_mcp_hub_trusted(trusted)
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        arbitrary = await client.post(
            f"/api/mcp/hub/trusted/servers/{contract.contract_id}/activate",
            json={
                "expected_contract_fingerprint": contract.contract_fingerprint,
                "url": "https://attacker.invalid/mcp",
            },
        )
        wrong = await client.post(
            f"/api/mcp/hub/trusted/servers/{contract.contract_id}/activate",
            json={"expected_contract_fingerprint": "0" * 64},
        )

    assert arbitrary.status_code == 422
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "hub_contract_fingerprint_mismatch"


def test_product_events_are_bounded_fixed_fields_without_payloads(tmp_path: Path) -> None:
    _hub, _review, trusted, _bridge, contract = make_stack(tmp_path)
    assert contract is not None
    trusted.record_runtime_event(
        "runtime_approval_shown",
        {
            "contract_id": contract.contract_id,
            "candidate_id": "mcphub_" + "1" * 32,
            "tool_name": "search",
            "arguments": {"secret": "must-not-persist"},
            "url": "https://must-not-persist.invalid",
        },
    )
    with sqlite3.connect(trusted.store.path) as db:
        row = db.execute("SELECT * FROM hub_product_events").fetchone()
        columns = [item[1] for item in db.execute("PRAGMA table_info(hub_product_events)")]
    encoded = json.dumps(dict(zip(columns, row)), sort_keys=True)
    assert "must-not-persist" not in encoded
    assert "arguments" not in columns
    assert "url" not in columns
    assert len(json.loads(encoded)["tool_digest"]) == 64


def test_expired_ready_health_fails_closed(tmp_path: Path) -> None:
    _hub, _review, trusted, _bridge, contract = make_stack(tmp_path)
    assert contract is not None
    trusted.store.set_health(
        "tenant-a",
        "owner-a",
        contract.contract_id,
        contract.contract_fingerprint,
        state="ready",
        checked_at=time.time() - 25 * 60 * 60,
    )
    assert trusted.get_server(contract.contract_id)["availability_state"] == "stale"
    assert trusted.activation_guard(contract.contract_id, contract.contract_fingerprint) == (
        False,
        "hub_trusted_revalidation_required",
    )
