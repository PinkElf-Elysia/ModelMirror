from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


class UiLicenseFailure(RuntimeError):
    pass


def package_name(path: str) -> str:
    return path.rsplit("node_modules/", 1)[-1]


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if lock.get("lockfileVersion") != 3:
        raise UiLicenseFailure("UI lockfileVersion must be 3")
    allowed = set(policy["allowedExpressions"])
    inventory: list[dict[str, Any]] = []
    for path, descriptor in sorted(lock.get("packages", {}).items()):
        if not path:
            continue
        if descriptor.get("link"):
            raise UiLicenseFailure(f"linked UI dependency is forbidden: {path}")
        resolved = descriptor.get("resolved")
        integrity = descriptor.get("integrity")
        license_expression = descriptor.get("license")
        if not isinstance(resolved, str) or not resolved.startswith("https://"):
            raise UiLicenseFailure(f"UI dependency is not HTTPS registry locked: {path}")
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            raise UiLicenseFailure(f"UI dependency is missing sha512 integrity: {path}")
        if license_expression not in allowed:
            raise UiLicenseFailure(
                f"UI dependency license is missing or denied: {path}: {license_expression!r}"
            )
        inventory.append(
            {
                "name": package_name(path),
                "version": descriptor.get("version"),
                "license": license_expression,
                "developmentOnly": bool(descriptor.get("dev")),
                "integrity": integrity,
                "resolved": resolved,
            }
        )

    document = {
        "schemaVersion": 1,
        "lockSha256": hashlib.sha256(args.lock.read_bytes()).hexdigest(),
        "packageCount": len(inventory),
        "packages": inventory,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(document) + b"\n")
    print(f"UI license audit passed: {len(inventory)} locked packages")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UiLicenseFailure, json.JSONDecodeError) as exc:
        print(f"UI license audit failed: {exc}")
        raise SystemExit(1)
