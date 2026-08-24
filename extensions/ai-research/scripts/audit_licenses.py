from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import shutil
import sys
from pathlib import Path


LOCK_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", re.MULTILINE)


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def locked_packages(path: Path) -> dict[str, str]:
    return {canonical_name(name): version for name, version in LOCK_PATTERN.findall(path.read_text())}


def detected_license(metadata: importlib.metadata.PackageMetadata, policy: dict, name: str) -> str | None:
    override = policy.get("overrides", {}).get(name)
    if override:
        return override
    expression = metadata.get("License-Expression")
    if expression:
        return normalize_expression(expression.strip(), policy)
    license_text = metadata.get("License")
    if license_text and len(license_text.strip()) <= 100:
        return normalize_expression(license_text.strip(), policy)
    for classifier in metadata.get_all("Classifier") or []:
        mapped = policy.get("classifierMap", {}).get(classifier)
        if mapped:
            return mapped
    return None


def normalize_expression(value: str, policy: dict) -> str:
    return policy.get("expressionAliases", {}).get(value, value)


def expression_is_allowed(value: str | None, allowed: set[str]) -> bool:
    if not value:
        return False
    identifiers = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", value)
    license_ids = [identifier for identifier in identifiers if identifier not in {"AND", "OR"}]
    return bool(license_ids) and all(identifier in allowed for identifier in license_ids)


def copy_license_files(distribution: importlib.metadata.Distribution, target: Path, name: str) -> list[str]:
    copied: list[str] = []
    for relative in distribution.files or []:
        normalized = str(relative).replace("\\", "/").lower()
        if ".dist-info/licenses/" not in normalized and not normalized.endswith(
            (".dist-info/license", ".dist-info/license.txt", ".dist-info/copying")
        ):
            continue
        source = Path(distribution.locate_file(relative))
        if not source.is_file() or source.is_symlink():
            continue
        destination = target / name / Path(relative).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(str(destination.relative_to(target)).replace("\\", "/"))
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", action="append", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--licenses-dir", type=Path)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    expected: dict[str, str] = {}
    for lock in args.lock:
        for name, version in locked_packages(lock).items():
            if name in expected and expected[name] != version:
                raise RuntimeError(f"conflicting locked versions for {name}")
            expected[name] = version
    allowed_base = {canonical_name(name) for name in policy.get("allowedBasePackages", [])}
    allowed = set(policy["allowedExpressions"])
    inventory: list[dict[str, object]] = []
    installed: set[str] = set()
    failures: list[str] = []
    if args.licenses_dir:
        args.licenses_dir.mkdir(parents=True, exist_ok=True)
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        display_name = metadata.get("Name") or "unknown"
        name = canonical_name(display_name)
        version = distribution.version
        if name not in expected and name not in allowed_base:
            continue
        installed.add(name)
        if name in expected and expected[name] != version:
            failures.append(f"{name}: installed {version}, locked {expected[name]}")
        license_value = detected_license(metadata, policy, name)
        if not expression_is_allowed(license_value, allowed):
            failures.append(f"{name}=={version}: unapproved license {license_value!r}")
        copied = (
            copy_license_files(distribution, args.licenses_dir, name)
            if args.licenses_dir
            else []
        )
        inventory.append(
            {
                "name": display_name,
                "canonicalName": name,
                "version": version,
                "license": license_value,
                "licenseFiles": copied,
            }
        )
    missing = sorted(set(expected) - installed)
    failures.extend(f"missing locked package: {name}" for name in missing)
    report = {
        "schemaVersion": 1,
        "status": "passed" if not failures else "failed",
        "packages": sorted(inventory, key=lambda item: str(item["canonicalName"])),
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"license audit passed for {len(inventory)} installed packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
