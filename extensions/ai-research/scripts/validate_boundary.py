from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = MODULE_ROOT.parents[1]
LDR_IMAGE = (
    "localdeepresearch/local-deep-research:1.10.6@"
    "sha256:b2c634291de8fb8d0662ab81a0b82ec17ab807109d20d57386042c5bdcd472e5"
)
LDR_SBOM_FACTS = {
    "sbomUrl": (
        "https://github.com/LearningCircuit/local-deep-research/releases/download/"
        "v1.10.6/sbom-container-amd64.spdx.json"
    ),
    "sbomSha256": "6f9c0e6f762763d2b34207a7638b65bedd37d818bd86e538483b21cb091c6315",
    "sbomSizeBytes": 5245009,
    "packageCount": 438,
    "packageEcosystems": {
        "pypi": 282,
        "deb": 134,
        "npm": 5,
        "generic": 2,
        "oci": 1,
        "unclassified": 14,
    },
    "declaredGplOrLgplCount": 100,
    "declaredUnknownCount": 60,
    "declaredKnownConcludedNoAssertionCount": 378,
    "declaredUnknownConcludedKnownCount": 22,
    "effectiveUnknownCount": 38,
    "concludedNoAssertionCount": 416,
    "declaredAgplCount": 0,
    "declaredCopyleftByEcosystem": {"deb": 98, "pypi": 2},
}
LDR_DISTRIBUTION_POLICY = {
    "externalPull": "allowed",
    "internallyHostedUse": "allowed_with_notice",
    "mirror": "blocked",
    "offlineBundle": "blocked",
    "modifiedImage": "blocked",
    "representAsMitOnly": "forbidden",
}


class BoundaryFailure(RuntimeError):
    pass


def is_ui_generated(path: Path) -> bool:
    relative = path.relative_to(MODULE_ROOT).parts
    return len(relative) >= 2 and relative[:2] in {
        ("ui", "node_modules"),
        ("ui", "dist"),
    }


def is_local_generated(path: Path) -> bool:
    relative = path.relative_to(MODULE_ROOT).parts
    return bool(relative) and relative[0] in {".venv", "runtime"}


def git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def changed_paths(base: str) -> set[str]:
    paths = set(git("diff", "--name-only", "--diff-filter=ACMRTUXB", base))
    paths.update(git("ls-files", "--others", "--exclude-standard"))
    return paths


def validate_requested_base(requested_base: str, locked_base: str) -> None:
    requested = git("rev-parse", requested_base)
    if not requested:
        raise BoundaryFailure(f"requested base cannot be resolved: {requested_base}")
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", locked_base, requested[0]],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise BoundaryFailure(
            f"requested base {requested_base} diverged from locked base {locked_base}"
        )
    if requested[0] != locked_base:
        print(
            f"AI Research base notice: {requested_base} advanced to {requested[0]}; "
            f"scope remains pinned to {locked_base}",
            file=sys.stderr,
        )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_paths(base: str, boundary: dict) -> None:
    allowed_parent = set(boundary["allowedParentFiles"])
    prefix = "extensions/ai-research/"
    illegal = sorted(
        path for path in changed_paths(base) if not path.startswith(prefix) and path not in allowed_parent
    )
    if illegal:
        raise BoundaryFailure(f"files outside the approved boundary changed: {illegal}")
    for path in MODULE_ROOT.rglob("*"):
        if is_ui_generated(path) or is_local_generated(path):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise BoundaryFailure(f"symlink is forbidden: {path.relative_to(MODULE_ROOT)}")


def validate_locked_files(source_lock: dict) -> None:
    for relative, descriptor in source_lock["lockedFiles"].items():
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise BoundaryFailure(f"locked file is missing: {relative}")
        if path.stat().st_size != descriptor["sizeBytes"] or sha256(path) != descriptor["sha256"]:
            raise BoundaryFailure(f"locked file drifted: {relative}")
    for relative in ("control/requirements.lock", "worker/requirements.lock", "requirements-test.lock"):
        text = (MODULE_ROOT / relative).read_text(encoding="utf-8")
        if "--hash=sha256:" not in text or "index-url" in "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        ):
            raise BoundaryFailure(f"dependency lock is not hash-only and index-neutral: {relative}")
    license_audit = source_lock["licenseAudit"]
    if sha256(MODULE_ROOT / "license-policy.json") != license_audit["policySha256"]:
        raise BoundaryFailure("license policy drifted from the source lock")
    ui_package = (MODULE_ROOT / "ui" / "package.json").read_text(encoding="utf-8")
    ui_lock = (MODULE_ROOT / "ui" / "package-lock.json").read_text(encoding="utf-8")
    if "workspace:" in ui_package + ui_lock or "file:" in ui_package + ui_lock:
        raise BoundaryFailure("UI dependencies must not use workspace: or file: references")
    subprocess.run(
        [
            sys.executable,
            str(MODULE_ROOT / "scripts" / "audit_ui_licenses.py"),
            "--lock",
            str(MODULE_ROOT / "ui" / "package-lock.json"),
            "--policy",
            str(MODULE_ROOT / "license-policy.json"),
            "--output",
            str(MODULE_ROOT / "runtime" / "sbom" / "ui-build-inventory.json"),
        ],
        check=True,
    )


def validate_runtime_references(boundary: dict) -> None:
    runtime_suffixes = {".py", ".sh", ".ps1", ".ts", ".tsx", ".js", ".cjs", ".html"}
    runtime_names = {"Dockerfile", "compose.yml"}
    for path in MODULE_ROOT.rglob("*"):
        if (
            not path.is_file()
            or "tests" in path.parts
            or "scripts" in path.parts
            or is_ui_generated(path)
            or is_local_generated(path)
        ):
            continue
        if path.suffix not in runtime_suffixes and path.name not in runtime_names:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        for forbidden in boundary["forbiddenRuntimeReferences"]:
            if forbidden in text:
                raise BoundaryFailure(
                    f"forbidden runtime reference {forbidden!r} in {path.relative_to(MODULE_ROOT)}"
                )
        if "C:\\Users\\" in text or "/Users/" in text:
            raise BoundaryFailure(f"host absolute path in {path.relative_to(MODULE_ROOT)}")


def validate_metric_names() -> None:
    forbidden = {"score", "accuracy", "win_rate"}
    for path in (MODULE_ROOT / "control" / "ai_research_control").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "log_metric" or not node.args:
                continue
            first = node.args[1] if len(node.args) > 1 else None
            if isinstance(first, ast.Constant) and first.value in forbidden:
                raise BoundaryFailure(f"scientific metric name logged in {path.name}: {first.value}")


def validate_parent_controls() -> None:
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    if "/extensions/ai-research" not in dockerignore.splitlines():
        raise BoundaryFailure("root .dockerignore does not exclude the optional module")
    workflow = REPO_ROOT / ".github" / "workflows" / "ai-research.yml"
    if not workflow.is_file() or "extensions/ai-research/**" not in workflow.read_text(encoding="utf-8"):
        raise BoundaryFailure("path-filtered module workflow is missing")


def _service_block(compose: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = compose.find(marker)
    if start < 0:
        raise BoundaryFailure(f"required Compose service is missing: {name}")
    content_start = start + len(marker)
    next_service = re.search(r"(?m)^  [a-z0-9][a-z0-9_-]*:\n", compose[content_start:])
    end = content_start + next_service.start() if next_service else len(compose)
    return compose[start:end]


def validate_ldr_distribution_mode(
    source_lock: dict,
    distribution_mode: str,
    *,
    compose_text: str | None = None,
    dockerfile_texts: dict[str, str] | None = None,
    packaged_paths: list[str] | None = None,
) -> None:
    if distribution_mode == "redistributable-bundle":
        raise BoundaryFailure(
            "LDR redistributable-bundle is blocked until package obligations and "
            "the 37 effective unknown licenses are disposed"
        )
    if distribution_mode != "external-pull":
        raise BoundaryFailure(f"unsupported distribution mode: {distribution_mode}")

    audit = source_lock.get("licenseAudit", {})
    image_audit = audit.get("localDeepResearchImage", {})
    if audit.get("status") != "passed_for_external_pull":
        raise BoundaryFailure("license audit is not approved for external-pull mode")
    if image_audit.get("integrationMode") != "external_pull_only":
        raise BoundaryFailure("LDR integration must remain external_pull_only")
    if image_audit.get("allowedImage") != LDR_IMAGE:
        raise BoundaryFailure("LDR allowed image is not the audited public digest")
    if image_audit.get("distributionPolicy") != LDR_DISTRIBUTION_POLICY:
        raise BoundaryFailure("LDR distribution policy drifted")
    for key, expected in LDR_SBOM_FACTS.items():
        if image_audit.get(key) != expected:
            raise BoundaryFailure(f"LDR SBOM fact drifted: {key}")

    upstreams = [
        item for item in source_lock.get("upstreams", [])
        if item.get("name") == "Local Deep Research"
    ]
    if len(upstreams) != 1 or upstreams[0].get("image") != LDR_IMAGE:
        raise BoundaryFailure("LDR upstream image lock is missing or ambiguous")
    if upstreams[0].get("integration") != "pull-upstream-image-by-digest":
        raise BoundaryFailure("LDR upstream integration is not a digest pull")

    compose = compose_text
    if compose is None:
        compose = (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")
    for service in ("ai-research-ldr-assets", "ai-research-ldr"):
        block = _service_block(compose, service)
        if f"    image: {LDR_IMAGE}\n" not in block:
            raise BoundaryFailure(f"{service} must use the audited public LDR digest")
        if re.search(r"(?m)^    build:", block):
            raise BoundaryFailure(f"{service} must not build or modify the LDR image")
        if re.search(r"(?m)^    pull_policy:\s*never\s*$", block):
            raise BoundaryFailure(f"{service} must not require an offline-only image")
    if compose.count(f"image: {LDR_IMAGE}") != 2:
        raise BoundaryFailure("the audited LDR image must appear in exactly two services")
    ldr_image_lines = [
        line.strip() for line in compose.splitlines()
        if line.lstrip().startswith("image:") and "local-deep-research" in line.lower()
    ]
    if ldr_image_lines != [f"image: {LDR_IMAGE}", f"image: {LDR_IMAGE}"]:
        raise BoundaryFailure("an unapproved or private LDR image reference is present")

    if dockerfile_texts is None:
        dockerfile_texts = {
            str(path.relative_to(MODULE_ROOT)): path.read_text(encoding="utf-8")
            for path in MODULE_ROOT.rglob("Dockerfile")
            if not is_local_generated(path) and not is_ui_generated(path)
        }
    for name, content in dockerfile_texts.items():
        if re.search(r"(?im)^\s*(?:FROM|COPY)\b.*localdeepresearch", content):
            raise BoundaryFailure(f"LDR image reuse in Dockerfile is forbidden: {name}")

    if packaged_paths is None:
        packaged_paths = [
            str(path.relative_to(MODULE_ROOT)).replace("\\", "/")
            for path in MODULE_ROOT.rglob("*")
            if path.is_file() and not is_local_generated(path) and not is_ui_generated(path)
        ]
    forbidden_archives = [
        path for path in packaged_paths
        if Path(path).suffix.lower() in {".oci", ".tar", ".tgz"}
        and ("ldr" in path.lower() or "local-deep-research" in path.lower())
    ]
    if forbidden_archives:
        raise BoundaryFailure(f"bundled LDR image archive is forbidden: {forbidden_archives}")

    notice = (MODULE_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    disposition = (MODULE_ROOT / "LDR_LICENSE_DISPOSITION.md").read_text(encoding="utf-8")
    if "not represented as MIT-only" not in notice or "external_pull_only" not in disposition:
        raise BoundaryFailure("LDR aggregate-image notice or disposition is missing")


def validate_runtime_privacy_defaults() -> None:
    compose = (MODULE_ROOT / "compose.yml").read_text(encoding="utf-8")
    control_dockerfile = (MODULE_ROOT / "control" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    if "/data/projects" not in control_dockerfile or "chown -R 65532:65532 /data" not in control_dockerfile:
        raise BoundaryFailure(
            "control image must initialize the projects volume for the non-root runtime"
        )

    def service_block(name: str) -> str:
        service_marker = f"  {name}:\n"
        marker_start = compose.find("\n" + service_marker)
        if marker_start < 0:
            raise BoundaryFailure(f"required Compose service is missing: {name}")
        start = marker_start + 1
        content_start = start + len(service_marker)
        next_block = re.search(
            r"(?m)^  [a-z0-9][a-z0-9_-]*:\n",
            compose[content_start:],
        )
        end = (
            content_start + next_block.start()
            if next_block is not None
            else len(compose)
        )
        return compose[start:end]

    if compose.count('MLFLOW_DISABLE_TELEMETRY: "true"') != 2:
        raise BoundaryFailure(
            "MLflow telemetry must be disabled in both control and tracking services"
        )
    relay_contract = {
        "AI_RESEARCH_MODEL_BRIDGE_URL: ${AI_RESEARCH_MODEL_BRIDGE_URL:-http://ai-research-model-relay:8090/api/ai-research/v1}",
        "AI_RESEARCH_MODEL_RELAY_TARGET_URL: ${AI_RESEARCH_MODEL_RELAY_TARGET_URL:-http://host.docker.internal:8000/api/ai-research/v1}",
        "subnet: ${AI_RESEARCH_TRACKING_SUBNET:-10.254.76.0/28}",
        "subnet: ${AI_RESEARCH_INSPECT_VIEW_SUBNET:-10.254.76.16/28}",
        "subnet: ${AI_RESEARCH_LITERATURE_CONTROL_SUBNET:-10.254.76.32/28}",
        "subnet: ${AI_RESEARCH_LITERATURE_EGRESS_SUBNET:-10.254.76.48/28}",
        "subnet: ${AI_RESEARCH_MODEL_BRIDGE_EGRESS_SUBNET:-10.254.76.64/28}",
        "subnet: ${AI_RESEARCH_LOCAL_GATEWAY_SUBNET:-10.254.76.80/28}",
        "ai_research_control.model_relay:app",
        "ai_research_control.console_gateway",
        "AI_RESEARCH_CONSOLE_GATEWAY_CONTROL_URL: http://ai-research-control:8080",
        "AI_RESEARCH_CONSOLE_GATEWAY_TRACKING_URL: http://ai-research-tracking:5000",
        "AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_URL: http://ai-research-inspect-view:7575",
        "AI_RESEARCH_CONSOLE_GATEWAY_INSPECT_PUBLIC_PORT: ${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
        "127.0.0.1:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
        "localhost:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}",
    }
    missing = sorted(value for value in relay_contract if value not in compose)
    if missing:
        raise BoundaryFailure(f"fixed model relay contract drifted: {missing}")
    control_service = service_block("ai-research-control")
    gateway_service = service_block("ai-research-console-gateway")
    relay_block = service_block("ai-research-model-relay")
    tracking_service = service_block("ai-research-tracking")
    inspect_service = service_block("ai-research-inspect-view")
    if "host.docker.internal" in control_service or "model_bridge_egress" in control_service:
        raise BoundaryFailure("control must not have direct model-bridge or generic egress")
    if "host.docker.internal:host-gateway" not in relay_block:
        raise BoundaryFailure("only the fixed model relay may reach the host bridge")
    if compose.count("host.docker.internal:host-gateway") != 1:
        raise BoundaryFailure("host bridge mapping must exist only on the fixed model relay")
    if any("\n    ports:" in block for block in (control_service, tracking_service, inspect_service)):
        raise BoundaryFailure("internal UI services must not publish host ports directly")
    required_gateway_bindings = {
        '127.0.0.1:${AI_RESEARCH_CONTROL_PORT:-8790}:8080',
        '127.0.0.1:${AI_RESEARCH_MLFLOW_PORT:-8791}:8091',
        '127.0.0.1:${AI_RESEARCH_INSPECT_VIEW_PORT:-8793}:8093',
    }
    if any(binding not in gateway_service for binding in required_gateway_bindings):
        raise BoundaryFailure("local UI bindings must terminate on the fixed gateway")
    if compose.count("- local_gateway_ingress") != 1 or "- local_gateway_ingress" not in gateway_service:
        raise BoundaryFailure("only the fixed console gateway may join the ingress network")
    for network in ("tracking_internal", "inspect_view_internal", "literature_control_internal"):
        marker = f"  {network}:\n    internal: true"
        if marker not in compose:
            raise BoundaryFailure(f"control network is not internal: {network}")


def validate_no_secrets(boundary: dict) -> None:
    patterns = [
        re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"sk-" + r"(?:or-v1-)?[A-Za-z0-9_-]{32,}"),
        re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{30,}"),
        re.compile(r"AIza" + r"[A-Za-z0-9_-]{30,}"),
    ]
    candidates = [
        path
        for path in MODULE_ROOT.rglob("*")
        if path.is_file() and not is_ui_generated(path) and not is_local_generated(path)
    ]
    candidates.extend(REPO_ROOT / relative for relative in boundary["allowedParentFiles"])
    for path in candidates:
        if path.suffix in {".pyc", ".png", ".webp", ".zip", ".gz", ".tar"}:
            continue
        if path.stat().st_size > 5_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            raise BoundaryFailure(f"high-confidence secret pattern detected: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--distribution-mode",
        required=True,
        choices=("external-pull", "redistributable-bundle"),
    )
    args = parser.parse_args()
    boundary = json.loads((MODULE_ROOT / "module-boundary.json").read_text(encoding="utf-8"))
    source_lock = json.loads((MODULE_ROOT / "source-lock.json").read_text(encoding="utf-8"))
    locked_base = source_lock["modelMirrorBaseCommit"]
    validate_requested_base(args.base, locked_base)
    validate_paths(locked_base, boundary)
    validate_locked_files(source_lock)
    validate_runtime_references(boundary)
    validate_metric_names()
    validate_parent_controls()
    validate_ldr_distribution_mode(source_lock, args.distribution_mode)
    validate_runtime_privacy_defaults()
    validate_no_secrets(boundary)
    print("AI Research boundary validation passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BoundaryFailure, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"AI Research boundary validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
