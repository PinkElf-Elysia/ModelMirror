from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def loopback_control_url() -> str:
    default = f"http://127.0.0.1:{os.getenv('AI_RESEARCH_CONTROL_PORT', '8790')}"
    value = os.getenv("AI_RESEARCH_ACCEPTANCE_CONTROL_URL", default).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("AI_RESEARCH_ACCEPTANCE_CONTROL_URL must be an HTTP loopback origin")
    try:
        if parsed.port is None:
            raise RuntimeError("AI_RESEARCH_ACCEPTANCE_CONTROL_URL must include an explicit port")
    except ValueError as exc:
        raise RuntimeError("AI_RESEARCH_ACCEPTANCE_CONTROL_URL has an invalid port") from exc
    return value


CONTROL = loopback_control_url()
ARTIFACTS = {
    "literature-review.md",
    "upstream-quarto.zip",
    "literature-review.qmd",
    "references.bib",
    "references.ris",
    "sources.json",
    "literature-receipt.json",
    "artifact-manifest.json",
}


class AcceptanceFailure(RuntimeError):
    pass


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any, dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    value = urllib.request.Request(CONTROL + path, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(value, timeout=30) as response:
            raw = response.read(128 * 1024 * 1024 + 1)
            if len(raw) > 128 * 1024 * 1024:
                raise AcceptanceFailure(f"oversized response from {path}")
            content_type = response.headers.get_content_type()
            attachment = response.headers.get("Content-Disposition", "").lower().startswith("attachment;")
            decoded: Any = (
                json.loads(raw)
                if raw and content_type == "application/json" and not attachment
                else raw
            )
            return response.status, decoded, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read(1024 * 1024 + 1)
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = raw.decode("utf-8", errors="replace")
        return exc.code, decoded, dict(exc.headers)


def expect(status: int, body: Any, allowed: set[int], operation: str) -> dict[str, Any]:
    if status not in allowed or not isinstance(body, dict):
        raise AcceptanceFailure(f"{operation} failed: HTTP {status} {body}")
    return body


def unlock() -> str:
    username = os.environ.get("AI_RESEARCH_ACCEPTANCE_LDR_USERNAME", "")
    password = os.environ.get("AI_RESEARCH_ACCEPTANCE_LDR_PASSWORD", "")
    if not username or not password:
        raise AcceptanceFailure("live acceptance requires LDR username and password environment inputs")
    status, body, _ = request(
        "POST",
        "/api/v1/literature/session/unlock",
        {"username": username, "password": password},
    )
    session = expect(status, body, {200}, "unlocking LDR")
    if session.get("status") != "ready" or session.get("username") != username:
        raise AcceptanceFailure("LDR did not return the unlocked account identity")
    return username


def prepare_collection() -> str:
    collection_id = os.environ.get("AI_RESEARCH_ACCEPTANCE_COLLECTION_ID", "")
    if not collection_id:
        raise AcceptanceFailure("live acceptance requires an explicitly approved Zotero/LDR collection id")
    status, body, _ = request("GET", "/api/v1/literature/zotero/status")
    zotero = expect(status, body, {200}, "reading Zotero status")
    config = zotero.get("config")
    if not isinstance(config, dict) or not (config.get("configured") or config.get("has_api_key")):
        raise AcceptanceFailure("Zotero must be configured by the user in LDR before acceptance")
    status, body, _ = request("POST", "/api/v1/literature/zotero/sync")
    expect(status, body, {200}, "synchronizing Zotero")
    encoded = urllib.parse.quote(collection_id, safe="")
    status, body, _ = request("POST", f"/api/v1/literature/library/collections/{encoded}/index")
    indexed = expect(status, body, {200}, "indexing the approved collection")
    if indexed.get("status") != "completed":
        raise AcceptanceFailure("LDR collection indexing did not complete")
    status, body, _ = request("GET", "/api/v1/literature/library/collections")
    collections = expect(status, body, {200}, "reading LDR collections").get("collections")
    selected = next((item for item in collections or [] if isinstance(item, dict) and item.get("id") == collection_id), None)
    if not selected:
        raise AcceptanceFailure("the approved collection was not returned by LDR")
    if not (
        selected.get("is_public") is True
        and selected.get("agent_enabled") is True
        and int(selected.get("indexed_document_count") or 0) > 0
    ):
        raise AcceptanceFailure("the approved collection did not pass index and egress gates")
    return collection_id


def wait_for_terminal(project_id: str, timeout: float = 1800.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    encoded = urllib.parse.quote(project_id, safe="")
    while time.monotonic() < deadline:
        status, body, _ = request("GET", f"/api/v1/projects/{encoded}")
        last = (status, body)
        if status == 200 and isinstance(body, dict) and body.get("literaturePhase") == "terminal":
            return body
        time.sleep(3)
    raise AcceptanceFailure(f"literature run did not become terminal: {last}")


def verify_outputs(project_id: str) -> dict[str, str]:
    encoded = urllib.parse.quote(project_id, safe="")
    status, body, _ = request("GET", f"/api/v1/projects/{encoded}/sources")
    sources = expect(status, body, {200}, "reading sources")
    if sources.get("integrityStatus") != "verified" or not sources.get("sources"):
        raise AcceptanceFailure("verified literature sources are missing")
    status, body, _ = request("GET", f"/api/v1/projects/{encoded}/review")
    review = expect(status, body, {200}, "reading literature review")
    if review.get("integrityStatus") != "verified" or not str(review.get("markdown") or "").strip():
        raise AcceptanceFailure("verified literature review is missing")
    digests: dict[str, str] = {}
    for name in sorted(ARTIFACTS):
        status, content, headers = request(
            "GET", f"/api/v1/projects/{encoded}/artifacts/{urllib.parse.quote(name, safe='')}"
        )
        if status != 200 or not isinstance(content, bytes) or not content:
            raise AcceptanceFailure(f"artifact download failed: {name}")
        digest = hashlib.sha256(content).hexdigest()
        if headers.get("X-Content-SHA256") != digest:
            raise AcceptanceFailure(f"artifact response hash disagreed: {name}")
        digests[name] = digest
    return digests


def initial(state_path: Path) -> None:
    username = unlock()
    collection_id = prepare_collection()
    suffix = uuid.uuid4().hex
    status, body, _ = request(
        "POST",
        "/api/v1/projects",
        {
            "title": os.environ.get("AI_RESEARCH_ACCEPTANCE_TITLE", "Agent 评测可复现性文献研究"),
            "researchQuestion": os.environ.get(
                "AI_RESEARCH_ACCEPTANCE_QUESTION",
                "公开文献中，哪些方法用于提高大语言模型 Agent 评测的可复现性？",
            ),
            "idempotencyKey": f"acceptance:project:{suffix}",
        },
    )
    project = expect(status, body, {200, 201}, "creating research project")
    project_id = str(project.get("projectId") or "")
    status, body, _ = request(
        "POST",
        f"/api/v1/projects/{urllib.parse.quote(project_id, safe='')}/literature/runs",
        {"idempotencyKey": f"acceptance:literature:{suffix}", "collectionId": collection_id},
    )
    expect(status, body, {200, 201}, "starting literature research")
    terminal = wait_for_terminal(project_id)
    if terminal.get("literatureOutcome") != "completed":
        raise AcceptanceFailure(f"literature research did not complete: {terminal}")
    digests = verify_outputs(project_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "projectId": project_id,
                "collectionId": collection_id,
                "username": username,
                "artifactSha256": digests,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def recovery(state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    unlock()
    project_id = str(state["projectId"])
    terminal = wait_for_terminal(project_id, timeout=60)
    if terminal.get("literatureOutcome") != "completed" or terminal.get("collectionId") != state["collectionId"]:
        raise AcceptanceFailure("project state did not recover after restart")
    if verify_outputs(project_id) != state["artifactSha256"]:
        raise AcceptanceFailure("artifact hashes changed after restart")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("initial", "recovery"))
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "initial":
        initial(args.state)
    else:
        recovery(args.state)
    print(f"literature acceptance {args.phase} passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceFailure, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"literature acceptance failed: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
