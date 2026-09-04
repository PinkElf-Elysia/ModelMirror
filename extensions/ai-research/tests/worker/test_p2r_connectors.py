from __future__ import annotations

import hashlib
import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_research_worker import p2r_connectors as connectors
from ai_research_worker import p2r_phase_contracts as phase_contracts


def _hit(source: str, *, url: str | None = None) -> dict[str, object]:
    return {
        "title": f"{source} result",
        "paper_url": url or f"https://example.org/{source}",
        "source": source,
    }


def _ready_probe(source: str):
    def probe(_module: object):
        fact: dict[str, object] = {
            "status": "ready",
            "hitCount": 1,
            "authMode": "credentials_present" if source == "openreview" else "anonymous",
        }
        if source == "openreview":
            fact["successfulVenueCount"] = 1
        return [_hit(source)], fact

    return probe


def _write_input_receipt(output_parent: Path) -> bytes:
    data = (
        json.dumps(
            {
                "protocol": connectors.P2R_INPUT_PROTOCOL,
                "status": "verified",
                "qualificationRunId": "p2rq_" + "a" * 32,
                "issuedAt": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    (output_parent / "p2r-input-receipt.json").write_bytes(data)
    return data


def _lock_synthetic_reuse_root(
    monkeypatch: pytest.MonkeyPatch, skill_root: Path
) -> dict[str, object]:
    files = sorted(
        (path for path in skill_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(skill_root).as_posix().casefold(),
    )
    pairs = bytearray()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(skill_root).as_posix()
        data = path.read_bytes()
        total_bytes += len(data)
        pairs.extend(relative.encode("utf-8"))
        pairs.extend(b"\0")
        pairs.extend(hashlib.sha256(data).hexdigest().encode("ascii"))
        pairs.extend(b"\n")
    facts = {
        "fileCount": len(files),
        "totalBytes": total_bytes,
        "aggregateSha256": hashlib.sha256(bytes(pairs)).hexdigest(),
    }
    monkeypatch.setattr(
        phase_contracts, "RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT", facts["fileCount"]
    )
    monkeypatch.setattr(
        phase_contracts, "RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES", facts["totalBytes"]
    )
    monkeypatch.setattr(
        phase_contracts,
        "RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256",
        facts["aggregateSha256"],
    )
    return facts


@pytest.mark.parametrize(
    ("hits", "match"),
    [
        ([_hit("arxiv", url="http://example.org/paper")], "non-HTTPS"),
        ([_hit("openalex")], "wrong provenance"),
        ([_hit("arxiv")] * 11, "bounded hit list"),
        ([{"source": "arxiv", "paper_url": "https://example.org"}], "missing title"),
    ],
)
def test_public_hit_gate_fails_closed(hits: list[dict[str, object]], match: str) -> None:
    with pytest.raises(connectors.P2RConnectorError, match=match):
        connectors._public_hits("arxiv", hits)


def test_openreview_probe_requires_credentials_without_contacting_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENREVIEW_USER", raising=False)
    monkeypatch.delenv("OPENREVIEW_PASS", raising=False)
    module = SimpleNamespace(get_client=lambda: pytest.fail("must not construct client"))
    with pytest.raises(connectors.P2RConnectorError, match="credentials"):
        connectors._probe_openreview(module)


def test_openreview_probe_requires_a_successful_authenticated_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENREVIEW_USER", "configured")
    monkeypatch.setenv("OPENREVIEW_PASS", "configured")

    class RejectingClient:
        def get_notes(self, **_kwargs: object) -> None:
            raise RuntimeError("rejected")

    module = SimpleNamespace(
        get_client=lambda: RejectingClient(),
        derive_active_venues=lambda _now: ["ICLR.cc/2027/Conference"],
    )
    with pytest.raises(connectors.P2RConnectorProbeError, match="every locked venue"):
        connectors._probe_openreview(module)


def test_openreview_probe_rejects_empty_fixed_query_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENREVIEW_USER", "configured")
    monkeypatch.setenv("OPENREVIEW_PASS", "configured")

    class Client:
        def get_notes(self, **_kwargs: object) -> list[object]:
            return []

    module = SimpleNamespace(
        get_client=lambda: Client(),
        derive_active_venues=lambda _now: ["ICLR.cc/2027/Conference"],
        search=lambda *_args, **_kwargs: [],
    )
    with pytest.raises(connectors.P2RConnectorProbeError, match="search failed"):
        connectors._probe_openreview(module)


def test_error_fact_extracts_only_bounded_nonsecret_openreview_diagnostics() -> None:
    secret = "credential-that-must-not-be-written"
    upstream = RuntimeError("body must remain private")
    upstream.response = SimpleNamespace(status_code=401)  # type: ignore[attr-defined]
    openreview_error = Exception({"name": "ForbiddenError", "message": secret})
    openreview_error.__cause__ = upstream
    wrapped = connectors.P2RConnectorProbeError(
        "client_construction_login", "OpenReview client login failed"
    )
    wrapped.__cause__ = openreview_error

    fact = connectors._error_fact(wrapped)

    assert fact == {
        "type": "P2RConnectorProbeError",
        "upstreamType": "RuntimeError",
        "stage": "client_construction_login",
        "httpStatus": 401,
        "upstreamErrorName": "ForbiddenError",
        "category": "authentication_or_authorization",
    }
    assert secret not in json.dumps(fact)


def test_locked_roots_recompute_source_and_requirements_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    lock_path = tmp_path / "connector.lock"
    lock_path.write_bytes(b"locked requirements")
    script_path = skill_root / "scripts" / "search.py"
    script_path.parent.mkdir()
    script_path.write_bytes(b"locked source")
    expected_reuse_root = _lock_synthetic_reuse_root(monkeypatch, skill_root)
    monkeypatch.setattr(
        connectors,
        "REQUIREMENTS_LOCK_SHA256",
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        connectors,
        "SCRIPT_HASHES",
        {"scripts/search.py": hashlib.sha256(script_path.read_bytes()).hexdigest()},
    )

    assert connectors._locked_roots(lock_path, skill_root) == (
        lock_path,
        skill_root,
        expected_reuse_root,
    )
    script_path.write_bytes(b"tampered source")
    _lock_synthetic_reuse_root(monkeypatch, skill_root)
    with pytest.raises(connectors.P2RConnectorError, match="source hash differs"):
        connectors._locked_roots(lock_path, skill_root)


@pytest.mark.parametrize("extra_name", ["scripts/__init__.py", "scripts/unlocked_helper.py"])
def test_locked_roots_reject_extra_package_or_dependency_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_name: str,
) -> None:
    skill_root = tmp_path / "skill"
    script_path = skill_root / "scripts" / "search.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_bytes(b"locked source")
    support_path = skill_root / "prompts" / "locked.md"
    support_path.parent.mkdir()
    support_path.write_bytes(b"locked support")
    lock_path = tmp_path / "connector.lock"
    lock_path.write_bytes(b"locked requirements")
    _lock_synthetic_reuse_root(monkeypatch, skill_root)
    monkeypatch.setattr(
        connectors,
        "REQUIREMENTS_LOCK_SHA256",
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        connectors,
        "SCRIPT_HASHES",
        {"scripts/search.py": hashlib.sha256(script_path.read_bytes()).hexdigest()},
    )

    extra_path = skill_root / extra_name
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_bytes(b"unlocked executable content")
    with pytest.raises(connectors.P2RConnectorError, match="reuse-root identity differs"):
        connectors._locked_roots(lock_path, skill_root)


def test_locked_roots_reject_tamper_outside_connector_script_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    script_path = skill_root / "scripts" / "search.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_bytes(b"locked source")
    support_path = skill_root / "prompts" / "locked.md"
    support_path.parent.mkdir()
    support_path.write_bytes(b"locked support")
    lock_path = tmp_path / "connector.lock"
    lock_path.write_bytes(b"locked requirements")
    _lock_synthetic_reuse_root(monkeypatch, skill_root)
    monkeypatch.setattr(
        connectors,
        "REQUIREMENTS_LOCK_SHA256",
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        connectors,
        "SCRIPT_HASHES",
        {"scripts/search.py": hashlib.sha256(script_path.read_bytes()).hexdigest()},
    )

    support_path.write_bytes(b"single-byte-tamper")
    with pytest.raises(connectors.P2RConnectorError, match="reuse-root identity differs"):
        connectors._locked_roots(lock_path, skill_root)


def test_locked_roots_reject_missing_locked_tree_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    script_path = skill_root / "scripts" / "search.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_bytes(b"locked source")
    support_path = skill_root / "prompts" / "locked.md"
    support_path.parent.mkdir()
    support_path.write_bytes(b"locked support")
    lock_path = tmp_path / "connector.lock"
    lock_path.write_bytes(b"locked requirements")
    _lock_synthetic_reuse_root(monkeypatch, skill_root)
    monkeypatch.setattr(
        connectors,
        "REQUIREMENTS_LOCK_SHA256",
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        connectors,
        "SCRIPT_HASHES",
        {"scripts/search.py": hashlib.sha256(script_path.read_bytes()).hexdigest()},
    )

    support_path.unlink()
    with pytest.raises(connectors.P2RConnectorError, match="reuse-root identity differs"):
        connectors._locked_roots(lock_path, skill_root)


def test_connector_loader_does_not_import_skill_package_initializers(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skill"
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    marker = tmp_path / "initializer-ran"
    (scripts / "__init__.py").write_text(
        "from pathlib import Path\n" f"Path({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    sources = {
        "search_arxiv.py": "ARXIV_API = 'https://export.arxiv.org/api/query'\n",
        "search_openalex.py": "API = 'https://api.openalex.org/works'\n",
        "search_semanticscholar.py": (
            "API = 'https://api.semanticscholar.org/graph/v1/paper/search'\n"
        ),
        "search_openreview.py": "VALUE = 'locked'\n",
    }
    for name, source in sources.items():
        (scripts / name).write_text(source, encoding="utf-8")
    original_path = list(connectors.sys.path)

    modules = connectors._load_connectors(skill_root.resolve())

    assert set(modules) == set(connectors.CONNECTOR_ORDER)
    assert marker.exists() is False
    assert connectors.sys.path == original_path


def test_qualification_script_never_mounts_the_parent_repository() -> None:
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "qualify_p2r_connectors.ps1"
    )
    script = script_path.read_text("utf-8")
    assert "$repoRoot" not in script
    assert "target=/repo" not in script
    assert "source=$workerPackageRoot,target=/module/ai_research_worker,readonly" in script
    assert "source=$lockPath,target=/lock/requirements.lock,readonly" in script
    assert "source=$wheelRoot,target=/wheels" not in script
    assert "source=$isolatedWheelRoot,target=/wheels,readonly" in script
    assert "P2R connector lock must contain exactly 17 unique wheel hashes" in script
    assert "P2R connector wheelhouse is missing one or more locked wheels" in script
    assert script.index('"--network", "none"') < script.index("Read-Host")


def test_qualification_writes_hash_bound_immutable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    output_parent = tmp_path / "output"
    requirements_lock = tmp_path / "connector.lock"
    requirements_lock.write_bytes(b"locked")
    skill_root.mkdir()
    output_parent.mkdir()
    input_receipt = _write_input_receipt(output_parent)
    monkeypatch.setattr(
        connectors,
        "_locked_roots",
        lambda requirements_lock, skill_root: (
            requirements_lock,
            skill_root,
            {
                "fileCount": 104,
                "totalBytes": 1_433_346,
                "aggregateSha256": "a" * 64,
            },
        ),
    )
    monkeypatch.setattr(connectors, "_validate_packages", lambda: None)
    monkeypatch.setattr(
        connectors,
        "_load_connectors",
        lambda _root: {name: object() for name in connectors.CONNECTOR_ORDER},
    )
    for name in connectors.CONNECTOR_ORDER:
        monkeypatch.setattr(connectors, f"_probe_{name}", _ready_probe(name))

    output, ready = connectors.qualify_connectors(
        requirements_lock=requirements_lock,
        skill_root=skill_root,
        output_parent=output_parent,
    )
    receipt = json.loads((output / "connector-receipt.json").read_text("utf-8"))
    assert ready is True
    assert receipt["status"] == "ready"
    assert receipt["claimLevel"] == "qualification_only"
    assert receipt["retryPolicy"] == connectors.RETRY_POLICY
    assert receipt["qualificationRunId"] == "p2rq_" + "a" * 32
    assert receipt["p2rInputReceiptSha256"] == hashlib.sha256(input_receipt).hexdigest()
    assert receipt["researchStudioReuseRoot"] == {
        "fileCount": 104,
        "totalBytes": 1_433_346,
        "aggregateSha256": "a" * 64,
    }
    assert receipt["qualifierSha256"] == hashlib.sha256(
        Path(connectors.__file__).read_bytes()
    ).hexdigest()
    assert list(receipt["connectors"]) == list(connectors.CONNECTOR_ORDER)
    for fact in receipt["connectors"].values():
        assert fact["probeAttempts"] == [{"sequence": 1, "outcome": "ready"}]
    for name in connectors.CONNECTOR_ORDER:
        artifact = f"{name}-hits.json"
        data = (output / artifact).read_bytes()
        assert receipt["artifacts"][artifact] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "sizeBytes": len(data),
        }
    with pytest.raises(connectors.P2RConnectorError, match="immutable"):
        connectors.qualify_connectors(
            requirements_lock=requirements_lock,
            skill_root=skill_root,
            output_parent=output_parent,
        )


def test_partial_failure_is_degraded_and_does_not_persist_secret_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    output_parent = tmp_path / "output"
    requirements_lock = tmp_path / "connector.lock"
    requirements_lock.write_bytes(b"locked")
    skill_root.mkdir()
    output_parent.mkdir()
    _write_input_receipt(output_parent)
    monkeypatch.setattr(
        connectors,
        "_locked_roots",
        lambda requirements_lock, skill_root: (
            requirements_lock,
            skill_root,
            {
                "fileCount": 104,
                "totalBytes": 1_433_346,
                "aggregateSha256": "a" * 64,
            },
        ),
    )
    monkeypatch.setattr(connectors, "_validate_packages", lambda: None)
    monkeypatch.setattr(
        connectors,
        "_load_connectors",
        lambda _root: {name: object() for name in connectors.CONNECTOR_ORDER},
    )
    for name in connectors.CONNECTOR_ORDER:
        monkeypatch.setattr(connectors, f"_probe_{name}", _ready_probe(name))

    secret = "credential-that-must-not-be-written"

    calls = 0

    def fail(_module: object) -> None:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "https://example.org", 429, secret, hdrs=None, fp=None
        )

    monkeypatch.setattr(connectors, "_probe_semanticscholar", fail)
    monkeypatch.setattr(connectors.time, "sleep", lambda _seconds: None)
    output, ready = connectors.qualify_connectors(
        requirements_lock=requirements_lock,
        skill_root=skill_root,
        output_parent=output_parent,
    )
    receipt_bytes = (output / "connector-receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert ready is False
    assert receipt["status"] == "degraded"
    assert receipt["connectors"]["semanticscholar"]["error"] == {
        "type": "HTTPError",
        "httpStatus": 429,
        "category": "rate_limited",
    }
    assert calls == 2
    assert receipt["connectors"]["semanticscholar"]["probeAttempts"] == [
        {
            "sequence": 1,
            "outcome": "failed",
            "error": {
                "type": "HTTPError",
                "httpStatus": 429,
                "category": "rate_limited",
            },
            "backoffSeconds": connectors.SEMANTICSCHOLAR_DEFAULT_RETRY_SECONDS,
        },
        {
            "sequence": 2,
            "outcome": "failed",
            "error": {
                "type": "HTTPError",
                "httpStatus": 429,
                "category": "rate_limited",
            },
        },
    ]
    assert secret.encode() not in receipt_bytes


def test_semanticscholar_429_then_ready_preserves_the_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "skill"
    output_parent = tmp_path / "output"
    requirements_lock = tmp_path / "connector.lock"
    requirements_lock.write_bytes(b"locked")
    skill_root.mkdir()
    output_parent.mkdir()
    _write_input_receipt(output_parent)
    monkeypatch.setattr(
        connectors,
        "_locked_roots",
        lambda requirements_lock, skill_root: (
            requirements_lock,
            skill_root,
            {
                "fileCount": 104,
                "totalBytes": 1_433_346,
                "aggregateSha256": "a" * 64,
            },
        ),
    )
    monkeypatch.setattr(connectors, "_validate_packages", lambda: None)
    monkeypatch.setattr(
        connectors,
        "_load_connectors",
        lambda _root: {name: object() for name in connectors.CONNECTOR_ORDER},
    )
    for name in connectors.CONNECTOR_ORDER:
        monkeypatch.setattr(connectors, f"_probe_{name}", _ready_probe(name))
    calls = 0

    def rate_limited_then_ready(_module: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://example.org", 429, "private", hdrs={"Retry-After": "7"}, fp=None
            )
        return _ready_probe("semanticscholar")(_module)

    delays: list[int] = []
    monkeypatch.setattr(connectors, "_probe_semanticscholar", rate_limited_then_ready)
    monkeypatch.setattr(connectors.time, "sleep", delays.append)

    output, ready = connectors.qualify_connectors(
        requirements_lock=requirements_lock,
        skill_root=skill_root,
        output_parent=output_parent,
    )
    fact = json.loads((output / "connector-receipt.json").read_text("utf-8"))[
        "connectors"
    ]["semanticscholar"]
    assert ready is True
    assert calls == 2
    assert delays == [7]
    assert fact["probeAttempts"] == [
        {
            "sequence": 1,
            "outcome": "failed",
            "error": {
                "type": "HTTPError",
                "httpStatus": 429,
                "category": "rate_limited",
            },
            "backoffSeconds": 7,
        },
        {"sequence": 2, "outcome": "ready"},
    ]


def test_semanticscholar_does_not_retry_excessive_retry_after() -> None:
    assert connectors._retry_after_seconds(
        urllib.error.HTTPError(
            "https://example.org", 429, "private", hdrs={"Retry-After": "121"}, fp=None
        )
    ) is None
