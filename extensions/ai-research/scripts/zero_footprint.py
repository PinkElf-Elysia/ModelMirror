from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]


class BaselineFailure(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args: str) -> str:
    result = subprocess.run(
        list(args), cwd=REPO_ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout


def client_dist(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise BaselineFailure(f"client dist is missing: {root}")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    pairs = b""
    for path in files:
        relative = path.relative_to(root).as_posix()
        pairs += relative.encode("utf-8") + b"\0" + sha256(path).encode("ascii") + b"\n"
    return {
        "fileCount": len(files),
        "totalBytes": sum(path.stat().st_size for path in files),
        "aggregateSha256": hashlib.sha256(pairs).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-dist", type=Path, default=REPO_ROOT / "client" / "dist")
    args = parser.parse_args()
    client_dist_root = args.client_dist.resolve()
    source_lock = json.loads((MODULE_ROOT / "source-lock.json").read_text(encoding="utf-8"))
    baseline = source_lock["coreBaseline"]
    locked_base = source_lock["modelMirrorBaseCommit"]
    failures: list[str] = []
    for relative, expected in baseline["trackedFiles"].items():
        actual = sha256(REPO_ROOT / relative)
        if actual != expected:
            failures.append(f"core tracked file drifted: {relative}")
    actual_client_dist = client_dist(client_dist_root)
    if actual_client_dist != baseline["clientDist"]:
        failures.append("client dist hash/count/size changed")

    services = sorted(command("docker", "compose", "config", "--services").splitlines())
    if services != baseline["defaultServices"]:
        failures.append(f"default Compose services changed: {services}")
    compose_config = json.loads(command("docker", "compose", "config", "--format", "json"))
    volumes = sorted((compose_config.get("volumes") or {}).keys())
    if volumes != baseline["defaultVolumes"]:
        failures.append(f"default Compose volumes changed: {volumes}")

    forbidden_diff = command(
        "git", "diff", "--name-only", locked_base, "--", "client", "server", "docker-compose.yml"
    ).strip()
    if forbidden_diff:
        failures.append(f"forbidden core diff exists: {forbidden_diff}")
    if failures:
        raise BaselineFailure("; ".join(failures))
    print(
        json.dumps(
            {
                "status": "passed",
                "clientDist": actual_client_dist,
                "defaultServiceCount": len(services),
                "defaultVolumeCount": len(volumes),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BaselineFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"zero-footprint validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
