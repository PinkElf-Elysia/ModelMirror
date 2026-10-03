"""Offline mutation tests: no server imports, repository initialization or network."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("provider_coverage_guard", ROOT / "scripts/check_provider_coverage.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class CoverageTests(unittest.TestCase):
    def sites(self, source):
        return guard.python_sites(source, "server/example.py")

    def test_new_post_in_registered_function_changes_inventory(self):
        first = self.sites("async def run(client):\n await client.post(url, json=body)\n")
        second = self.sites("async def run(client):\n await client.post(url, json=body)\n await client.post(url, json=body)\n")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertNotEqual(second[0]["id"], second[1]["id"])

    def test_changed_endpoint_requires_review(self):
        a = self.sites("client.post('/models', json=body)")
        b = self.sites("client.post('/chat/completions', json=body)")
        self.assertNotEqual(a[0]["id"], b[0]["id"])

    def test_comments_and_lines_do_not_change_python_identity(self):
        a = self.sites("client.post(url)")
        b = self.sites("# explanatory comment\n\nclient.post( url )\n")
        self.assertEqual(a[0]["id"], b[0]["id"])
        self.assertNotEqual(a[0]["line"], b[0]["line"])

    def test_alias_constructor_and_method_are_discovered(self):
        sites = self.sites("from httpx import AsyncClient as C\nasync def run():\n async with C() as c:\n  send_it = c.post\n  await send_it(url)\n")
        self.assertEqual([s["callee"] for s in sites], ["import", "C", "send_it"])

    def test_helper_alias_is_discovered(self):
        sites = self.sites("from main import collect_chat_completion_text as complete\ncomplete(payload)")
        self.assertEqual(sites[0]["detector"], "delegate")

    def test_config_read_is_candidate_not_proof_of_dispatch(self):
        sites = self.sites("get_llm_gateway_config()")
        self.assertEqual(sites[0]["detector"], "delegate")
        self.assertNotIn("kind", sites[0])

    def test_fastapi_decorator_not_outbound(self):
        self.assertEqual(self.sites("@app.post('/api/chat')\nasync def chat():\n return {'ok': True}"), [])

    def test_strings_and_dict_get_not_outbound(self):
        self.assertEqual(self.sites("text = 'client.post(url)'\ndata.get('model')"), [])

    def test_client_constructor_in_default_argument(self):
        self.assertEqual(len(self.sites("import httpx\ndef f(c=httpx.AsyncClient()):\n pass")), 2)

    def test_class_scopes_distinguish_senders(self):
        sites = self.sites("class A:\n def call(self):\n  self.client.post(url)\nclass B:\n def call(self):\n  self.client.post(url)")
        self.assertEqual([s["symbol"] for s in sites], ["A.call", "B.call"])

    def test_script_injected_fetch_is_detected(self):
        for source in ("fetch(url)", "const f=globalThis.fetch", "fetchImpl(url)", "import https from 'node:https'"):
            self.assertIsNotNone(guard.JS_NETWORK.search(source))

    def test_source_scope_excludes_tests_not_new_modules(self):
        self.assertTrue(guard.is_source("server/new_feature/provider.py"))
        self.assertTrue(guard.is_source("extensions/new_feature/provider.ts"))
        self.assertFalse(guard.is_source("server/tests/test_provider.py"))
        self.assertFalse(guard.is_source("server/storage/credential.json"))
        self.assertFalse(guard.is_source(".env"))

    def test_no_application_import_to_read_registry(self):
        values = guard.registry(ROOT)
        self.assertEqual(len(values), 37)
        self.assertEqual(values["chat_document_native"]["shapes"], ["chat_document_stream"])

    def fixture(self):
        entry = {"id": "example", "status": "migration_pending"}
        for name in ("source", "shapes", "adapters", "configuration", "dispatch", "logical_key", "receipt", "usage", "recovery", "owner", "reason", "policy"):
            entry[name] = "explicitly pending"
        entry["evidence"] = ["server/main.py"]
        sites = self.sites("client.post(url)")
        records = [{"id": sites[0]["id"], "entries": ["example"], "kind": "non_model_http", "owner": "test", "reason": "ordinary HTTP fixture"}]
        return {"contract_version": "modelmirror-provider-coverage-v1", "entries": [entry], "sites": records,
                "source_modules": {s["path"]: s["source_sha256"] for s in sites}}, sites

    def test_endpoint_variable_change_invalidates_non_model_exception(self):
        a = self.sites("url = '/catalog'\nclient.post(url)")
        b = self.sites("url = '/chat/completions'\nclient.post(url)")
        self.assertEqual(a[0]["id"], b[0]["id"])
        self.assertNotEqual(a[0]["source_sha256"], b[0]["source_sha256"])
        manifest, _ = self.fixture()
        manifest["sites"][0]["id"] = a[0]["id"]
        manifest["source_modules"] = {a[0]["path"]: a[0]["source_sha256"]}
        with patch.object(guard, "registry", return_value={}):
            self.assertIn("source module drift: re-review constants, delegates and exceptions", guard.validate(ROOT, manifest, b))

    def test_ordinary_http_can_be_classified_without_claiming_model(self):
        manifest, sites = self.fixture()
        with patch.object(guard, "registry", return_value={}):
            self.assertEqual(guard.validate(ROOT, manifest, sites), [])

    def test_new_stale_duplicate_and_unclassified_sites_fail(self):
        manifest, sites = self.fixture()
        variants = []
        new = copy.deepcopy(manifest)
        new["sites"] = []
        variants.append(new)
        stale = copy.deepcopy(manifest)
        stale["sites"][0]["id"] = "removed"
        variants.append(stale)
        duplicate = copy.deepcopy(manifest)
        duplicate["sites"] *= 2
        variants.append(duplicate)
        missing = copy.deepcopy(manifest)
        missing["sites"][0]["reason"] = ""
        variants.append(missing)
        with patch.object(guard, "registry", return_value={}):
            for variant in variants:
                self.assertTrue(guard.validate(ROOT, variant, sites))

    def test_registry_drift_fails(self):
        manifest, sites = self.fixture()
        with patch.object(guard, "registry", return_value={"new_entry": {}}):
            self.assertIn("workload registry drift (flags/shapes/integration status)", guard.validate(ROOT, manifest, sites))

    def test_unknown_entry_reference_fails(self):
        manifest, sites = self.fixture()
        manifest["sites"][0]["entries"] = ["unknown"]
        with patch.object(guard, "registry", return_value={}):
            self.assertTrue(guard.validate(ROOT, manifest, sites))

    def test_syntax_errors_not_silently_skipped(self):
        with self.assertRaises(SyntaxError):
            self.sites("async def broken(")

    def test_readonly_scan_does_not_import_or_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "server").mkdir()
            source = root / "server/provider.py"
            source.write_text("raise RuntimeError('must not execute')\nclient.post(url)", encoding="utf-8")
            before = source.read_bytes()
            with patch.object(guard, "source_paths", return_value=["server/provider.py"]):
                self.assertEqual(len(guard.scan(root)), 1)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(root.rglob("*")), [root / "server", source])


if __name__ == "__main__":
    unittest.main()
