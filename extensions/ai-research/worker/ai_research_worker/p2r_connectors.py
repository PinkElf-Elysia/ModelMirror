from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .p2r_phase_contracts import P2RPhaseContractError, verify_reuse_root


PROTOCOL = "modelmirror-ai-research-p2r-connectors-v3"
P2R_INPUT_PROTOCOL = "modelmirror-ai-research-p2r-input-v2"
QUALIFICATION_RUN_ID_PATTERN = re.compile(r"^p2rq_[0-9a-f]{32}$")
MAX_P2R_QUALIFICATION_AGE_SECONDS = 6 * 60 * 60
MAX_P2R_CLOCK_SKEW_SECONDS = 60
RESEARCHSTUDIO_COMMIT = "a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a"
PYTHON_VERSION = "3.12.13"
BASE_IMAGE = (
    "python@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461"
)
FIXED_QUERY = "long-horizon language model agent evaluation reproducibility"
QUALIFICATION_AS_OF = datetime(2026, 8, 29, tzinfo=timezone.utc)
REQUIREMENTS_LOCK_SHA256 = (
    "3294cf73e6cadb018775c64e11b886187dfed455501c828c7203ddca976a491b"
)
SCRIPT_HASHES = {
    "scripts/search_arxiv.py": "8c01c501932a411ead2e3f8ca113b8f3efd121b3aa31a4331b91df1ad5a5d999",
    "scripts/search_openalex.py": "c2b91318d7d80dcd5dbfc9506c27fdd59a0070adc29729d07573c0e06c77bb5e",
    "scripts/search_semanticscholar.py": "218d4d175b07e4ffa35f2b202be217610529a379206b3a6d6df4aa56657613d2",
    "scripts/search_openreview.py": "3d892624cc0f28db1551f1c06a2ac982c41796ee7cfd11d835583f958be90943",
    "scripts/run.py": "0aec39ecb1cf0d3aa0d85694ac1da0f6ba2c39a55d96effa8cb21ca55aaff1b8",
}
PACKAGE_VERSIONS = {
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "Deprecated": "1.3.1",
    "editdistance": "0.8.1",
    "feedparser": "6.0.14",
    "feedparser-sgmllib": "2.1.0",
    "future": "1.0.0",
    "idna": "3.19",
    "openreview-py": "2.5.1",
    "pycryptodome": "3.23.0",
    "PyJWT": "2.13.0",
    "pylatexenc": "2.11",
    "requests": "2.34.2",
    "tld": "0.13.2",
    "tqdm": "4.70.0",
    "urllib3": "2.7.0",
    "wrapt": "2.3.0",
}
CONNECTOR_ORDER = ("arxiv", "openalex", "semanticscholar", "openreview")
CONNECTOR_SCRIPT_PATHS = {
    "arxiv": "scripts/search_arxiv.py",
    "openalex": "scripts/search_openalex.py",
    "semanticscholar": "scripts/search_semanticscholar.py",
    "openreview": "scripts/search_openreview.py",
}
SEMANTICSCHOLAR_MAX_PROBE_ATTEMPTS = 2
SEMANTICSCHOLAR_DEFAULT_RETRY_SECONDS = 60
SEMANTICSCHOLAR_MIN_RETRY_SECONDS = 5
SEMANTICSCHOLAR_MAX_RETRY_SECONDS = 120
RETRY_POLICY = {
    "semanticscholar": {
        "maxProbeAttempts": SEMANTICSCHOLAR_MAX_PROBE_ATTEMPTS,
        "retryableHttpStatus": [429],
        "retryAfterSeconds": {
            "default": SEMANTICSCHOLAR_DEFAULT_RETRY_SECONDS,
            "min": SEMANTICSCHOLAR_MIN_RETRY_SECONDS,
            "max": SEMANTICSCHOLAR_MAX_RETRY_SECONDS,
            "aboveMax": "do_not_retry",
        },
    }
}


class P2RConnectorError(RuntimeError):
    pass


class P2RConnectorProbeError(P2RConnectorError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _durable_write(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _utc_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise P2RConnectorError(f"P2R input {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P2RConnectorError(f"P2R input {field} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise P2RConnectorError(f"P2R input {field} is not UTC")
    return parsed


def _input_binding(output_parent: Path) -> tuple[bytes, dict[str, object]]:
    input_path = output_parent / "p2r-input-receipt.json"
    if input_path.is_symlink() or not input_path.is_file():
        raise P2RConnectorError("P2R input receipt is missing or unsafe")
    try:
        input_path.resolve(strict=True).relative_to(output_parent)
    except ValueError as exc:
        raise P2RConnectorError("P2R input receipt escapes the fresh run") from exc
    data = input_path.read_bytes()
    if not data or len(data) > 64 * 1024:
        raise P2RConnectorError("P2R input receipt is empty or oversized")
    try:
        receipt = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P2RConnectorError("P2R input receipt is malformed") from exc
    qualification_run_id = receipt.get("qualificationRunId") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("protocol") != P2R_INPUT_PROTOCOL
        or receipt.get("status") != "verified"
        or not isinstance(qualification_run_id, str)
        or QUALIFICATION_RUN_ID_PATTERN.fullmatch(qualification_run_id) is None
    ):
        raise P2RConnectorError("P2R input receipt identity is invalid")
    issued_at = _utc_datetime(receipt.get("issuedAt"), field="issuedAt")
    now = datetime.now(timezone.utc)
    if (
        issued_at > now + timedelta(seconds=MAX_P2R_CLOCK_SKEW_SECONDS)
        or (now - issued_at).total_seconds() > MAX_P2R_QUALIFICATION_AGE_SECONDS
    ):
        raise P2RConnectorError("P2R input receipt is not fresh")
    return data, receipt


def _locked_roots(
    requirements_lock: Path, skill_root: Path
) -> tuple[Path, Path, dict[str, object]]:
    if requirements_lock.is_symlink() or skill_root.is_symlink():
        raise P2RConnectorError("qualification inputs must not be symbolic links")
    requirements_lock = requirements_lock.resolve(strict=True)
    skill_root = skill_root.resolve(strict=True)
    if not requirements_lock.is_file():
        raise P2RConnectorError("connector requirements lock is missing or unsafe")
    if _sha256(requirements_lock.read_bytes()) != REQUIREMENTS_LOCK_SHA256:
        raise P2RConnectorError("connector requirements lock hash does not match")
    try:
        reuse_root = verify_reuse_root(skill_root)
    except P2RPhaseContractError as exc:
        raise P2RConnectorError(
            "locked ResearchStudio reuse-root identity differs"
        ) from exc
    for name, digest in SCRIPT_HASHES.items():
        path = skill_root / name
        if path.is_symlink() or not path.is_file():
            raise P2RConnectorError(f"locked ResearchStudio source is missing: {name}")
        try:
            path.resolve(strict=True).relative_to(skill_root)
        except ValueError as exc:
            raise P2RConnectorError(f"locked ResearchStudio source escapes root: {name}") from exc
        if _sha256(path.read_bytes()) != digest:
            raise P2RConnectorError(f"locked ResearchStudio source hash differs: {name}")
    return requirements_lock, skill_root, reuse_root


def _validate_packages() -> None:
    if sys.version.split()[0] != PYTHON_VERSION:
        raise P2RConnectorError(f"connector qualification requires Python {PYTHON_VERSION}")
    for name, version in PACKAGE_VERSIONS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise P2RConnectorError(f"locked connector package is missing: {name}") from exc
        if actual != version:
            raise P2RConnectorError(f"locked connector package version differs: {name}")


def _load_connectors(skill_root: Path) -> dict[str, Any]:
    modules: dict[str, Any] = {}
    for name, relative_path in CONNECTOR_SCRIPT_PATHS.items():
        module_path = (skill_root / relative_path).resolve(strict=True)
        try:
            module_path.relative_to(skill_root)
        except ValueError as exc:
            raise P2RConnectorError(
                f"connector module resolved outside locked source: {name}"
            ) from exc
        spec = importlib.util.spec_from_file_location(
            f"_modelmirror_p2r_upstream_{name}", module_path
        )
        if spec is None or spec.loader is None:
            raise P2RConnectorError(f"connector module cannot be loaded: {name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    for name, module in modules.items():
        module_file = Path(module.__file__).resolve(strict=True)
        try:
            module_file.relative_to(skill_root)
        except ValueError as exc:
            raise P2RConnectorError(f"connector module resolved outside locked source: {name}") from exc
    if (
        modules["arxiv"].ARXIV_API != "https://export.arxiv.org/api/query"
        or modules["openalex"].API != "https://api.openalex.org/works"
        or modules["semanticscholar"].API
        != "https://api.semanticscholar.org/graph/v1/paper/search"
    ):
        raise P2RConnectorError("a connector endpoint differs from the locked profile")
    return modules


def _public_hits(name: str, hits: object, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(hits, list) or (not hits and not allow_empty) or len(hits) > 10:
        raise P2RConnectorError(f"{name} returned an invalid bounded hit list")
    normalized: list[dict[str, Any]] = []
    for index, hit in enumerate(hits):
        if not isinstance(hit, dict) or hit.get("source") != name:
            raise P2RConnectorError(f"{name} hit {index} has the wrong provenance")
        title = hit.get("title")
        url = hit.get("paper_url")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            raise P2RConnectorError(f"{name} hit {index} is missing title or URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise P2RConnectorError(f"{name} hit {index} has a non-HTTPS URL")
        normalized.append(hit)
    return normalized


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(chain) < 8 and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _bounded_http_status(exc: BaseException) -> int | None:
    for node in _exception_chain(exc):
        candidates: list[object] = []
        if isinstance(node, urllib.error.HTTPError):
            candidates.append(node.code)
        response = getattr(node, "response", None)
        if response is not None:
            candidates.extend(
                [getattr(response, "status_code", None), getattr(response, "status", None)]
            )
        if node.args and isinstance(node.args[0], dict):
            payload = node.args[0]
            candidates.extend(
                [payload.get("status"), payload.get("statusCode"), payload.get("status_code")]
            )
        for value in candidates:
            if not isinstance(value, bool) and isinstance(value, int) and 100 <= value <= 599:
                return value
    return None


def _safe_upstream_error_name(exc: BaseException) -> str | None:
    for node in _exception_chain(exc):
        if not node.args or not isinstance(node.args[0], dict):
            continue
        name = node.args[0].get("name")
        if (
            isinstance(name, str)
            and 1 <= len(name) <= 64
            and all(character.isalnum() or character in "._-" for character in name)
        ):
            return name
    return None


def _error_category(exc: BaseException, status: int | None) -> str:
    if status in {401, 403}:
        return "authentication_or_authorization"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "upstream_server"
    for node in _exception_chain(exc):
        if type(node).__name__ == "MfaRequiredException":
            return "mfa_required"
        payload = node.args[0] if node.args and isinstance(node.args[0], dict) else None
        if payload is None:
            continue
        name = payload.get("name")
        message = payload.get("message")
        text = " ".join(value for value in (name, message) if isinstance(value, str)).lower()
        if "mfa" in text or "multi-factor" in text:
            return "mfa_required"
        if any(
            marker in text
            for marker in (
                "invalid username",
                "invalid password",
                "invalid credential",
                "authentication",
                "unauthorized",
                "forbidden",
            )
        ):
            return "authentication_or_authorization"
        if "rate" in text and "limit" in text:
            return "rate_limited"
    return "upstream_error"


def _error_fact(exc: BaseException) -> dict[str, object]:
    fact: dict[str, object] = {"type": type(exc).__name__}
    chain = _exception_chain(exc)
    if len(chain) > 1:
        fact["upstreamType"] = type(chain[-1]).__name__
    stage = getattr(exc, "stage", None)
    if isinstance(stage, str):
        fact["stage"] = stage
    status = _bounded_http_status(exc)
    if status is not None:
        fact["httpStatus"] = status
    upstream_name = _safe_upstream_error_name(exc)
    if upstream_name is not None:
        fact["upstreamErrorName"] = upstream_name
    fact["category"] = _error_category(exc, status)
    return fact


def _retry_after_seconds(exc: BaseException) -> int | None:
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        raise P2RConnectorError("retry delay requested for a non-retryable error")
    value = exc.headers.get("Retry-After") if exc.headers is not None else None
    if isinstance(value, str) and value.strip().isdigit():
        seconds = int(value.strip())
        if seconds > SEMANTICSCHOLAR_MAX_RETRY_SECONDS:
            return None
        return max(SEMANTICSCHOLAR_MIN_RETRY_SECONDS, seconds)
    return SEMANTICSCHOLAR_DEFAULT_RETRY_SECONDS


def _probe_arxiv(module: Any) -> tuple[list[dict[str, Any]], dict[str, object]]:
    hits = _public_hits("arxiv", module.search(FIXED_QUERY, max_results=5))
    return hits, {"status": "ready", "hitCount": len(hits), "authMode": "anonymous"}


def _probe_openalex(module: Any) -> tuple[list[dict[str, Any]], dict[str, object]]:
    hits = _public_hits(
        "openalex",
        module.search(
            FIXED_QUERY,
            "2025-01-01",
            until_date="2026-08-29",
            published_only=False,
            max_results=5,
        ),
    )
    return hits, {
        "status": "ready",
        "hitCount": len(hits),
        "authMode": "api_key" if os.environ.get("OPENALEX_API_KEY") else "anonymous",
    }


def _probe_semanticscholar(module: Any) -> tuple[list[dict[str, Any]], dict[str, object]]:
    hits = _public_hits(
        "semanticscholar",
        module.search(FIXED_QUERY, 2025, until_year=2026, max_results=5),
    )
    return hits, {
        "status": "ready",
        "hitCount": len(hits),
        "authMode": (
            "api_key" if os.environ.get("SEMANTICSCHOLAR_API_KEY") else "anonymous"
        ),
    }


def _probe_openreview(module: Any) -> tuple[list[dict[str, Any]], dict[str, object]]:
    if not os.environ.get("OPENREVIEW_USER") or not os.environ.get("OPENREVIEW_PASS"):
        raise P2RConnectorError("OpenReview credentials are not configured")
    try:
        client = module.get_client()
    except Exception as exc:
        raise P2RConnectorProbeError(
            "client_construction_login", "OpenReview client login failed"
        ) from exc
    venues = module.derive_active_venues(QUALIFICATION_AS_OF)
    since = QUALIFICATION_AS_OF - timedelta(days=30 * 6)
    successful_venues: list[str] = []
    last_venue_error: BaseException | None = None
    for venue in venues:
        try:
            client.get_notes(
                invitation=f"{venue}/-/Submission",
                limit=1,
                sort="cdate:desc",
                mintcdate=int(since.timestamp() * 1000),
            )
        except Exception as exc:
            last_venue_error = exc
            continue
        successful_venues.append(venue)
    if not successful_venues:
        error = P2RConnectorProbeError(
            "venue_preflight", "OpenReview authenticated probe failed for every locked venue"
        )
        if last_venue_error is not None:
            raise error from last_venue_error
        raise error
    try:
        hits = _public_hits(
            "openreview",
            module.search(
                client,
                FIXED_QUERY,
                since,
                successful_venues,
                max_results=5,
                per_venue_cap=50,
                until=QUALIFICATION_AS_OF,
            ),
        )
    except Exception as exc:
        raise P2RConnectorProbeError(
            "search", "OpenReview fixed qualification search failed"
        ) from exc
    return hits, {
        "status": "ready",
        "hitCount": len(hits),
        "authMode": "credentials_present",
        "successfulVenueCount": len(successful_venues),
    }


def qualify_connectors(
    *, requirements_lock: Path, skill_root: Path, output_parent: Path
) -> tuple[Path, bool]:
    _, skill_root, reuse_root = _locked_roots(requirements_lock, skill_root)
    _validate_packages()
    if output_parent.is_symlink() or not output_parent.is_dir():
        raise P2RConnectorError("connector qualification output parent is missing or unsafe")
    output_parent = output_parent.resolve(strict=True)
    final = output_parent / "connector-qualification"
    if final.exists() or final.is_symlink():
        raise P2RConnectorError("connector qualification evidence is immutable")
    if {item.name for item in output_parent.iterdir()} != {"p2r-input-receipt.json"}:
        raise P2RConnectorError("connector qualification requires one fresh P2R input receipt")
    input_receipt_bytes, input_receipt = _input_binding(output_parent)
    staging = output_parent / f".connector-qualification.staging-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
        )
        modules = _load_connectors(skill_root)
        probes = {
            "arxiv": _probe_arxiv,
            "openalex": _probe_openalex,
            "semanticscholar": _probe_semanticscholar,
            "openreview": _probe_openreview,
        }
        connector_facts: dict[str, dict[str, object]] = {}
        artifacts: dict[str, dict[str, object]] = {}
        for name in CONNECTOR_ORDER:
            probe_attempts: list[dict[str, object]] = []
            max_attempts = (
                SEMANTICSCHOLAR_MAX_PROBE_ATTEMPTS
                if name == "semanticscholar"
                else 1
            )
            attempt_count = 0
            while attempt_count < max_attempts:
                attempt_count += 1
                try:
                    hits, fact = probes[name](modules[name])
                    probe_attempts.append(
                        {"sequence": attempt_count, "outcome": "ready"}
                    )
                    fact = {
                        **fact,
                        "probeAttempts": probe_attempts,
                    }
                    break
                except Exception as exc:
                    error = _error_fact(exc)
                    retry_delay = (
                        _retry_after_seconds(exc)
                        if name == "semanticscholar"
                        and isinstance(exc, urllib.error.HTTPError)
                        and exc.code == 429
                        and attempt_count < max_attempts
                        else None
                    )
                    if (
                        retry_delay is not None
                    ):
                        probe_attempts.append(
                            {
                                "sequence": attempt_count,
                                "outcome": "failed",
                                "error": error,
                                "backoffSeconds": retry_delay,
                            }
                        )
                        time.sleep(retry_delay)
                        continue
                    probe_attempts.append(
                        {
                            "sequence": attempt_count,
                            "outcome": "failed",
                            "error": error,
                        }
                    )
                    hits = []
                    fact = {
                        "status": "failed",
                        "error": error,
                        "probeAttempts": probe_attempts,
                    }
                    break
            hit_bytes = _json_bytes(hits)
            filename = f"{name}-hits.json"
            _durable_write(staging / filename, hit_bytes)
            artifacts[filename] = {
                "sha256": _sha256(hit_bytes),
                "sizeBytes": len(hit_bytes),
            }
            connector_facts[name] = {**fact, "artifact": filename}

        ready = all(
            connector_facts[name].get("status") == "ready" for name in CONNECTOR_ORDER
        )
        receipt = {
            "protocol": PROTOCOL,
            "status": "ready" if ready else "degraded",
            "degraded": not ready,
            "qualificationRunId": input_receipt["qualificationRunId"],
            "p2rInputReceiptSha256": _sha256(input_receipt_bytes),
            "inputIssuedAt": input_receipt["issuedAt"],
            "qualifiedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "query": FIXED_QUERY,
            "asOf": QUALIFICATION_AS_OF.isoformat().replace("+00:00", "Z"),
            "researchStudioCommit": RESEARCHSTUDIO_COMMIT,
            "researchStudioReuseRoot": reuse_root,
            "pythonVersion": PYTHON_VERSION,
            "baseImage": BASE_IMAGE,
            "retryPolicy": RETRY_POLICY,
            "qualifierSha256": _sha256(Path(__file__).read_bytes()),
            "requirementsLockSha256": REQUIREMENTS_LOCK_SHA256,
            "scriptSha256": SCRIPT_HASHES,
            "packageVersions": PACKAGE_VERSIONS,
            "connectors": connector_facts,
            "artifacts": artifacts,
            "claimLevel": "qualification_only",
        }
        _durable_write(staging / "connector-receipt.json", _json_bytes(receipt))
        os.replace(staging, final)
        return final, ready
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify the fixed P2R connector profile")
    parser.add_argument("--requirements-lock", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        _, _, reuse_root = _locked_roots(args.requirements_lock, args.skill_root)
        print(json.dumps({"status": "verified", **reuse_root}, separators=(",", ":")))
        return 0
    if args.output_parent is None:
        parser.error("--output-parent is required unless --verify-only is used")
    output, ready = qualify_connectors(
        requirements_lock=args.requirements_lock,
        skill_root=args.skill_root,
        output_parent=args.output_parent,
    )
    print(
        json.dumps(
            {"status": "ready" if ready else "degraded", "path": str(output)},
            separators=(",", ":"),
        )
    )
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
