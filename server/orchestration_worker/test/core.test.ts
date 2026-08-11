import assert from 'node:assert/strict';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { tmpdir } from 'node:os';

import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import { evaluateCondition } from '../../vendor/agency-orchestrator/src/core/condition.js';
import { parseWorkflow, validateWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';
import { extractVariables, renderTemplate } from '../../vendor/agency-orchestrator/src/core/template.js';
import { parseVerify } from '../../vendor/agency-orchestrator/src/core/verify.js';

test('parses, validates and layers a deterministic workflow', () => {
  const root = join(tmpdir(), `mm-agency-r0-${process.pid}-${Date.now()}`);
  const roles = join(root, 'roles');
  mkdirSync(join(roles, 'experts'), { recursive: true });
  writeFileSync(
    join(roles, 'experts', 'reviewer.md'),
    '---\nname: Reviewer\ndescription: Reviews work\n---\nReview carefully.\n',
    'utf8',
  );
  const workflowPath = join(root, 'workflow.yaml');
  writeFileSync(
    workflowPath,
    `name: review\nagents_dir: ${JSON.stringify(roles)}\nllm:\n  provider: modelmirror\n  model: fake\ninputs:\n  - name: topic\n    required: true\nsteps:\n  - id: draft\n    role: experts/reviewer\n    task: Draft {{topic}}\n    output: draft_text\n  - id: check\n    role: experts/reviewer\n    depends_on: [draft]\n    task: Check {{draft_text}}\n    output: final_text\n`,
    'utf8',
  );

  try {
    const workflow = parseWorkflow(workflowPath);
    assert.deepEqual(validateWorkflow(workflow, roles), []);
    assert.deepEqual(buildDAG(workflow).levels, [['draft'], ['check']]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('preserves template, condition and verification semantics', () => {
  const context = new Map([['topic', 'security review']]);
  assert.equal(renderTemplate('Do {{topic}}', context), 'Do security review');
  assert.deepEqual(extractVariables('{{topic}} then {{result}}'), ['topic', 'result']);
  assert.equal(evaluateCondition('{{topic}} contains security', context), true);
  assert.equal(parseVerify('{"pass":true,"failed":[]}')?.pass, true);
});
