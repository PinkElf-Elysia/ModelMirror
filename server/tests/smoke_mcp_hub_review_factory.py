"""Real Registry scale proof for MCP Hub Review Factory V1.

This is an operator harness, not a public API. It uses only Registry identities,
prints each immutable proposal before approving it, and fails instead of
publishing fixture or synthetic contracts when the real gate cannot be met.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from server.mcp.hub import HubError, HubSocketBridge, MCPHubService, MCPHubStore
from server.mcp.hub_contracts import HubContractRegistry, normalize_contract
from server.mcp.hub_review import MCPHubReviewService, MCPHubReviewStore


QT_NAME = "io.qt.qt-docs-mcp/qt-documentation"
QT_VERSION = "0.2.0"
SEED = "hub-review-factory-v1"


def _publisher_identity(server_name: str, publisher: str) -> str:
    """Use the same deterministic fallback as Registry batch selection."""

    return publisher.strip() or server_name.split("/", 1)[0]


def _identity_for(server: dict[str, Any]) -> dict[str, str]:
    remote = next(
        item for item in server["remotes"] if item.get("eligibility") == "eligible"
    )
    return {
        "server_name": server["server_name"],
        "version": server["version"],
        "remote_id": remote["remote_id"],
    }


async def _wait_run(review: MCPHubReviewService, run_id: str) -> dict[str, Any]:
    task = review._tasks.get(run_id)
    if task is not None:
        await task
    return review.store.require_run(run_id, review.tenant_id, review.owner_id)


async def _approve_representative(
    review: MCPHubReviewService, run: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    proposal = item["proposal"]
    print(
        "operator_proposal="
        + json.dumps(
            {
                "server_name": item["server_name"],
                "version": item["version"],
                "origin": item["evidence"]["snapshot"]["origin"],
                "tool_name": proposal["tool_name"],
                "arguments": proposal["arguments"],
                "proposal_digest": proposal["proposal_digest"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    await review.approve_proposal(
        run["run_id"],
        item["item_id"],
        proposal["proposal_id"],
        proposal["proposal_digest"],
    )
    return review.store.require_item(
        run["run_id"], item["item_id"], review.tenant_id, review.owner_id
    )


def _approve_contract(
    review: MCPHubReviewService,
    run: dict[str, Any],
    item: dict[str, Any],
    *,
    required_tools: set[str] | None = None,
) -> dict[str, Any]:
    effects = dict(item["evidence"].get("effect_proposals") or {})
    read_tools = sorted(
        name for name, effect in effects.items() if effect == "read_candidate"
    )
    if required_tools is not None:
        read_tools = sorted(required_tools)
    decided = review.decide(
        run["run_id"],
        item["item_id"],
        decision="approve",
        expected_evidence_digest=item["evidence_digest"],
        allowed_tools=read_tools,
        tool_effects={name: "read" for name in read_tools},
    )
    return review.publish(
        run["run_id"], item["item_id"], decided["contract_fingerprint"]
    )


def _block_remaining(review: MCPHubReviewService, run_id: str) -> None:
    run = review.store.require_run(run_id, review.tenant_id, review.owner_id)
    for item in run["items"]:
        if item["state"] not in {
            "awaiting_call_approval",
            "awaiting_decision",
            "approved",
        }:
            continue
        review.decide(
            run_id,
            item["item_id"],
            decision="block",
            expected_evidence_digest=item["evidence_digest"],
            allowed_tools=[],
            tool_effects={},
        )


async def run(
    storage_dir: Path,
    export_dir: Path,
    *,
    required_selection: int = 20,
    required_preflight: int = 5,
) -> dict[str, Any]:
    store = MCPHubStore(storage_dir)
    hub = MCPHubService(
        store,
        tenant_id="review-factory-acceptance",
        owner_id="local-operator",
        bridge=HubSocketBridge(),
        reviewed_contracts=None,
    )
    review_store = MCPHubReviewStore(store)
    review = MCPHubReviewService(
        hub,
        review_store,
        signing_key="review-factory-acceptance-signing-key-not-for-production",
    )
    hub.contract_registry = review.contracts
    hub.set_review_service(review)
    await hub.start()
    await review.start()
    try:
        if int(store.meta("snapshot_count", "0") or 0) <= 0:
            sync_id = store.create_sync()
            await hub._run_sync(sync_id)
            sync = store.get_sync(sync_id) or {}
            if sync.get("status") not in {"completed", "not_modified"}:
                raise RuntimeError(f"registry_sync_failed:{sync.get('error_code')}")
        else:
            print("registry_snapshot_reused=true", flush=True)
        print("registry_snapshot_count=" + store.meta("snapshot_count", "0"), flush=True)
        eligible_entries, _ = store.list_servers(
            eligibility="eligible", limit=50_000, offset=0
        )
        eligible_origins = sorted(
            {
                remote["origin"]
                for server in eligible_entries
                for remote in server.get("remotes") or []
                if remote.get("eligibility") == "eligible"
            }
        )
        print(f"eligible_registry_entries={len(eligible_entries)}", flush=True)
        print(f"eligible_origin_count={len(eligible_origins)}", flush=True)
        print(
            "eligible_origins_digest="
            + hashlib.sha256(
                json.dumps(eligible_origins, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            flush=True,
        )
        selection = review.reproducible_registry_selection(required_selection, SEED)
        print("selection=" + json.dumps(selection, sort_keys=True), flush=True)
        if len(selection) != required_selection:
            raise RuntimeError(f"scale_selection_short:{len(selection)}")

        batch = review.create_run(selection)
        batch = await _wait_run(review, batch["run_id"])
        preflight_items = [
            item
            for item in batch["items"]
            if str(item.get("evidence", {}).get("schema_digest") or "")
        ]
        print(f"static_classified={len(batch['items'])}", flush=True)
        print(f"real_preflight_passed={len(preflight_items)}", flush=True)
        if (
            len(batch["items"]) != required_selection
            or len(preflight_items) < required_preflight
        ):
            # A failed scale gate is terminal for this operator harness.  Do
            # not strand an awaiting-operator run that would block the next
            # reproducible attempt after diagnostics or an environment fix.
            review.cancel(batch["run_id"])
            raise RuntimeError("scale_preflight_gate_failed")

        qt_server = hub.get_server(QT_NAME, QT_VERSION)
        qt_publisher = _publisher_identity(
            QT_NAME, str(qt_server.get("publisher") or "")
        )
        new_published: dict[str, Any] | None = None
        new_item: dict[str, Any] | None = None
        for observed in batch["items"]:
            if observed["state"] != "awaiting_call_approval" or observed["server_name"] == QT_NAME:
                continue
            snapshot_publisher = _publisher_identity(
                observed["server_name"],
                str(
                    observed.get("evidence", {})
                    .get("snapshot", {})
                    .get("publisher")
                    or ""
                ),
            )
            if snapshot_publisher == qt_publisher:
                continue
            try:
                current = await _approve_representative(review, batch, observed)
                if current["state"] != "awaiting_decision":
                    continue
                new_published = _approve_contract(review, batch, current)
                new_item = current
                break
            except HubError as exc:
                print(
                    f"representative_rejected={observed['server_name']}:{exc.code}",
                    flush=True,
                )
        if new_published is None or new_item is None:
            review.cancel(batch["run_id"])
            raise RuntimeError("real_new_contract_gate_failed")
        _block_remaining(review, batch["run_id"])
        batch = review.store.require_run(
            batch["run_id"], review.tenant_id, review.owner_id
        )
        if batch["status"] != "completed":
            raise RuntimeError(f"batch_not_closed:{batch['status']}")

        qt_run = review.create_run([_identity_for(qt_server)])
        qt_run = await _wait_run(review, qt_run["run_id"])
        qt_item = qt_run["items"][0]
        if qt_item["state"] != "awaiting_call_approval":
            raise RuntimeError(f"qt_factory_preflight_failed:{qt_item['error_code']}")
        qt_item = await _approve_representative(review, qt_run, qt_item)
        if qt_item["state"] != "awaiting_decision":
            raise RuntimeError(
                f"qt_representative_call_failed:{qt_item['error_code']}"
            )
        repo_qt, reason = review.contracts.lookup_identity(
            QT_NAME, QT_VERSION, "https://qt-docs-mcp.qt.io/mcp"
        )
        if repo_qt is None or reason:
            raise RuntimeError(f"qt_repository_contract_unavailable:{reason}")
        qt_published = _approve_contract(
            review,
            qt_run,
            qt_item,
            required_tools=set(repo_qt.allowed_tools),
        )

        export_dir.mkdir(parents=True, exist_ok=True)
        new_export = review.export_contract(batch["run_id"], new_item["item_id"])
        qt_export = review.export_contract(qt_run["run_id"], qt_item["item_id"])
        (export_dir / "new.json").write_bytes(new_export)
        (export_dir / "qt.json").write_bytes(qt_export)
        exported_registry = HubContractRegistry(repository_dir=export_dir)
        exported_contracts, collisions = exported_registry.all()
        if collisions or len(exported_contracts) != 2:
            raise RuntimeError("export_loader_mismatch")
        if normalize_contract(json.loads(new_export)).contract_fingerprint != new_published["contract_fingerprint"]:
            raise RuntimeError("new_export_fingerprint_mismatch")

        for published in (new_published, qt_published):
            revoked = await review.revoke(published["contract_id"], "scale proof")
            if not revoked["revoked"]:
                raise RuntimeError("contract_revoke_failed")
        new_republished = review.publish(
            batch["run_id"],
            new_item["item_id"],
            new_published["contract_fingerprint"],
        )
        qt_republished = review.publish(
            qt_run["run_id"],
            qt_item["item_id"],
            qt_published["contract_fingerprint"],
        )
        if not new_republished["activation_eligible"] or not qt_republished["activation_eligible"]:
            raise RuntimeError("contract_republish_failed")

        unapproved_runtime = [
            tool
            for tool in hub.runtime_tools()
            if tool["candidate_id"]
            not in {new_item["candidate_id"], qt_item["candidate_id"]}
        ]
        if unapproved_runtime:
            raise RuntimeError("unapproved_runtime_tool_detected")
        return {
            "snapshot_count": int(store.meta("snapshot_count", "0")),
            "static_classified": len(batch["items"]),
            "real_preflight_passed": len(preflight_items),
            "required_selection": required_selection,
            "required_preflight": required_preflight,
            "acceptance_gate_met": (
                required_selection == 20
                and required_preflight == 5
                and len(batch["items"]) == 20
                and len(preflight_items) >= 5
            ),
            "new_contract_id": new_published["contract_id"],
            "new_server_name": new_item["server_name"],
            "qt_contract_id": qt_published["contract_id"],
            "published_contracts": 2,
            "export_loader_equal": True,
            "unapproved_runtime_tools": 0,
        }
    finally:
        await review.close()
        await hub.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-dir", type=Path, default=Path("/tmp/hub-review-storage"))
    parser.add_argument("--export-dir", type=Path, default=Path("/tmp/hub-review-export"))
    parser.add_argument("--required-selection", type=int, default=20)
    parser.add_argument("--required-preflight", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.required_selection <= 20:
        parser.error("--required-selection must be between 1 and 20")
    if not 1 <= args.required_preflight <= args.required_selection:
        parser.error("--required-preflight must be within the selected batch")
    result = asyncio.run(
        run(
            args.storage_dir,
            args.export_dir,
            required_selection=args.required_selection,
            required_preflight=args.required_preflight,
        )
    )
    print("scale_proof=" + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
