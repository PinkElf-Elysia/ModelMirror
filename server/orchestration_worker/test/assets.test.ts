import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { handleAssetRequest } from '../src/assets.js';

test('Agency assets persist upstream Team and Prompt records under the host-owned root', () => {
  const root = mkdtempSync(join(tmpdir(), 'mm-agency-assets-'));
  const previous = process.env.MM_AGENCY_ASSET_ROOT;
  process.env.MM_AGENCY_ASSET_ROOT = root;
  try {
    handleAssetRequest({
      action: 'save_team',
      team: {
        name: '发布专家组',
        description: 'Reusable launch lineup',
        roles: [
          { role: 'agent-alpha', name: 'Alpha' },
          { role: 'agent-beta', name: 'Beta' },
        ],
      },
    });
    handleAssetRequest({
      action: 'save_template',
      template: { name: '发布任务', content: '为产品制定一份可执行的发布计划。', note: 'first' },
    });
    handleAssetRequest({
      action: 'save_template',
      template: { name: '发布任务', content: '为产品制定一份含验收标准的发布计划。', note: 'second' },
    });

    const listed = handleAssetRequest({ action: 'list' }) as {
      teams: Array<Record<string, unknown>>;
      templates: Array<Record<string, unknown>>;
      garden: Array<Record<string, unknown>>;
    };
    assert.equal(listed.teams.length, 1);
    assert.deepEqual((listed.teams[0].roles as Array<Record<string, unknown>>).map(role => role.role), [
      'agent-alpha',
      'agent-beta',
    ]);
    assert.equal(listed.templates.length, 1);
    assert.equal(listed.templates[0].version_count, 2);
    assert.equal(listed.templates[0].content, '为产品制定一份含验收标准的发布计划。');
    assert.ok(listed.garden.length > 0);

    const promptFile = join(root, 'prompts', '发布任务.prompt.json');
    const stored = JSON.parse(readFileSync(promptFile, 'utf8')) as { versions: unknown[] };
    assert.equal(stored.versions.length, 2);
  } finally {
    if (previous === undefined) delete process.env.MM_AGENCY_ASSET_ROOT;
    else process.env.MM_AGENCY_ASSET_ROOT = previous;
    rmSync(root, { recursive: true, force: true });
  }
});
