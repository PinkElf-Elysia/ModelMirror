from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCANNER_IMAGE = (
    "ghcr.io/google/osv-scanner@sha256:"
    "5116601dedc01c1c580eb92371883ec052fc4c13c3fbc109d621a63ac416d475"
)
BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for result in report.get("results", []):
        for package in result.get("packages", []):
            package_data = package.get("package", {})
            for vulnerability in package.get("vulnerabilities", []) or []:
                severity = str(
                    vulnerability.get("database_specific", {}).get("severity", "UNKNOWN")
                ).upper()
                findings.append(
                    {
                        "id": vulnerability.get("id", ""),
                        "aliases": sorted(vulnerability.get("aliases", []) or []),
                        "package": package_data.get("name", ""),
                        "version": package_data.get("version", ""),
                        "severity": severity,
                    }
                )
    return sorted(findings, key=lambda item: (item["severity"], item["id"], item["package"]))


def generate(input_path: Path, output_path: Path) -> None:
    report = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("OSV report must be a JSON object")
    findings = _findings(report)
    counts = Counter(item["severity"] for item in findings)
    blocking = [item for item in findings if item["severity"] in BLOCKING_SEVERITIES]
    summary = {
        "schema_version": 1,
        "scanner": {
            "name": "OSV-Scanner",
            "version": "2.4.0",
            "image": SCANNER_IMAGE,
        },
        "input": "minimal production pnpm lockfile",
        "finding_count": len(findings),
        "severity_counts": dict(sorted(counts.items())),
        "findings": findings,
        "gate": {
            "status": "failed" if blocking else "passed",
            "blocking_severities": sorted(BLOCKING_SEVERITIES),
            "blocking_findings": blocking,
            "policy": "Reject HIGH or CRITICAL OSV findings in the production closure.",
        },
        "raw_report_sha256": hashlib.sha256(_canonical(report)).hexdigest(),
    }
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if blocking:
        raise SystemExit("OSV gate failed: high or critical findings are present")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.input, args.output)


if __name__ == "__main__":
    main()
