from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from server.coding_worker.parity import ParityEngine, load_frozen_manifest
from server.coding_worker.parity_runner import (
    ParityRunRequest,
    ParityRunnerError,
    SeparatedParityRunner,
)


FIXTURE = Path(__file__).parent / "fixtures" / "coding_worker_v16_parity.json"
CANDIDATE = "1" * 40
ROUTE_RECEIPT = "2" * 64


def _request() -> ParityRunRequest:
    manifest = load_frozen_manifest(FIXTURE)
    task = manifest.tasks[0]
    return ParityRunRequest(
        run_id="run_native_opencode_py_multifile_defect_1",
        task_id=task.task_id,
        engine=ParityEngine.NATIVE_OPENCODE,
        attempt=1,
        objective=task.objective,
        fixture_id=task.fixture_id,
        fixture_revision=task.fixture_revision,
        initial_tree_hash=task.initial_tree_hash,
        hidden_check_bundle_id=task.hidden_check_bundle_id,
        hidden_check_sha256=task.hidden_check_sha256,
        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=manifest.hidden_checker_bundle_sha256,
        runner_image_digest=manifest.runner_images.native_opencode,
        model_route_catalog_sha256=manifest.model_route_catalog_sha256,
        budget=task.budget,
        model_route_receipt_sha256=ROUTE_RECEIPT,
        candidate_sha=CANDIDATE,
        task_manifest_sha256=manifest.canonical_sha256(),
    )


RUNNER_SCRIPT = r"""
import json,sys
r=json.load(sys.stdin)
assert 'hidden_check_bundle_id' not in r
assert 'hidden_check_sha256' not in r
assert 'hidden_checker_bundle_sha256' not in r
print(json.dumps({
  'protocol':r['protocol'],'run_id':r['run_id'],'task_id':r['task_id'],
  'engine':r['engine'],'attempt':r['attempt'],'engine_version':'1.18.9',
  'model_route_receipt_sha256':r['model_route_receipt_sha256'],
  'fixture_bundle_sha256':r['fixture_bundle_sha256'],
  'runner_image_digest':r['runner_image_digest'],'candidate_sha':r['candidate_sha'],
  'task_manifest_sha256':r['task_manifest_sha256'],
  'initial_tree_hash':r['initial_tree_hash'],'final_tree_hash':'f'*64,
  'workspace_export':{'artifact_id':'workspace_export_1','sha256':'e'*64,'size_bytes':42},
  'raw_artifact_manifest_sha256':'a'*64,'policy_violations':[],
  'timeout':False,'budget_limited':False,'stuck':False,'manual_repair':False,
  'undeclared_side_effect':False,'input_tokens':10,'output_tokens':5,
  'tool_calls':2,'active_seconds':3.0
}))
"""


CHECKER_SCRIPT = r"""
import json,sys
r=json.load(sys.stdin)
assert 'objective' not in r
assert 'engine' not in r
assert 'model_route_receipt_sha256' not in r
print(json.dumps({
  'protocol':r['protocol'],'run_id':r['run_id'],'task_id':r['task_id'],
  'attempt':r['attempt'],'hidden_check_sha256':r['hidden_check_sha256'],
  'hidden_checker_bundle_sha256':r['hidden_checker_bundle_sha256'],
  'initial_tree_hash':r['initial_tree_hash'],'final_tree_hash':r['final_tree_hash'],
  'workspace_export_sha256':r['workspace_export']['sha256'],
  'hidden_checks_passed':True,'allowed_diff':True
}))
"""


def test_separated_runner_keeps_hidden_checks_out_of_runner_and_model_data_out_of_checker() -> None:
    runner = SeparatedParityRunner(
        engine=ParityEngine.NATIVE_OPENCODE,
        runner_argv=(sys.executable, "-c", RUNNER_SCRIPT),
        checker_argv=(sys.executable, "-c", CHECKER_SCRIPT),
        timeout_seconds=30,
        checker_timeout_seconds=30,
    )

    outcome = runner.execute(_request())

    assert outcome.accepted is True
    assert outcome.checker_receipt_sha256 is not None
    assert outcome.final_tree_hash == "f" * 64


def test_separated_runner_rejects_checker_receipt_for_a_different_export() -> None:
    tampered = CHECKER_SCRIPT.replace(
        "r['workspace_export']['sha256']", "'0'*64"
    )
    runner = SeparatedParityRunner(
        engine=ParityEngine.NATIVE_OPENCODE,
        runner_argv=(sys.executable, "-c", RUNNER_SCRIPT),
        checker_argv=(sys.executable, "-c", tampered),
        timeout_seconds=30,
        checker_timeout_seconds=30,
    )

    with pytest.raises(ParityRunnerError, match="checker receipt binding"):
        runner.execute(_request())


def test_separated_runner_does_not_accept_runner_self_attestation() -> None:
    self_attesting = RUNNER_SCRIPT.replace(
        "'tool_calls':2,'active_seconds':3.0",
        "'tool_calls':2,'active_seconds':3.0,'hidden_checks_passed':True",
    )
    runner = SeparatedParityRunner(
        engine=ParityEngine.NATIVE_OPENCODE,
        runner_argv=(sys.executable, "-c", self_attesting),
        checker_argv=(sys.executable, "-c", CHECKER_SCRIPT),
        timeout_seconds=30,
        checker_timeout_seconds=30,
    )

    with pytest.raises(ParityRunnerError, match="runner response is invalid"):
        runner.execute(_request())


def test_separated_runner_requires_distinct_commands() -> None:
    command = (sys.executable, "-c", RUNNER_SCRIPT)
    with pytest.raises(ValueError, match="independent"):
        SeparatedParityRunner(
            engine=ParityEngine.NATIVE_OPENCODE,
            runner_argv=command,
            checker_argv=command,
        )
