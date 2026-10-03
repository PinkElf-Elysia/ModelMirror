"""Read-only, dependency-free source coverage guard. Never imports application code.

This is a change detector, not a security sandbox or a runtime readiness check.
Discovery is deliberately conservative: an HTTP candidate is not necessarily a
model request. Every candidate needs an explicit reviewed classification.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

MANIFEST = "docs/audits/provider-coverage.json"
STATES = {"managed_integrated", "migration_pending", "external_domain", "excluded"}
KINDS = {"model_dispatch", "model_delegate", "transport", "read_only_probe", "non_model_http", "external_domain"}
METHODS = {"post", "request", "stream", "send", "urlopen", "urlretrieve"}
NETWORK = {"httpx", "requests", "aiohttp", "openai", "urllib.request", "http.client", "socket", "websockets"}
HELPERS = {"collect_chat_completion_text", "stream_chat_text", "_run_workflow_response", "get_llm_gateway_config",
           "request_provider_url", "send_authorized", "send_authorized_stream", "send_stream", "_requester"}
ROOTS = ("server/", "client/src/", "extensions/", "experiments/")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}
SKIP_PARTS = {"tests", "__tests__", "fixtures", "node_modules", "vendor", "dist", "client_dist", ".git"}
JS_NETWORK = re.compile(r"\b(?:fetch\w*|XMLHttpRequest|WebSocket|EventSource|axios|undici)\b|[\"'](?:node:)?https?[\"']")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func) + "()"
    return "?"


def python_sites(source: str, path: str) -> list[dict]:
    tree = ast.parse(source, filename=path)
    source_hash = digest(ast.dump(tree, include_attributes=False))
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    # Track bound-method aliases conservatively across the module. False
    # positives require classification; an alias must not hide a new send.
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, (ast.Name, ast.Attribute)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = dotted(node.value)
                head, _, tail = value.partition(".")
                value = aliases.get(head, head) + ("." + tail if tail else "")
                for target in targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = value
    sites = []
    counts = Counter()

    def add(node: ast.AST, scope: str, kind: str, callee: str):
        fingerprint = digest(ast.dump(node, include_attributes=False))
        key = f"{path}::{scope}::{kind}:{callee}:{fingerprint}"
        counts[key] += 1
        sites.append({"id": f"{key}:{counts[key]}", "path": path, "symbol": scope,
                      "line": node.lineno, "detector": kind, "callee": callee,
                      "source_sha256": source_hash})

    def visit(node: ast.AST, scope: str):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = node.name if scope == "<module>" else scope + "." + node.name
            # Route decorators are inbound endpoints, not outbound calls.
            for child in node.body:
                visit(child, scope)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node.args, scope)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [i.name for i in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(any(n == p or n.startswith(p + ".") for p in NETWORK) for n in names):
                add(node, scope, "network_import", "import")
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            head, _, tail = name.partition(".")
            resolved = aliases.get(head, head) + ("." + tail if tail else "")
            leaf = resolved.rsplit(".", 1)[-1]
            if leaf in HELPERS or resolved.rsplit(".", 1)[-1] in HELPERS:
                add(node, scope, "delegate", name)
            elif leaf in METHODS or (any(resolved.startswith(p + ".") for p in NETWORK)
                                     and leaf in {"AsyncClient", "Client", "Session", "ClientSession", "OpenAI", "AsyncOpenAI", "connect", "get", "put", "patch", "delete"}):
                # Includes generic tool HTTP and even ambiguous send() calls;
                # the manifest, not this heuristic, assigns semantic meaning.
                add(node, scope, "network_candidate", name)
            elif leaf == "get" and (head in {"client", "request_client", "http_client", "session"}
                                    or name.startswith(("self._client.", "self.client."))):
                add(node, scope, "network_candidate", name)
            elif leaf == "create" and any(s in name for s in ("completions.", "responses.", "embeddings.", "messages.")):
                add(node, scope, "network_candidate", name)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, "<module>")
    return sites


def is_source(path: str) -> bool:
    p = Path(path)
    return (path.startswith(ROOTS) and p.suffix in SUFFIXES
            and not set(p.parts).intersection(SKIP_PARTS)
            and not p.name.startswith("test_")
            and not any(x in p.name for x in (".test.", ".spec.")))


def source_paths(root: Path) -> list[str]:
    # Include untracked additions, without reading ignored runtime data or secrets.
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=root, capture_output=True, check=True)
    return sorted({p for p in result.stdout.decode("utf-8").split("\0") if p and is_source(p)})


def scan(root: Path) -> list[dict]:
    sites = []
    for path in source_paths(root):
        target = root / path
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"source link requires review: {path}")
        source = target.read_text(encoding="utf-8-sig")
        if target.suffix == ".py":
            sites.extend(python_sites(source, path))
        elif JS_NETWORK.search(source):
            # No ad-hoc JS parser: review the exact module bytes. This can flag
            # comments/UI text but cannot silently grant a whole-file exception.
            sites.append({"id": f"{path}::<module>::script_network:{digest(source)}:1",
                          "path": path, "symbol": "<module>", "line": 1,
                          "detector": "script_network", "callee": "module review",
                          "source_sha256": digest(source)})
    return sites


def literal(node: ast.AST):
    if isinstance(node, ast.Call) and dotted(node.func) == "frozenset" and len(node.args) == 1:
        return literal(node.args[0])
    if isinstance(node, ast.Dict):
        return {literal(k): literal(v) for k, v in zip(node.keys, node.values)}
    return ast.literal_eval(node)


def registry(root: Path) -> dict:
    path = root / "server/model_router/workload_control.py"
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"ENTRY_FEATURE_FLAGS", "ENTRY_ALLOWED_SHAPES", "DATA_PLANE_INTEGRATED_ENTRIES"}:
                values[node.target.id] = literal(node.value)
    flags = values["ENTRY_FEATURE_FLAGS"]
    shapes = values["ENTRY_ALLOWED_SHAPES"]
    integrated = values["DATA_PLANE_INTEGRATED_ENTRIES"]
    if flags.keys() != shapes.keys() or not integrated <= flags.keys():
        raise ValueError("workload registry disagreement")
    return {entry: {"flags": list(flags[entry]), "shapes": sorted(shapes[entry]),
                    "status": "managed_integrated" if entry in integrated else "migration_pending"}
            for entry in sorted(flags)}


def validate(root: Path, manifest: dict, sites: list[dict]) -> list[str]:
    errors = []
    if manifest.get("contract_version") != "modelmirror-provider-coverage-v1":
        errors.append("invalid manifest contract")
    entries = manifest.get("entries", [])
    by_entry = {e.get("id"): e for e in entries}
    if len(entries) != len(by_entry):
        errors.append("duplicate entry ID")
    for e in entries:
        if e.get("status") not in STATES:
            errors.append(f"invalid entry status: {e.get('id')}")
        for field in ("source", "shapes", "adapters", "configuration", "dispatch", "logical_key",
                      "receipt", "usage", "recovery", "evidence", "owner", "reason", "policy"):
            if not e.get(field):
                errors.append(f"missing {field}: {e.get('id')}")
        for path in e.get("evidence", []):
            if (not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts
                    or not (root / path).is_file()):
                errors.append(f"missing evidence: {e.get('id')}")
    expected_registry = registry(root)
    actual_registry = {e["id"]: {k: e.get(k) for k in ("flags", "shapes", "status")}
                       for e in entries if e.get("registry") == "workload"}
    if actual_registry != expected_registry:
        errors.append("workload registry drift (flags/shapes/integration status)")
    records = manifest.get("sites", [])
    registered = {r.get("id"): r for r in records}
    if len(registered) != len(records):
        errors.append("duplicate site ID")
    observed = {s["id"]: s for s in sites}
    for key in sorted(observed.keys() - registered.keys()):
        s = observed[key]
        errors.append(f"unreviewed site: {s['path']}:{s['line']} {s['symbol']} {s['callee']}")
    for key in sorted(registered.keys() - observed.keys()):
        errors.append(f"stale site: {key}")
    for r in records:
        if r.get("kind") not in KINDS or not r.get("reason") or not r.get("owner"):
            errors.append(f"unclassified site: {r.get('id')}")
        if not r.get("entries") or any(e not in by_entry for e in r["entries"]):
            errors.append(f"invalid site entry references: {r.get('id')}")
    # A non-model POST must not silently become model traffic just because its
    # unchanged call still references `url`. Pin the containing module as well.
    modules = {s["path"]: s["source_sha256"] for s in sites}
    if manifest.get("source_modules") != modules:
        errors.append("source module drift: re-review constants, delegates and exceptions")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--discover", action="store_true", help="print candidates; does not approve or write them")
    args = parser.parse_args()
    try:
        sites = scan(args.root)
        if args.discover:
            print(json.dumps({"workloads": registry(args.root), "sites": sites}, indent=2))
            return 0
        manifest = json.loads((args.root / MANIFEST).read_text(encoding="utf-8"))
        errors = validate(args.root, manifest, sites)
        if errors:
            print("\n".join(errors))
            return 1
        print(f"Provider coverage: {len(manifest['entries'])} entries, {len(sites)} registered candidates; source coverage only.")
        return 0
    except (OSError, ValueError, KeyError, SyntaxError, subprocess.CalledProcessError) as exc:
        print(f"Provider coverage check failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
