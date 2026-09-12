"""Explicit, bounded real smoke; importing this module performs no I/O."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable
from uuid import uuid4

from .adapter import AgentsAPIAdapter, AgentsAPIError, TERMINAL, recorded_usage

MARKER = "MM_AGENTS_V1_ALPHA"
FIRST_INPUT = f"Remember this marker for our next turn: {MARKER}. Reply with exactly READY and nothing else."
SECOND_INPUT = "Return exactly the marker I asked you to remember in the previous turn, with no other text."
CALL_SECONDS = 180
CLEANUP_SECONDS = 30


def safe_error(error: BaseException) -> dict[str, Any]:
    if isinstance(error, AgentsAPIError):
        return error.safe_dict()
    if isinstance(error, asyncio.CancelledError):
        return {"category": "interrupted"}
    if isinstance(error, TimeoutError):
        return {"category": "deadline_exceeded"}
    return {"category": "local_error"}  # Never serialize exception text.


def final_history(items: list[dict[str, Any]], turn_id: str) -> str:
    messages = [
        item for item in items
        if item.get("type") == "message" and item.get("role") == "assistant"
        and item.get("turn_id") == turn_id and item.get("status") == "completed"
    ]
    has_final = any(item.get("phase") == "final_answer" for item in messages)
    parts: list[str] = []
    for item in messages:
        if item.get("phase") != "final_answer" and (has_final or item.get("phase") is not None):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise AgentsAPIError("invalid_saved_message")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise AgentsAPIError("unexpected_saved_content")
            text = part.get("text")
            if not isinstance(text, str):
                raise AgentsAPIError("invalid_saved_text")
            parts.append(text)
    return "".join(parts)


def session_summary(value: dict[str, Any]) -> dict[str, Any]:
    status = value.get("status")
    return {
        "status": status if status in {"idle", "in_progress", "requires_action", "failed"} else "unknown",
        "usage": recorded_usage(value.get("usage")),
    }


async def cleanup_session(
    adapter: AgentsAPIAdapter, report: dict[str, Any], *, seconds: float
) -> None:
    cleanup = report["cleanup"]
    sid = adapter.session_id
    if sid is None:
        cleanup["status"] = "not_applicable"
        cleanup["remote_state"] = "unknown" if adapter.create_attempted else "not_created"
        return

    async def step(name: str, operation: Awaitable[Any]) -> Any:
        try:
            # A failed read must not consume the entire opportunity to delete.
            value = await asyncio.wait_for(operation, timeout=min(5.0, seconds / 6))
            cleanup[name] = {"status": "passed"}
            return value
        except Exception as error:
            cleanup[name] = {"status": "failed", "error": safe_error(error)}
            return None

    try:
        async with asyncio.timeout(seconds):
            if report["invocation_status"] != "passed":
                # A failed/ambiguous submission may still be executing remotely.
                if adapter.last_status not in TERMINAL:
                    await step("cancel_request", adapter.cancel_turn(sid))
                state = await step("inspect_session", adapter.retrieve_session(sid))
                if state is not None:
                    report["recovered_session"] = session_summary(state)
                if adapter.turn_ids:
                    turn = await step("inspect_turn", adapter.retrieve_turn(sid, adapter.turn_ids[-1]))
                    if turn is not None:
                        status = turn.get("status")
                        cleanup["observed_turn_status"] = status if status in TERMINAL else "not_terminal"
                        cleanup["observed_turn_usage"] = recorded_usage(turn.get("usage"))
                items = await step("inspect_history", adapter.list_items(sid))
                if items is not None:
                    # Recover safe final outputs only, not reasoning/tool payloads.
                    try:
                        report["recovered_outputs"] = [
                            {"turn_id": tid, "text": final_history(items, tid)}
                            for tid in adapter.turn_ids
                        ]
                    except AgentsAPIError as error:
                        report["recovery_error"] = safe_error(error)
            deletion = await step("delete_request", adapter.delete_session(sid))
            if deletion is not None:
                cleanup["deletion_confirmed"] = deletion["deleted"]
            try:
                async with asyncio.timeout(min(5.0, seconds / 6)):
                    await adapter.retrieve_session(sid)
                cleanup["api_absent"] = False
            except AgentsAPIError as error:
                if error.http_status == 404:
                    cleanup["api_absent"] = True
                else:
                    cleanup["verify_error"] = safe_error(error)
            except Exception as error:
                cleanup["verify_error"] = safe_error(error)
    except (Exception, asyncio.CancelledError) as error:
        cleanup["error"] = safe_error(error)
    cleanup["status"] = (
        "passed" if cleanup["deletion_confirmed"] and cleanup["api_absent"] else "failed"
    )
    # API deletion cannot prove physical cleanup or exact final billing.
    cleanup["physical_cleanup"] = "unverified"


async def run_probe(
    adapter: AgentsAPIAdapter, model: str, *,
    evidence: str, call_seconds: float = CALL_SECONDS, cleanup_seconds: float = CLEANUP_SECONDS,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1, "evidence": evidence, "model": model,
        "status": "failed", "invocation_status": "not_run",
        "limits": {"sessions": 1, "inputs": 2, "call_seconds": call_seconds, "cleanup_seconds": cleanup_seconds},
        "checks": {"first_turn": "not_run", "second_turn": "not_run", "history": "not_run"},
        "cleanup": {
            "status": "not_run", "deletion_confirmed": False,
            "api_absent": False, "physical_cleanup": "unverified",
        },
    }
    start = time.monotonic()
    stage = "first_turn"
    try:
        async with asyncio.timeout(call_seconds):
            first = await adapter.create_session(model, FIRST_INPUT)
            if first.text != "READY":
                raise AgentsAPIError("first_output_mismatch")
            report["checks"][stage] = "passed"
            stage = "second_turn"
            second = await adapter.continue_session(SECOND_INPUT)
            if second.text != MARKER or first.turn_id == second.turn_id:
                raise AgentsAPIError("second_output_mismatch")
            report["checks"][stage] = "passed"
            stage = "history"
            state = await adapter.retrieve_session(second.session_id)
            report["session"] = session_summary(state)
            agent = state.get("agent")
            if (
                state.get("environment") != {"type": "none"} or not isinstance(agent, dict)
                or agent.get("tools") != [] or not isinstance(agent.get("multi_agent"), dict)
                or agent["multi_agent"].get("enabled") is not False
            ):
                raise AgentsAPIError("session_configuration_mismatch")
            if state.get("status") != "idle" or state.get("required_actions"):
                raise AgentsAPIError("session_not_idle")
            for result in (first, second):
                turn = await adapter.retrieve_turn(result.session_id, result.turn_id)
                if turn.get("status") != "completed" or turn.get("subagent_id") is not None:
                    raise AgentsAPIError("saved_turn_not_completed")
                result.usage = recorded_usage(turn.get("usage"))
            items = await adapter.list_items(second.session_id)
            if (
                final_history(items, first.turn_id) != "READY"
                or final_history(items, second.turn_id) != MARKER
            ):
                raise AgentsAPIError("saved_history_mismatch")
            report["checks"][stage] = "passed"
            report["invocation_status"] = "passed"
    except (Exception, asyncio.CancelledError) as error:
        report["checks"][stage] = "failed"
        report["invocation_status"] = "failed"
        report["error"] = safe_error(error)
    finally:
        await cleanup_session(adapter, report, seconds=cleanup_seconds)
        report["session_id"] = adapter.session_id
        report["turn_ids"] = list(adapter.turn_ids)
        report["inputs_submitted"] = adapter.inputs_submitted
        report["turns"] = [asdict(result) for result in adapter.results]
        report["requests"] = adapter.requests
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    if report["invocation_status"] == "passed" and report["cleanup"]["status"] == "passed":
        report["status"] = "passed"
    return report


def source_receipt(root: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
        check=True, timeout=5,
    )
    revision = result.stdout.strip()
    if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
        raise AgentsAPIError("invalid_source_revision")
    directory = Path(__file__).resolve().parent
    return {
        "base_sha": revision,
        "source_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("adapter.py", "smoke.py", "test_adapter.py", "README.md")
        },
    }


def receipt_json(report: dict[str, Any], key: str) -> str:
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    # Defense in depth if the credential is unexpectedly echoed in safe fields.
    return encoded.replace(key, "[REDACTED]") if key else encoded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit OpenAI Agents API synthetic two-turn smoke.")
    parser.add_argument("--live", action="store_true", help="Required: allow one billed session with at most two inputs.")
    parser.add_argument("--model", help="Required for --live; this experiment uses gpt-6-astra, with no fallback.")
    args = parser.parse_args(argv)
    if not args.live or not args.model:
        parser.error("Both --live and --model are required; no request was sent.")

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    root = Path(__file__).resolve().parents[2]
    started = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "schema_version": 1, "evidence": "real_api", "model": args.model,
        "status": "not_run", "started_at": started, "requests": [],
    }
    path: Path | None = None
    try:
        report.update(source_receipt(root))
        output = root / "artifacts" / "openai-agents-api"
        output.mkdir(parents=True, exist_ok=True)
        path = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex + ".json")
        # Check receipt writability before creating any remote state.
        with path.open("x", encoding="utf-8") as file:
            file.write(receipt_json(report, key) + "\n")
        if not key:
            raise AgentsAPIError("missing_openai_api_key")

        async def execute() -> dict[str, Any]:
            async with AgentsAPIAdapter(key) as adapter:
                return await run_probe(adapter, args.model, evidence="real_api")

        report.update(asyncio.run(execute()))
    except KeyboardInterrupt:
        report["status"] = "failed"
        report["error"] = {"category": "interrupted_outside_cleanup"}
    except Exception as error:
        report["error"] = safe_error(error)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    serialized = receipt_json(report, key)
    if path is not None:
        try:
            path.write_text(serialized + "\n", encoding="utf-8")
        except OSError:
            print('{"status":"failed","error":{"category":"receipt_write_failed"}}')
            print(serialized)
            return 1
        print("Receipt: " + str(path))
    print(serialized)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
