from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _load(name: str) -> object:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify() -> None:
    sbom = _load("production.cdx.json")
    licenses = _load("production-licenses.json")
    report = _load("supply-chain-report.json")
    audit = _load("production-audit.json")
    osv = _load("production-osv.json")
    osv_gate = _load("osv-gate-report.json")
    assert isinstance(sbom, dict) and sbom.get("bomFormat") == "CycloneDX"
    assert sbom.get("specVersion") == "1.6"
    application = sbom.get("metadata", {}).get("component", {})
    assert application.get("type") == "application"
    assert application.get("name") == "@modelmirror/upstream-workbench-worker"
    assert isinstance(licenses, dict) and isinstance(licenses.get("components"), list)
    assert all(
        item.get("name") != "@modelmirror/upstream-workbench-worker"
        for item in licenses["components"]
    )
    assert isinstance(report, dict) and report.get("schema_version") == 1
    assert isinstance(audit, dict)
    assert isinstance(osv, dict) and isinstance(osv.get("results"), list)
    assert isinstance(osv_gate, dict) and osv_gate.get("schema_version") == 1
    assert report.get("upstream_revision") == "047505dccc0cc16ad92be11011347d635f33ceb0"
    assert report.get("license_gate", {}).get("status") == "passed"
    assert report.get("audit_gate", {}).get("status") == "passed"
    assert osv_gate.get("gate", {}).get("status") == "passed"
    assert osv_gate.get("scanner", {}).get("image") == (
        "ghcr.io/google/osv-scanner@sha256:"
        "5116601dedc01c1c580eb92371883ec052fc4c13c3fbc109d621a63ac416d475"
    )
    assert osv_gate.get("raw_report_sha256") == hashlib.sha256(
        _canonical(osv).encode("utf-8")
    ).hexdigest()
    expected = hashlib.sha256(_canonical(sbom).encode("utf-8")).hexdigest()
    assert report.get("sbom_sha256") == expected
    assert report.get("production_component_count") == len(licenses["components"])


if __name__ == "__main__":
    verify()
    print("verified supply-chain artifacts")
