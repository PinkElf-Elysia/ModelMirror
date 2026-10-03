"""Explicit, task-owned offline acceptance. No application imports or deployment.

prepare snapshots reviewed source; build installs existing test dependencies;
run creates one network-free container; verify binds evidence to that snapshot.
No stage retries and no operations on existing application containers.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

CONTRACT = "modelmirror-provider-acceptance-v1"
PROFILE = "docs/audits/provider-acceptance-profile.json"
DOCKERFILE = "scripts/provider_acceptance.Dockerfile"
SHA = re.compile(r"^[a-f0-9]{64}$")
IMAGE = re.compile(r"^sha256:[a-f0-9]{64}$")
PYTEST = ["python", "-B", "-m", "pytest"]
JUNIT = ["-q", "-p", "no:cacheprovider", "--junitxml=/evidence/result.xml"]
STAGES = {
    "guard": PYTEST + ["server/tests/test_provider_coverage.py", "server/tests/test_provider_acceptance.py"] + JUNIT,
    "workflow": PYTEST + ["server/tests/test_workflow_run_contract.py"] + JUNIT,
    "backend": PYTEST + ["server/tests/", "--ignore=server/tests/test_workflow_run_contract.py"] + JUNIT,
    "worker": ["npm", "run", "test:all", "--prefix", "server/orchestration_worker"],
    "catalog": ["node", "--test", "scripts/update-openrouter-models.test.mjs", "scripts/openrouter-pricing-contracts.test.mjs", ".agents/skills/openrouter-update/scripts/inspect-json.test.mjs", ".agents/skills/openrouter-update/scripts/stable-signature.test.mjs"],
    "frontend_typecheck": ["npm", "run", "typecheck", "--prefix", "client"],
    "frontend_tests": ["npm", "run", "test:run", "--prefix", "client"],
    "frontend_build": ["npm", "run", "build", "--prefix", "client"],
    "help_assets": ["npm", "run", "verify:help-images", "--prefix", "client"],
}


class Rejected(ValueError):
    """Stable errors deliberately omit command output, paths and payloads."""


def require(ok, code):
    if not ok:
        raise Rejected(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "evidence_file_invalid")
    return json.loads(path.read_bytes())


def create_json(path, value):
    with path.open("xb") as stream:
        stream.write(encoded(value))


def command(argv, cwd=None, timeout=60):
    result = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(result.returncode == 0, "command_failed")
    return result.stdout


def validate_profile(profile):
    require(set(profile) == {"contract_version", "profile", "network_mode", "mounts", "ports", "paid_calls", "production", "python_image", "node_image", "stages", "timeout_seconds"}, "profile_fields_invalid")
    require(profile["contract_version"] == CONTRACT and profile["profile"] == "offline-single-writer-validation", "profile_contract_invalid")
    require(profile["network_mode"] == "none" and profile["mounts"] == [] and profile["ports"] == [] and profile["paid_calls"] is False and profile["production"] is False, "profile_isolation_invalid")
    for kind in ("python", "node"):
        require(re.fullmatch(kind + r":[a-zA-Z0-9.-]+@sha256:[a-f0-9]{64}", profile[kind + "_image"]), "base_image_not_pinned")
    require(profile["stages"] == list(STAGES) and type(profile["timeout_seconds"]) is int and 1 <= profile["timeout_seconds"] <= 3600, "profile_stages_invalid")
    return profile


def safe_source(root, relative):
    p = PurePosixPath(relative)
    require(relative == p.as_posix() and not p.is_absolute() and ".." not in p.parts and ":" not in relative and "\\" not in relative, "source_path_invalid")
    require(not any(part in {".git", "node_modules", "__pycache__", ".venv", "venv"} for part in p.parts), "source_path_forbidden")
    require(not (p.name.startswith(".env") and p.name != ".env.example"), "source_secret_file_forbidden")
    require(p.suffix.lower() not in {".sqlite", ".sqlite3", ".db", ".pem", ".key", ".log"}, "source_runtime_file_forbidden")
    target = root.joinpath(*p.parts)
    require(not any(parent.is_symlink() for parent in [target, *target.parents] if parent != root.parent), "source_symlink_forbidden")
    require(target.resolve().is_relative_to(root.resolve()) and target.is_file(), "source_file_invalid")
    return target


def source_inventory(root, includes):
    tracked = {}
    for item in command(["git", "ls-files", "--stage", "-z"], root).decode().split("\0"):
        if not item:
            continue
        meta, path = item.split("\t", 1)
        mode, _, stage = meta.split()
        require(stage == "0" and mode in {"100644", "100755"}, "source_index_invalid")
        tracked[path] = int(mode[-3:], 8)
    dirty = command(["git", "diff", "HEAD", "--name-only", "-z"], root).decode().split("\0")
    require(set(filter(None, dirty)) <= set(includes), "unreviewed_source_changes")
    paths = sorted(set(tracked) | set(includes))
    rows, contents = [], {}
    for path in paths:
        target = safe_source(root, path)
        data = target.read_bytes()
        # Checkout line endings are part of the snapshot, not equated with Git SHA.
        rows.append({"path": path, "sha256": sha(data), "mode": tracked.get(path, 0o644)})
        contents[path] = data
    return rows, contents


def prepare(root, bundle, includes):
    root = root.resolve()
    require(not bundle.exists() and not bundle.resolve().is_relative_to(root), "bundle_must_be_new_external_directory")
    rows, contents = source_inventory(root, includes)
    profile = validate_profile(json.loads(contents[PROFILE]))
    source = {"contract_version": CONTRACT,
              "commit": command(["git", "rev-parse", "HEAD"], root).decode().strip(),
              "git_tree": command(["git", "rev-parse", "HEAD^{tree}"], root).decode().strip(),
              "reviewed_overlays": sorted(includes), "files": rows,
              "content_sha256": sha(encoded(rows)), "profile_sha256": sha(encoded(profile))}
    require(re.fullmatch(r"[a-f0-9]{40}", source["commit"]), "source_commit_invalid")
    bundle.mkdir(parents=True)
    with tarfile.open(bundle / "source.tar", "x") as archive:
        for row in rows:
            path, data = row["path"], contents[row["path"]]
            member = tarfile.TarInfo(path)
            member.size, member.mtime, member.mode = len(data), 0, row["mode"]
            archive.addfile(member, io.BytesIO(data))
    source["archive_sha256"] = sha((bundle / "source.tar").read_bytes())
    (bundle / "Dockerfile").write_bytes(contents[DOCKERFILE])
    source["dockerfile_sha256"] = sha(contents[DOCKERFILE])
    create_json(bundle / "source.json", source)
    create_json(bundle / "profile.json", profile)
    return source["content_sha256"]


def load_bundle(bundle):
    require(bundle.is_dir() and not bundle.is_symlink(), "bundle_invalid")
    source = read_json(bundle / "source.json")
    profile = validate_profile(read_json(bundle / "profile.json"))
    require(source["contract_version"] == CONTRACT and sha(encoded(source["files"])) == source["content_sha256"], "source_manifest_changed")
    require(sha(encoded(profile)) == source["profile_sha256"], "profile_changed")
    for name, key in (("source.tar", "archive_sha256"), ("Dockerfile", "dockerfile_sha256")):
        target = bundle / name
        require(target.is_file() and not target.is_symlink() and sha(target.read_bytes()) == source[key], "build_input_changed")
    return source, profile


def build(bundle):
    source, profile = load_bundle(bundle)
    require(not any((bundle / name).exists() for name in ("image.json", "image.iid", "build-started.json")), "build_already_attempted")
    # An interrupted attempt is preserved; never overwrite or silently resume it.
    create_json(bundle / "build-started.json", {"source_sha256": source["content_sha256"], "at": time.time()})
    command(["docker", "build", "--pull=false", "--iidfile", str(bundle / "image.iid"),
             "--build-arg", "NODE_IMAGE=" + profile["node_image"],
             "--build-arg", "PYTHON_IMAGE=" + profile["python_image"],
             "--build-arg", "SOURCE_SHA256=" + source["content_sha256"], str(bundle)], timeout=3600)
    image = (bundle / "image.iid").read_text().strip()
    require(IMAGE.fullmatch(image), "image_identity_invalid")
    actual = json.loads(command(["docker", "image", "inspect", "--format", '{{json .Id}}', image]))
    require(IMAGE.fullmatch(actual), "image_identity_invalid")
    proof = command(["docker", "create", "--network", "none", "--entrypoint", "/usr/bin/true", actual]).decode().strip()
    for name in ("source.json", "profile.json", "python-packages.txt"):
        command(["docker", "cp", proof + ":/opt/mm-acceptance/" + name, str(bundle / ("image-" + name))])
    require(read_json(bundle / "image-source.json") == source and read_json(bundle / "image-profile.json") == profile, "image_embedded_evidence_mismatch")
    create_json(bundle / "image.json", {"image_id": actual, "build_reference": image,
                                       "source_sha256": source["content_sha256"], "profile_sha256": source["profile_sha256"],
                                       "python_packages_sha256": sha((bundle / "image-python-packages.txt").read_bytes())})
    return actual


def expected_environment(bundle):
    with tarfile.open(bundle / "source.tar") as archive:
        manifest = json.load(archive.extractfile("docs/audits/provider-coverage.json"))
    flags = {"MODEL_CONTROL_CHAT_ENABLED", "MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED"}
    for entry in manifest["entries"]:
        flags.update(entry.get("flags", []))
    require(all(re.fullmatch(r"MODEL_[A-Z_]+", flag) for flag in flags), "flag_name_invalid")
    return ["PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin", "HOME=/tmp", "PYTHONDONTWRITEBYTECODE=1", "CODING_PROJECT_UPLOAD_ROOT=/tmp/project-uploads"] + [flag + "=false" for flag in sorted(flags)]


def checked_image(bundle, source, profile):
    image = read_json(bundle / "image.json")
    require(image["source_sha256"] == source["content_sha256"] and image["profile_sha256"] == source["profile_sha256"] and IMAGE.fullmatch(image["image_id"]), "image_evidence_mismatch")
    require(read_json(bundle / "image-source.json") == source and read_json(bundle / "image-profile.json") == profile, "image_embedded_evidence_mismatch")
    packages = bundle / "image-python-packages.txt"
    require(packages.is_file() and not packages.is_symlink() and sha(packages.read_bytes()) == image["python_packages_sha256"], "image_packages_changed")
    return image


def inspect_container(container):
    # Never request Config.Env, labels, logs or a full inspect of application state.
    fields = {"image": ".Image", "network": ".HostConfig.NetworkMode", "mounts": ".Mounts", "ports": ".HostConfig.PortBindings", "entrypoint": ".Config.Entrypoint", "cmd": ".Config.Cmd", "workdir": ".Config.WorkingDir", "privileged": ".HostConfig.Privileged", "status": ".State.Status", "exit_code": ".State.ExitCode"}
    template = "{" + ",".join('"' + key + '":{{json ' + field + '}}' for key, field in fields.items()) + "}"
    actual = json.loads(command(["docker", "container", "inspect", "--format", template, container]))
    actual["ports"] = actual["ports"] or {}
    return actual


def check_effective(actual, image, argv):
    require(actual["image"] == image, "effective_image_mismatch")
    require(actual["network"] == "none" and actual["mounts"] == [] and not actual["ports"] and actual["privileged"] is False, "effective_isolation_mismatch")
    require(actual["entrypoint"] == ["/usr/bin/env"] and actual["cmd"] == argv and actual["workdir"] == "/validation", "effective_command_mismatch")


def junit_summary(data):
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "junit_dtd_forbidden")
    root = ET.fromstring(data)
    cases = list(root.iter("testcase"))
    failures, skipped = [], 0
    for case in cases:
        if case.find("skipped") is not None:
            skipped += 1
        if case.find("failure") is not None or case.find("error") is not None:
            # Hash identifiers and details; never serialize assertion bodies/params.
            failures.append(sha(encoded([case.get("classname", ""), case.get("name", "")])))
    require(cases, "junit_no_tests")
    return {"tests": len(cases), "failed": len(failures), "skipped": skipped,
            "passed": len(cases) - len(failures) - skipped, "failure_ids": sorted(failures)}


def run_stage(bundle, stage):
    source, profile = load_bundle(bundle)
    require(stage in profile["stages"], "stage_invalid")
    image = checked_image(bundle, source, profile)
    output = bundle / (stage + ".json")
    marker = bundle / (stage + "-started.json")
    require(not output.exists() and not marker.exists(), "stage_already_attempted")
    name = "mm-acceptance-" + uuid.uuid4().hex
    argv = ["-i", *expected_environment(bundle), *STAGES[stage]]
    create_json(marker, {"container": name, "stage": stage, "at": time.time()})
    container = command(["docker", "create", "--name", name, "--network", "none", "--workdir", "/validation", "--entrypoint", "/usr/bin/env", image["image_id"], *argv]).decode().strip()
    before = inspect_container(container)
    check_effective(before, image["image_id"], argv)
    started = time.time()
    interrupted = False
    try:
        result = subprocess.run(["docker", "start", "--attach", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=profile["timeout_seconds"])
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        interrupted = True
        command(["docker", "stop", "--time", "10", container], timeout=30)
        result = None
    after = inspect_container(container)
    check_effective(after, image["image_id"], argv)
    require(after["status"] == "exited", "container_not_finished")
    state = "interrupted" if interrupted else ("passed" if result.returncode == 0 and after["exit_code"] == 0 else "failed")
    report = {"contract_version": CONTRACT, "stage": stage, "source_commit": source["commit"], "source_sha256": source["content_sha256"], "image_id": image["image_id"], "profile_sha256": source["profile_sha256"], "effective_configuration_sha256": sha(encoded({k: v for k, v in before.items() if k not in {"status", "exit_code"}})), "status": state, "exit_code": after["exit_code"], "started_at": started, "finished_at": time.time(), "container": name, "summary": None}
    if stage in {"guard", "workflow", "backend"} and not interrupted:
        raw = bundle / (stage + ".xml")
        command(["docker", "cp", container + ":/evidence/result.xml", str(raw)])
        report["summary"] = junit_summary(raw.read_bytes())
        report["junit_sha256"] = sha(raw.read_bytes())
        # Raw JUnit remains local test evidence only, never an API/commit payload.
        if report["summary"]["failed"]:
            report["status"] = "failed"
    create_json(output, report)
    return report


def verify(bundle):
    source, profile = load_bundle(bundle)
    image = checked_image(bundle, source, profile)
    rows = []
    for stage in profile["stages"]:
        path = bundle / (stage + ".json")
        if not path.exists():
            rows.append({"stage": stage, "status": "not_run"})
            continue
        report = read_json(path)
        require(report["stage"] == stage and report["source_sha256"] == source["content_sha256"] and report["source_commit"] == source["commit"] and report["image_id"] == image["image_id"] and report["profile_sha256"] == source["profile_sha256"], "test_evidence_mismatch")
        expected = {"image": image["image_id"], "network": "none", "mounts": [], "ports": {}, "entrypoint": ["/usr/bin/env"], "cmd": ["-i", *expected_environment(bundle), *STAGES[stage]], "workdir": "/validation", "privileged": False}
        require(report["effective_configuration_sha256"] == sha(encoded(expected)), "test_configuration_mismatch")
        require(report["status"] != "passed" or stage not in {"guard", "workflow", "backend"} or report["summary"] is not None, "missing_required_junit")
        if report["summary"] is not None:
            data = (bundle / (stage + ".xml")).read_bytes()
            require(sha(data) == report["junit_sha256"] and junit_summary(data) == report["summary"], "junit_evidence_changed")
        require(report["status"] in {"passed", "failed", "interrupted"}, "test_status_invalid")
        require(report["status"] != "passed" or (report["exit_code"] == 0 and not (report["summary"] or {}).get("failed")), "false_pass")
        rows.append({"stage": stage, "status": report["status"], "summary": report["summary"]})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "build", "run", "verify"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--stage", choices=list(STAGES))
    args = parser.parse_args()
    try:
        bundle = args.bundle.resolve()
        if args.action == "prepare":
            value = {"source_sha256": prepare(args.root, bundle, args.include)}
        elif args.action == "build":
            value = {"image_id": build(bundle)}
        elif args.action == "run":
            value = run_stage(bundle, args.stage)
        else:
            value = verify(bundle)
        print(json.dumps(value, sort_keys=True))
        if args.action == "run":
            return 0 if value["status"] == "passed" else 1
        if args.action == "verify":
            return 0 if all(row["status"] == "passed" for row in value) else 1
        return 0
    except (Rejected, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ET.ParseError):
        print('{"error":"provider_acceptance_failed_closed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
