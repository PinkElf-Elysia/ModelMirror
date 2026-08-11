from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from skills.trust_scanner import (  # noqa: E402
    SKILL_TRUST_INDEX_VERSION,
    SKILL_TRUST_SCANNER_VERSION,
    SKILL_TRUST_SUMMARY_VERSION,
    catalog_fingerprint_for,
    sha256_json,
    source_key,
)


_RECEIPT_FIELDS = {
    "receiptId", "source", "directoryTreeSha", "packageDigest", "scannerVersion",
    "riskLevel", "trustStatus", "installPolicy", "compatibilityStatus", "routerEligible", "summary",
    "scripts", "opaqueResources", "license", "allowedTools", "dependencies",
    "commands", "capabilities", "findings", "trustFingerprint",
}
_FINDING_FIELDS = {"code", "severity", "message", "path", "line", "field"}
_FORBIDDEN_PAYLOAD_KEYS = {"content", "excerpt", "snippet", "sourcecode", "raw"}


def _assert_redacted_schema(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            assert str(key).casefold() not in _FORBIDDEN_PAYLOAD_KEYS
            _assert_redacted_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_redacted_schema(nested)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"Index is unavailable: {path}") from exc
    assert isinstance(value, dict), f"Index must be an object: {path}"
    return value


def audit(
    *,
    trust_path: Path,
    summary_path: Path,
    runtime_path: Path,
    report_path: Path,
) -> dict[str, int]:
    trust = _load(trust_path)
    summary = _load(summary_path)
    runtime = _load(runtime_path)
    report = _load(report_path)

    assert trust.get("version") == SKILL_TRUST_INDEX_VERSION
    assert trust.get("scannerVersion") == SKILL_TRUST_SCANNER_VERSION
    assert trust.get("fingerprint") == sha256_json({key: value for key, value in trust.items() if key != "fingerprint"})
    assert summary.get("version") == SKILL_TRUST_SUMMARY_VERSION
    assert summary.get("scannerVersion") == SKILL_TRUST_SCANNER_VERSION
    assert summary.get("fingerprint") == sha256_json({key: value for key, value in summary.items() if key != "fingerprint"})
    assert runtime.get("version") == 2

    catalog_fingerprint = catalog_fingerprint_for(runtime["candidates"])
    assert trust["catalogFingerprint"] == catalog_fingerprint
    assert summary["catalogFingerprint"] == catalog_fingerprint
    assert runtime["catalogFingerprint"] == catalog_fingerprint
    assert report["catalogFingerprint"] == catalog_fingerprint
    assert summary["trustIndexFingerprint"] == trust["fingerprint"]
    assert runtime["trustIndexFingerprint"] == trust["fingerprint"]
    assert report["trustIndexFingerprint"] == trust["fingerprint"]

    receipts_by_id: dict[str, Mapping[str, Any]] = {}
    sources: set[str] = set()
    for receipt in trust["receipts"]:
        assert set(receipt) == _RECEIPT_FIELDS
        _assert_redacted_schema(receipt)
        receipt_id = receipt["receiptId"]
        assert re.fullmatch(r"skill-trust-[0-9a-f]{24}", receipt_id)
        assert receipt_id not in receipts_by_id
        payload = {key: value for key, value in receipt.items() if key != "trustFingerprint"}
        assert receipt["trustFingerprint"] == sha256_json(payload)
        source = receipt["source"]
        key = source_key(source["repoUrl"], source["subPath"], source["verifiedCommit"])
        assert key not in sources
        sources.add(key)
        assert receipt["riskLevel"] in {"low", "medium", "high", "critical"}
        assert receipt["trustStatus"] in {"verified", "conditional", "blocked"}
        assert receipt["installPolicy"] in {"allow", "confirm", "block"}
        assert receipt["compatibilityStatus"] in {"portable", "conditional", "unsupported"}
        assert isinstance(receipt["routerEligible"], bool)
        if receipt["installPolicy"] == "block":
            assert receipt["trustStatus"] == "blocked"
            assert receipt["compatibilityStatus"] == "unsupported"
            assert receipt["routerEligible"] is False
        if receipt["routerEligible"]:
            assert receipt["installPolicy"] != "block"
        for finding in receipt["findings"]:
            assert set(finding).issubset(_FINDING_FIELDS)
            assert {"code", "severity", "message"}.issubset(finding)
            finding_path = finding.get("path")
            if finding_path is not None:
                assert isinstance(finding_path, str)
                assert len(finding_path) <= 240
                assert not finding_path.startswith(("/", "\\"))
                assert not re.match(r"^[A-Za-z]:[\\/]", finding_path)
                assert "\n" not in finding_path and "\r" not in finding_path
        for label in [
            receipt.get("license"),
            *receipt["allowedTools"],
            *receipt["dependencies"],
            *receipt["commands"],
        ]:
            if label is not None:
                assert isinstance(label, str)
                assert len(label) <= 200
                assert "\n" not in label and "\r" not in label
        receipts_by_id[receipt_id] = receipt

    summary_by_id = {item["receiptId"]: item for item in summary["receipts"]}
    assert set(summary_by_id) == set(receipts_by_id)
    assert summary["candidateReceipts"] == trust["candidateReceipts"]
    candidate_ids = {candidate["candidateId"] for candidate in runtime["candidates"]}
    assert set(trust["candidateReceipts"]) == candidate_ids
    source_mappings: set[str] = set()
    for candidate in runtime["candidates"]:
        receipt_id = trust["candidateReceipts"][candidate["candidateId"]]
        receipt = receipts_by_id[receipt_id]
        assert source_key(**{
            "repo_url": candidate["installSource"]["repoUrl"],
            "sub_path": candidate["installSource"]["subPath"],
            "verified_commit": candidate["installSource"]["verifiedCommit"],
        }) == source_key(**{
            "repo_url": receipt["source"]["repoUrl"],
            "sub_path": receipt["source"]["subPath"],
            "verified_commit": receipt["source"]["verifiedCommit"],
        })
        assert candidate["trust"] == {
            "receiptId": receipt_id,
            "trustFingerprint": receipt["trustFingerprint"],
            "riskLevel": receipt["riskLevel"],
            "trustStatus": receipt["trustStatus"],
            "installPolicy": receipt["installPolicy"],
            "compatibilityStatus": receipt["compatibilityStatus"],
            "routerEligible": receipt["routerEligible"],
        }
        mapping = "#".join(
            (
                candidate["installSource"]["repoUrl"].casefold().removesuffix(".git"),
                candidate["installSource"]["subPath"].strip("/"),
            )
        )
        assert mapping not in source_mappings
        source_mappings.add(mapping)

    blocked = sum(receipt["installPolicy"] == "block" for receipt in trust["receipts"])
    confirm = sum(receipt["installPolicy"] == "confirm" for receipt in trust["receipts"])
    allow = sum(receipt["installPolicy"] == "allow" for receipt in trust["receipts"])
    assert report["candidateCount"] == len(runtime["candidates"])
    assert report["uniqueReceiptCount"] == len(receipts_by_id)
    return {"receipts": len(receipts_by_id), "allow": allow, "confirm": confirm, "block": blocked}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Skill trust/runtime/client index consistency.")
    parser.add_argument("--trust-index", type=Path, default=ROOT / "server/skills/data/skill_trust_index.json")
    parser.add_argument("--summary-index", type=Path, default=ROOT / "client/src/data/skillTrustIndex.generated.json")
    parser.add_argument("--runtime-index", type=Path, default=ROOT / "server/skills/data/skill_runtime_index.json")
    parser.add_argument("--report", type=Path, default=ROOT / "server/skills/data/skill_trust_report.json")
    args = parser.parse_args()
    result = audit(
        trust_path=args.trust_index.resolve(),
        summary_path=args.summary_index.resolve(),
        runtime_path=args.runtime_index.resolve(),
        report_path=args.report.resolve(),
    )
    print(
        f"Skill trust audit passed: {result['receipts']} receipts; "
        f"allow={result['allow']} confirm={result['confirm']} block={result['block']}"
    )


if __name__ == "__main__":
    main()
