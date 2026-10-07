"""Exercise fresh interpreters, not pytest's already-imported package graph."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("package,source_root", [
    ("server.meta_agent", ROOT),
    ("meta_agent", ROOT / "server"),
])
def test_generation_modules_import_in_both_server_launch_modes(package, source_root):
    # -I excludes PYTHONPATH and user site-packages. Neither mode can borrow
    # imports that pytest has installed in sys.modules or its repository path.
    script = """
import importlib
import json
import sys
sys.path.insert(0, sys.argv[1])
package = sys.argv[2]
contract = importlib.import_module(package + '.generation_contract')
recipe = importlib.import_module(package + '.generation_recipe')
edits = importlib.import_module(package + '.recipe_edits')
repair = importlib.import_module(package + '.recipe_repair')
prefix = 'server.' if package.startswith('server.') else ''
nodes = importlib.import_module(prefix + 'workflow_native.node_contracts')
assert contract.WorkflowValueSchema is nodes.WorkflowValueSchema
assert recipe.WorkflowValueSchema is nodes.WorkflowValueSchema
assert recipe.canonical_checksum is nodes.canonical_checksum
assert edits.canonical_checksum is nodes.canonical_checksum
assert repair.canonical_checksum is nodes.canonical_checksum
assert recipe.GENERATION_PROTOCOL_VERSION == 1
schema = contract.GenerationTaskPlan.model_json_schema()
assert schema['properties']['tasks']['maxItems'] == 8
print(json.dumps({'package': package, 'contract_loaded': True}))
"""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(source_root), package],
        cwd=source_root, env=environment, capture_output=True, text=True,
        timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"package": package, "contract_loaded": True}
