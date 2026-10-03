"""No Docker/network required: attack evidence, source and isolation assumptions."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("provider_acceptance", ROOT / "scripts/provider_acceptance.py")
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class AcceptanceTests(unittest.TestCase):
    def profile(self):
        return json.loads((ROOT / h.PROFILE).read_bytes())

    def test_profile_is_pinned_and_offline(self):
        self.assertEqual(h.validate_profile(self.profile())["network_mode"], "none")

    def test_profile_rejects_secrets_and_extra_fields(self):
        value = self.profile()
        value["api_key"] = "synthetic-secret"
        with self.assertRaises(h.Rejected):
            h.validate_profile(value)

    def test_profile_rejects_network_mounts_ports_paid_and_production(self):
        for key, value in (("network_mode", "host"), ("mounts", ["/data"]), ("ports", [8000]), ("paid_calls", True), ("production", True)):
            profile = self.profile()
            profile[key] = value
            with self.subTest(key=key), self.assertRaises(h.Rejected):
                h.validate_profile(profile)

    def test_profile_rejects_tags_and_unbounded_time(self):
        for key, value in (("python_image", "python:latest"), ("timeout_seconds", 3601), ("timeout_seconds", True), ("stages", ["arbitrary_command"])):
            profile = self.profile()
            profile[key] = value
            with self.subTest(key=key), self.assertRaises(h.Rejected):
                h.validate_profile(profile)

    def test_source_path_does_not_read_secrets_or_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("../x", "/x", "C:/x", "server/.env", "node_modules/a.py", "private.key", "runtime.sqlite", "a\\b"):
                with self.subTest(name=name), self.assertRaises(h.Rejected):
                    h.safe_source(root, name)

    def test_source_path_checks_symlink_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file.py"
            path.write_text("synthetic")
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(h.Rejected):
                h.safe_source(Path(temp), "file.py")

    def test_unreviewed_changes_are_rejected(self):
        with patch.object(h, "command", side_effect=[b"100644 abc 0\ta.py\0", b"a.py\0"]):
            with self.assertRaisesRegex(h.Rejected, "unreviewed_source_changes"):
                h.source_inventory(ROOT, [])

    def test_snapshot_preserves_index_modes_and_hashes_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.sh").write_bytes(b"echo synthetic\n")
            with patch.object(h, "command", side_effect=[b"100755 abc 0\ta.sh\0", b""]):
                rows, contents = h.source_inventory(root, [])
            self.assertEqual(rows[0]["mode"], 0o755)
            self.assertEqual(rows[0]["sha256"], h.sha(contents["a.sh"]))

    def test_conflicted_index_fails_closed(self):
        with patch.object(h, "command", return_value=b"100644 abc 2\ta.py\0"), self.assertRaises(h.Rejected):
            h.source_inventory(ROOT, [])

    def actual(self):
        return {"image": "sha256:" + "a" * 64, "network": "none", "mounts": [], "ports": {}, "entrypoint": ["/usr/bin/env"], "cmd": ["-i", "python"], "workdir": "/validation", "privileged": False}

    def test_effective_configuration_not_just_image_tag(self):
        actual = self.actual()
        h.check_effective(actual, actual["image"], actual["cmd"])
        for key, value in (("image", "sha256:" + "b" * 64), ("network", "bridge"), ("mounts", [{}]), ("ports", {"80/tcp": []}), ("privileged", True), ("cmd", ["python"]), ("entrypoint", ["sh"]), ("workdir", "/app")):
            changed = dict(actual, **{key: value})
            with self.subTest(key=key), self.assertRaises(h.Rejected):
                h.check_effective(changed, actual["image"], actual["cmd"])

    def test_junit_distinguishes_skip_failure_error_and_pass(self):
        xml = b'<testsuites><testsuite><testcase name="ok"/><testcase name="s"><skipped/></testcase><testcase name="f"><failure>synthetic secret body</failure></testcase><testcase name="e"><error/></testcase></testsuite></testsuites>'
        result = h.junit_summary(xml)
        self.assertEqual((result["tests"], result["passed"], result["failed"], result["skipped"]), (4, 1, 2, 1))
        self.assertNotIn("secret", json.dumps(result))

    def test_junit_no_tests_or_entities_never_pass(self):
        for xml in (b"<testsuite/>", b'<!DOCTYPE foo [<!ENTITY x "bad">]><testsuite/>'):
            with self.assertRaises(h.Rejected):
                h.junit_summary(xml)

    def test_command_failure_does_not_echo_stderr(self):
        result = subprocess.CompletedProcess([], 1, b"prompt", b"synthetic-secret")
        with patch.object(h.subprocess, "run", return_value=result), self.assertRaisesRegex(h.Rejected, "^command_failed$"):
            h.command(["synthetic"])

    def test_records_are_exclusive_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "record.json"
            h.create_json(path, {"status": "uncertain"})
            with self.assertRaises(FileExistsError):
                h.create_json(path, {"status": "passed"})
            self.assertEqual(h.read_json(path), {"status": "uncertain"})

    def bundle(self, root):
        profile = self.profile()
        source = {"contract_version": h.CONTRACT, "commit": "a" * 40, "files": [], "content_sha256": h.sha(h.encoded([])), "profile_sha256": h.sha(h.encoded(profile)), "archive_sha256": h.sha(b"snapshot"), "dockerfile_sha256": h.sha(b"dockerfile")}
        (root / "source.tar").write_bytes(b"snapshot")
        (root / "Dockerfile").write_bytes(b"dockerfile")
        h.create_json(root / "source.json", source)
        h.create_json(root / "profile.json", profile)
        h.create_json(root / "image-source.json", source)
        h.create_json(root / "image-profile.json", profile)
        (root / "image-python-packages.txt").write_bytes(b"pytest==9.1.0\n")
        self.image_record(root, source)
        return source, profile

    def image_record(self, root, source):
        (root / "image.json").write_bytes(h.encoded({"image_id": "sha256:" + "a" * 64, "source_sha256": source["content_sha256"], "profile_sha256": source["profile_sha256"], "python_packages_sha256": h.sha(b"pytest==9.1.0\n")}))

    def test_build_inputs_or_profile_change_rejected(self):
        for name in ("source.tar", "Dockerfile", "profile.json"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.bundle(root)
                (root / name).write_bytes(b"{}")
                with self.subTest(name=name), self.assertRaises(h.Rejected):
                    h.load_bundle(root)

    def test_build_interruption_not_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.bundle(root)
            h.create_json(root / "build-started.json", {})
            with patch.object(h, "command") as execute, self.assertRaises(h.Rejected):
                h.build(root)
            execute.assert_not_called()

    def test_verify_missing_stages_not_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self.bundle(root)
            rows = h.verify(root)
            self.assertEqual(len(rows), len(profile["stages"]))
            self.assertTrue(all(row["status"] == "not_run" for row in rows))

    def test_foreign_source_evidence_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.bundle(root)
            changed = dict(source, content_sha256="b" * 64)
            self.image_record(root, changed)
            with self.assertRaisesRegex(h.Rejected, "image_evidence_mismatch"):
                h.verify(root)

    def test_changed_embedded_image_or_dependencies_rejected(self):
        for name in ("image-source.json", "image-profile.json", "image-python-packages.txt"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.bundle(root)
                (root / name).write_bytes(b"{}")
                with self.subTest(name=name), self.assertRaises(h.Rejected):
                    h.verify(root)

    def report(self, root, source, stage="guard"):
        expected = self.actual()
        expected["cmd"] = ["-i", *h.STAGES[stage]]
        return {"stage": stage, "source_commit": source["commit"], "source_sha256": source["content_sha256"], "image_id": expected["image"], "profile_sha256": source["profile_sha256"], "effective_configuration_sha256": h.sha(h.encoded(expected)), "status": "passed", "exit_code": 0, "summary": None}

    def test_success_without_junit_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.bundle(root)
            h.create_json(root / "guard.json", self.report(root, source))
            with patch.object(h, "expected_environment", return_value=[]), self.assertRaisesRegex(h.Rejected, "missing_required_junit"):
                h.verify(root)

    def test_nonzero_exit_cannot_be_reported_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.bundle(root)
            report = self.report(root, source, "worker")
            report["exit_code"] = 1
            h.create_json(root / "worker.json", report)
            with patch.object(h, "expected_environment", return_value=[]), self.assertRaisesRegex(h.Rejected, "false_pass"):
                h.verify(root)

    def test_report_image_config_and_commit_must_match(self):
        for key in ("source_commit", "image_id", "effective_configuration_sha256"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, _ = self.bundle(root)
                report = self.report(root, source, "worker")
                report[key] = "mismatched"
                h.create_json(root / "worker.json", report)
                with patch.object(h, "expected_environment", return_value=[]), self.subTest(key=key), self.assertRaises(h.Rejected):
                    h.verify(root)

    def test_junit_tampering_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _ = self.bundle(root)
            xml = b'<testsuite><testcase name="ok"/></testsuite>'
            report = self.report(root, source)
            report.update(summary=h.junit_summary(xml), junit_sha256=h.sha(xml))
            h.create_json(root / "guard.json", report)
            (root / "guard.xml").write_bytes(xml)
            with patch.object(h, "expected_environment", return_value=[]):
                self.assertEqual(h.verify(root)[0]["status"], "passed")
                (root / "guard.xml").write_bytes(b"<testsuite/>")
                with self.assertRaisesRegex(h.Rejected, "junit_evidence_changed"):
                    h.verify(root)


if __name__ == "__main__":
    unittest.main()
