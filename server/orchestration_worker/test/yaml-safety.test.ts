import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import yaml from 'js-yaml';

import { parseWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';

test('counts empty merge sources against a small explicit work budget', () => {
  // Keep the regression tiny; no CPU-exhaustion payload or timing assertion.
  const options = { maxTotalMergeKeys: 2 } as yaml.LoadOptions;
  assert.throws(
    () => yaml.load('empty: &empty {}\nmerged: { <<: [*empty, *empty, *empty] }', options),
    /merge keys exceeded maxTotalMergeKeys/,
  );
});

test('the deployed workflow parser bounds a single merge sequence', () => {
  const root = mkdtempSync(join(tmpdir(), 'mm-yaml-safety-'));
  const path = join(root, 'workflow.yaml');
  // Linear, bounded input just beyond the fixed 100-source sequence limit.
  const source = `name: bounded\nllm: { provider: modelmirror, model: synthetic }\nsteps:\n  - id: final\n    role: reviewer\n    task: Review\n    <<: [${Array.from({ length: 101 }, () => '{}').join(',')}]\n`;
  assert.ok(Buffer.byteLength(source) < 1000);
  try {
    writeFileSync(path, source);
    assert.throws(() => parseWorkflow(path), /abnormal merge sequence size/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('the default budget counts empty sources across separate merge sequences', () => {
  const sequence = Array.from({ length: 100 }, () => '{}').join(',');
  const source = Array.from({ length: 101 }, (_, index) => `item${index}: { <<: [${sequence}] }`).join('\n');
  assert.ok(Buffer.byteLength(source) < 40000);
  assert.throws(() => yaml.load(source), /merge keys exceeded maxTotalMergeKeys/);
});

test('ordinary YAML merges keep explicit values and existing workflow semantics', () => {
  const root = mkdtempSync(join(tmpdir(), 'mm-yaml-compat-'));
  const path = join(root, 'workflow.yaml');
  try {
    writeFileSync(path, `name: compatible
llm: { provider: modelmirror, model: synthetic }
defaults: &defaults { role: reviewer, task: Inherited, output: reviewed }
steps:
  - <<: *defaults
    id: final
    task: Explicit
`);
    assert.deepEqual(parseWorkflow(path).steps, [
      { role: 'reviewer', task: 'Explicit', output: 'reviewed', id: 'final' },
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
