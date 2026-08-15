import assert from 'node:assert/strict';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { tmpdir } from 'node:os';

import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import { evaluateCondition } from '../../vendor/agency-orchestrator/src/core/condition.js';
import { parseWorkflow, validateWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';
import { extractVariables, renderTemplate } from '../../vendor/agency-orchestrator/src/core/template.js';
import { parseVerify } from '../../vendor/agency-orchestrator/src/core/verify.js';
import { BridgeConnector } from '../src/bridge_connector.js';
import { JsonlChannel } from '../src/channel.js';
import { AGENCY_BRIDGE_PROTOCOL } from '../src/protocol.js';
import {
  handleRequest,
  normalizeAllowedResourceAcceptance,
  removeUnsupportedExtensionClauses,
  validateGoalDurationUnits,
  validateGoalContentProhibitions,
  validateGoalProhibitions,
  validateUncertaintyPolicy,
} from '../src/service.js';

test('narrows model-generated named-resource acceptance without weakening an explicit every-action rule', () => {
  const workflow = `name: checklist
steps:
  - id: final
    role: agent-alpha
    task: Create a checklist.
    acceptance: |-
      1. 包含三个阶段
      2. 所有检查项仅使用纸质登记簿和共享盘两种现有资源
    output: final_output`;
  const normalized = normalizeAllowedResourceAcceptance(
    workflow,
    '仅使用现有纸质登记簿和共享盘，不新增软件。',
  );
  assert.equal(normalized.changed, true);
  assert.match(normalized.yaml, /检查清单不得引入纸质登记簿和共享盘以外的新工具、软件或系统/);
  assert.doesNotMatch(normalized.yaml, /所有检查项仅使用/);

  const explicit = normalizeAllowedResourceAcceptance(
    workflow,
    '所有检查项必须使用纸质登记簿或共享盘完成。',
  );
  assert.equal(explicit.changed, false);
  assert.equal(explicit.yaml, workflow);
});

test('rejects a plan that resolves required pending markers without human input', () => {
  const workflow = {
    name: 'policy',
    agents_dir: 'modelmirror-experts',
    llm: { provider: 'modelmirror', model: 'fake' },
    steps: [
      {
        id: 'draft', role: 'agent-alpha',
        task: 'Keep every missing value marked 待确认.', output: 'draft_text',
      },
      {
        id: 'final', role: 'agent-alpha', depends_on: ['draft'],
        task: '将草案中的待确认标记替换为行业惯例数值。',
        acceptance: '所有待确认标记已被合理替换。', output: 'final_text',
      },
    ],
  } as unknown as Parameters<typeof validateUncertaintyPolicy>[1];

  assert.deepEqual(
    validateUncertaintyPolicy('缺失信息标为待确认。', workflow),
    [
      'step "final" requires unresolved TBD/pending placeholders to be removed without preceding human_input; approval does not supply missing external facts and this also contradicts the user\'s explicit uncertainty policy',
    ],
  );
  workflow.steps[1]!.task = '生成最终内部版。';
  workflow.steps[1]!.acceptance = '无【待定】标记，所有占位描述合理。';
  assert.deepEqual(
    validateUncertaintyPolicy('生成最终内部版。', workflow),
    [],
  );
  workflow.steps[1]!.task = '保留所有待确认标记，并输出正式版本。';
  workflow.steps[1]!.acceptance = '所有缺失信息仍明确标为待确认。';
  assert.deepEqual(validateUncertaintyPolicy('缺失信息标为待确认。', workflow), []);
  workflow.steps[1]!.task = '缺失信息标为待确认，然后定义数据删除时限。';
  assert.deepEqual(validateUncertaintyPolicy('缺失信息标为待确认。', workflow), []);
  workflow.steps[1]!.task = '将所有【待补充：XXXX】部分根据行业最佳实践填充为具体条款。';
  workflow.steps[1]!.acceptance = '所有【待补充】标记已被具体条款替换。';
  assert.deepEqual(
    validateUncertaintyPolicy('缺失信息标为待确认，批准草案后生成最终内部版。', workflow),
    ['step "final" requires unresolved TBD/pending placeholders to be removed without preceding human_input; approval does not supply missing external facts and this also contradicts the user\'s explicit uncertainty policy'],
  );
});

test('rejects individual aliases when the goal forbids real staffing', () => {
  const workflow = {
    name: 'staffing template',
    agents_dir: 'modelmirror-experts',
    llm: { provider: 'modelmirror', model: 'fake' },
    steps: [{
      id: 'schedule', role: 'agent-alpha', output: 'schedule_text',
      task: 'Use role names such as Volunteer A and assign the detailed two-week template.',
      acceptance: 'The template uses Volunteer A-L.',
    }],
  } as unknown as Parameters<typeof validateGoalProhibitions>[1];

  assert.deepEqual(
    validateGoalProhibitions('Design a template, but 不执行真实排班。', workflow),
    [
      'step "schedule" contradicts the user\'s prohibition on real staffing by requesting individual aliases or assignments; use role slots and capacity placeholders marked TBD without assigning people to dates or shifts',
    ],
  );
  workflow.steps[0]!.task = 'Use anonymous role slots marked TBD; 不得使用志愿者A等个人别名。';
  workflow.steps[0]!.acceptance = 'Do not assign people to dates or shifts.';
  assert.deepEqual(validateGoalProhibitions('Design a template, but 不执行真实排班。', workflow), []);
  workflow.steps[0]!.task = '使用窗口接待员A和库存管理员B填充两周模板。';
  assert.equal(validateGoalProhibitions('Design a template, but 不执行真实排班。', workflow).length, 1);
  workflow.steps[0]!.task = '按窗口A和窗口B定义岗位容量，不分配任何人员。';
  assert.deepEqual(validateGoalProhibitions('Design a template, but 不执行真实排班。', workflow), []);
});

test('rejects positive task instructions for explicitly prohibited content', () => {
  const workflow = {
    name: 'bounded-card',
    steps: [{
      id: 'draft', role: 'agent-alpha', output: 'draft_text',
      task: [
        '列出联系人、联系方式（电话/IM）和职责。',
        '按影响用户数和持续时间判断是否执行升级流程。',
        '使用值班软件记录并监控。',
      ].join('\n'),
      acceptance: '不得新增人数、响应时限、软件、通知渠道或执行真实操作。',
    }],
  } as unknown as Parameters<typeof validateGoalContentProhibitions>[1];
  const errors = validateGoalContentProhibitions(
    '不得新增响应时限、人数、预算、软件、通知渠道或执行真实操作。',
    workflow,
  );
  assert.equal(errors.length, 5);
  assert.ok(errors.every(error => error.includes('step "draft"')));

  workflow.steps[0]!.task = '只描述故障类型；不联系、不通知、不执行升级流程。';
  workflow.steps[0]!.acceptance = '未新增响应时限、人数、预算、软件或通知渠道。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得新增响应时限、人数、预算、软件、通知渠道或执行真实操作。',
    workflow,
  ), []);

  workflow.steps[0]!.acceptance = '没有任何新增的日期、地点、人数、预算、软件或通知渠道信息。';
  workflow.steps[0]!.task = '讨论现代软件开发中的协作范式。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得新增日期、地点、人数、预算、软件或通知渠道。',
    workflow,
  ), []);

  workflow.steps[0]!.task = '输出三条讨论问题。';
  workflow.steps[0]!.acceptance = '不添加任何额外信息（如日期、地点、人数、预算、软件或通知渠道）。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得添加日期、地点、人数、预算、软件或通知渠道。',
    workflow,
  ), []);

  workflow.steps[0]!.acceptance = '未添加任何日期、地点、人数、预算、软件或通知渠道信息。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得添加日期、地点、人数、预算、软件或通知渠道。',
    workflow,
  ), []);

  workflow.steps[0]!.acceptance = '没有任何日期、地点、人数、预算、软件或通知渠道的额外信息。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得添加日期、地点、人数、预算、软件或通知渠道。',
    workflow,
  ), []);

  workflow.steps[0]!.acceptance = '请勿添加超出事实范围的日期、人数、预算、软件或外部系统信息。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得添加日期、人数、预算、软件或外部系统。',
    workflow,
  ), []);

  workflow.steps[0]!.acceptance = '没有引入超出事实范围的日期、人数、预算、软件或外部系统信息。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得引入日期、人数、预算、软件或外部系统。',
    workflow,
  ), []);

  workflow.steps[0]!.task = '形成面向新手的主持人备忘卡草案。';
  workflow.steps[0]!.acceptance = '2. 未虚构任何日期、地点、天气、设备数量、参与人数、收费、联系人或安全认证';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得虚构活动日期、地点、天气、设备数量、参与人数、收费、联系人或安全认证。',
    workflow,
  ), []);

  workflow.steps[0]!.task = '讨论共同故事如何让成千上万的陌生人协作。';
  workflow.steps[0]!.acceptance = '讨论问题具有启发性。';
  assert.match(
    validateGoalContentProhibitions('不得新增人数。', workflow).join('\n'),
    /prohibited headcounts/,
  );
  workflow.steps[0]!.task = '讨论协作如何从几十人的部落扩展。';
  assert.match(
    validateGoalContentProhibitions('不得新增人数。', workflow).join('\n'),
    /prohibited headcounts/,
  );

  workflow.steps[0]!.task = '应用：协作模式的演进与设计。';
  workflow.steps[0]!.acceptance = '讨论问题具有启发性。';
  assert.deepEqual(validateGoalContentProhibitions('不得新增软件。', workflow), []);

  workflow.steps[0]!.task = '讨论合作成本与制度成本。';
  assert.deepEqual(validateGoalContentProhibitions('不得新增预算。', workflow), []);
  assert.match(
    validateGoalContentProhibitions('不得新增成本。', workflow).join('\n'),
    /prohibited budgets/,
  );

  workflow.steps[0]!.task = '使用值班软件记录并监控。';
  assert.equal(validateGoalContentProhibitions(
    '不得新增软件。',
    workflow,
  ).length, 1);

  workflow.steps[0]!.task = '升级信息清单仅列出需要通知的角色名称，不包含联系方式（如电话、IM群组）或执行指令。';
  assert.deepEqual(validateGoalContentProhibitions(
    '不得新增响应时限、人数、预算、软件、通知渠道或执行真实操作。',
    workflow,
  ), []);

  workflow.steps[0]!.task = [
    '不得新增以下信息：',
    '- 响应时限',
    '- 人数',
    '- 预算',
    '- 软件',
    '- 通知渠道',
    '升级信息清单可列出故障所属系统和需要通知的角色名称。',
  ].join('\n');
  assert.deepEqual(validateGoalContentProhibitions(
    '不得新增响应时限、人数、预算、软件、通知渠道或执行真实操作。',
    workflow,
  ), []);
});

test('rejects a planning step that silently changes a user duration unit', () => {
  const workflow = {
    name: 'duration-policy',
    steps: [{
      id: 'final', role: 'agent-alpha', output: 'final_output',
      task: '例外审批必须在3个工作日内响应，日志保留1年。',
      acceptance: '明确3个工作日响应和1年日志保留。',
    }],
  } as unknown as Parameters<typeof validateGoalDurationUnits>[1];
  const errors = validateGoalDurationUnits('例外审批响应3天，日志保留1年。', workflow);
  assert.equal(errors.length, 1);
  assert.match(errors[0] ?? '', /changes the user's duration 3 天 to 3 工作日/);
  workflow.steps[0]!.task = '例外审批必须在3个自然日内响应，日志保留1年。';
  workflow.steps[0]!.acceptance = '明确3天响应和1年日志保留。';
  const naturalDayErrors = validateGoalDurationUnits('例外审批响应3天，日志保留1年。', workflow);
  assert.equal(naturalDayErrors.length, 1);
  assert.match(naturalDayErrors[0] ?? '', /changes the user's duration 3 天 to 3 自然日/);
  workflow.steps[0]!.task = '例外审批必须在3天内响应，如遇非工作日顺延至下一工作日。';
  workflow.steps[0]!.acceptance = '明确3天响应和1年日志保留。';
  const extensionErrors = validateGoalDurationUnits('例外审批响应3天，日志保留1年。', workflow);
  assert.equal(extensionErrors.length, 1);
  assert.match(extensionErrors[0] ?? '', /silently adds a deadline-extension policy/);
  workflow.steps[0]!.task = '例外审批必须在3天内响应，日志保留1年。';
  workflow.steps[0]!.acceptance = '明确3天响应和1年日志保留。';
  assert.deepEqual(validateGoalDurationUnits('例外审批响应3天，日志保留1年。', workflow), []);
  workflow.steps[0]!.task = '建议例外审批在3个工作日内响应，具体时限待确认。';
  workflow.steps[0]!.acceptance = '保留用户要求的3天响应。';
  assert.deepEqual(validateGoalDurationUnits('例外审批响应3天。', workflow), []);
});

test('deterministically removes only unsupported holiday-extension clauses before model repair', () => {
  const source = `name: duration\nsteps:\n  - id: final\n    role: agent-alpha\n    task: |\n      保留3天响应。\n      如遇非工作日，顺延至下一工作日。\n      生成最终内部版。\n    acceptance: 保留3天响应。\n    output: final_output\n`;
  const normalized = removeUnsupportedExtensionClauses(source, '例外审批响应3天。');
  assert.equal(normalized.changed, true);
  assert.match(normalized.yaml, /保留3天响应/);
  assert.match(normalized.yaml, /生成最终内部版/);
  assert.doesNotMatch(normalized.yaml, /顺延|下一工作日/);

  const authorized = removeUnsupportedExtensionClauses(source, '例外审批响应3天，如遇非工作日顺延至下一工作日。');
  assert.equal(authorized.changed, false);
  assert.equal(authorized.yaml, source);
});

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

test('planning rejects a truncated response without a hidden paid retry', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const connector = new BridgeConnector(new JsonlChannel(input, output), 'planning-recovery');
  const requests: Record<string, unknown>[] = [];
  let buffer = '';
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      requests.push(message);
      input.write(`${JSON.stringify({
        protocol: AGENCY_BRIDGE_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: false,
        error: { code: 'model_output_truncated', message: 'truncated' },
      })}\n`);
    }
  });

  await assert.rejects(
    connector.chat('system', 'goal', {
      provider: 'modelmirror',
      model: 'fake',
      max_tokens: 10240,
      temperature: 0.2,
    }),
    (error: unknown) => error instanceof Error && error.message === 'truncated',
  );

  assert.equal(requests.length, 1);
  input.end();
  output.end();
});

test('planning validation enforces the six-step execution contract', async () => {
  const steps = Array.from({ length: 7 }, (_, index) => {
    const id = `step_${index + 1}`;
    const previous = index > 0 ? `\n    depends_on: [step_${index}]` : '';
    const acceptance = index === 6 ? '\n    acceptance: Final result is actionable.' : '';
    return `  - id: ${id}\n    role: agent-alpha${previous}\n    task: Complete stage ${index + 1}.${acceptance}\n    output: output_${index + 1}`;
  }).join('\n');
  const input = new PassThrough();
  const output = new PassThrough();
  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id: 'validate-six-step-limit',
    method: 'validate',
    params: {
      yaml: `name: bounded-plan\nagents_dir: modelmirror-experts\nllm:\n  provider: modelmirror\n  model: fake\nsteps:\n${steps}`,
      agents: [{
        id: 'agent-alpha',
        path: 'agent-alpha',
        name: 'Alpha',
        department: 'Product',
        description: 'Product planning expert',
        system_prompt: 'Plan product work.',
      }],
    },
  }, new JsonlChannel(input, output));

  assert.equal(result.valid, false);
  assert.match(String((result.errors as string[]).join('\n')), /more than 6 steps/);
  input.end();
  output.end();
});

test('planning rejects outputs that collide with host runtime context variables', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id: 'validate-reserved-output',
    method: 'validate',
    params: {
      yaml: `name: reserved-output
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake
steps:
  - id: final
    role: agent-alpha
    task: Write the final result.
    acceptance: The result is complete.
    output: user_input`,
      agents: [{
        id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Product',
        description: 'Product planning expert', system_prompt: 'Plan product work.',
      }],
    },
  }, new JsonlChannel(input, output));
  assert.equal(result.valid, false);
  assert.match(String((result.errors as string[]).join('\n')), /reserved ModelMirror context variable/);
  input.end();
  output.end();
});

test('planning rejects structured property references on plain-text step outputs', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id: 'validate-structured-reference',
    method: 'validate',
    params: {
      yaml: `name: structured-reference
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake
steps:
  - id: requirements
    type: human_input
    prompt: Confirm the required format.
    output: user_requirements
  - id: final
    role: agent-alpha
    depends_on: [requirements]
    task: Write the result from {{user_requirements}}.
    acceptance: The format must match {{user_requirements.format}}.
    output: final_output`,
      agents: [{
        id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Product',
        description: 'Product planning expert', system_prompt: 'Plan product work.',
      }],
    },
  }, new JsonlChannel(input, output));
  assert.equal(result.valid, false);
  assert.match(String((result.errors as string[]).join('\n')), /unsupported structured template reference/);
  input.end();
  output.end();
});

test('planning accepts a well-formed serial HITL workflow without semantic rewrites', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  let buffer = '';
  let modelRequests = 0;
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      modelRequests += 1;
      const content = `name: preserved HITL plan
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake
steps:
  - id: analyze
    role: agent-alpha
    task: Analyze the onboarding obstacles from the supplied goal.
    output: analysis_output
  - id: choose
    type: human_input
    depends_on: [analyze]
    prompt: Choose the primary audience after reviewing {{analysis_output}}.
    task: Wait for the user's audience choice.
    output: audience_choice
  - id: experiment
    role: agent-alpha
    depends_on: [choose]
    task: Design experiments for {{audience_choice}}.
    output: experiment_output
  - id: approve
    type: approval
    depends_on: [experiment]
    prompt: Approve the experiment plan {{experiment_output}}.
    task: Wait for approval.
    output: approval_output
  - id: final
    role: agent-alpha
    depends_on: [approve]
    task: Produce the final brief in no more than 800 Chinese characters.
    acceptance: Final brief must be no more than 800 Chinese characters.
    output: final_output`;
      input.write(`${JSON.stringify({
        protocol: AGENCY_BRIDGE_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id,
        ok: true,
        result: {
          content,
          finish_reason: 'stop',
          usage: { input_tokens: 20, output_tokens: 30 },
        },
      })}\n`);
    }
  });

  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id: 'explicit-hitl-contract',
    method: 'compose',
    params: {
      goal: '先分析新手引导阻力，然后暂停让我选择首要人群；制定实验后暂停让我审批。最终简报不超过 800 字。',
      model_id: 'fake-model',
      agents: [{
        id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Product',
        description: 'Product planning expert', system_prompt: 'Plan product work.',
      }],
      mode: 'auto', pinned_agent_ids: [], max_agents: 1, temperature: 0.2,
      allow_hitl: true,
    },
  }, new JsonlChannel(input, output));

  const validation = result.validation as Record<string, unknown>;
  const workflow = validation.workflow as { steps: Array<Record<string, unknown>> };
  assert.equal(modelRequests, 1);
  assert.equal(result.repair_used, false);
  assert.equal(validation.valid, true, JSON.stringify(validation.errors));
  assert.deepEqual(workflow.steps.map(step => step.type ?? 'normal'), [
    'normal', 'human_input', 'normal', 'approval', 'normal',
  ]);
  assert.match(String(workflow.steps.at(-1)?.acceptance), /800/);
  input.end();
  output.end();
});

test('planning does not surface upstream validator warnings after final repair succeeds', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let buffer = '';
  let calls = 0;
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      calls += 1;
      const content = calls === 1
        ? `name: Needs repair\nllm:\n  provider: modelmirror\n  model: fake-model\nsteps:\n  - id: draft\n    role: agent-alpha\n    task: Draft rules and reference {{missing_output}}.\n    output: draft_output\n  - id: final\n    role: agent-alpha\n    task: Publish {{draft_output}}.\n    output: final_output\n    depends_on: [draft]\n    acceptance: Final rules are complete.`
        : `name: Repaired\nllm:\n  provider: modelmirror\n  model: fake-model\nsteps:\n  - id: draft\n    role: agent-alpha\n    task: Draft rules.\n    output: draft_output\n  - id: final\n    role: agent-alpha\n    task: Publish {{draft_output}}.\n    output: final_output\n    depends_on: [draft]\n    acceptance: Final rules are complete.`;
      input.write(`${JSON.stringify({
        protocol: AGENCY_BRIDGE_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id,
        ok: true,
        result: { content, finish_reason: 'stop', usage: { input_tokens: 10, output_tokens: 10 } },
      })}\n`);
    }
  });

  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request', id: 'stale-warning', method: 'compose',
    params: {
      goal: 'Draft and publish volunteer access rules.', model_id: 'fake-model',
      agents: [{
        id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Security',
        description: 'Security policy expert.', system_prompt: 'Write safe policies.',
      }],
      mode: 'auto', max_agents: 1, temperature: 0.2, allow_hitl: false,
    },
  }, channel);

  assert.equal((result.validation as Record<string, unknown>).valid, true);
  assert.deepEqual(result.warnings, []);
  assert.equal(result.repair_used, true);
  input.end();
  output.end();
});

test('planning repairs an individual-alias task before real-staffing execution', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let buffer = '';
  let calls = 0;
  let repairPrompt = '';
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      calls += 1;
      if (calls === 2) repairPrompt = JSON.stringify(message.messages);
      const content = calls === 1
        ? `name: Unsafe staffing\nllm:\n  provider: modelmirror\n  model: fake-model\nsteps:\n  - id: final\n    role: agent-alpha\n    task: Assign Volunteer A-L to ten workdays.\n    output: final_output\n    acceptance: Every shift names Volunteer A-L.`
        : `name: Safe staffing template\nllm:\n  provider: modelmirror\n  model: fake-model\nsteps:\n  - id: final\n    role: agent-alpha\n    task: Produce role slots and aggregate capacity only; mark every unconfirmed slot TBD and do not assign people to dates or shifts.\n    output: final_output\n    acceptance: The reusable template contains only role slots, aggregate capacity, and explicit TBD markers.`;
      input.write(`${JSON.stringify({
        protocol: AGENCY_BRIDGE_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id,
        ok: true,
        result: { content, finish_reason: 'stop', usage: { input_tokens: 10, output_tokens: 10 } },
      })}\n`);
    }
  });

  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request', id: 'staffing-repair', method: 'compose',
    params: {
      goal: 'Design a reusable two-week template, but 不执行真实排班。', model_id: 'fake-model',
      agents: [{
        id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Operations',
        description: 'Operations planning expert.', system_prompt: 'Create safe templates.',
      }],
      mode: 'auto', max_agents: 1, temperature: 0.2, allow_hitl: false,
    },
  }, channel);

  assert.equal(calls, 2);
  assert.equal((result.validation as Record<string, unknown>).valid, true);
  assert.equal(result.repair_used, true);
  assert.match(repairPrompt, /remove every individual alias/);
  assert.match(repairPrompt, /prohibition on real staffing/);
  input.end();
  output.end();
});

test('pinned planning exposes only the fixed expert catalog to the model', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let buffer = '';
  let planningPrompt = '';
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      planningPrompt = JSON.stringify(message.messages);
      input.write(`${JSON.stringify({
        protocol: AGENCY_BRIDGE_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id,
        ok: true,
        result: {
          content: `name: Fixed team\nllm:\n  provider: modelmirror\n  model: fake-model\nsteps:\n  - id: final\n    role: agent-alpha\n    task: Write the final brief.\n    acceptance: The brief is complete.\n    output: final_output`,
          finish_reason: 'stop',
          usage: { input_tokens: 10, output_tokens: 10 },
        },
      })}\n`);
    }
  });

  const result = await handleRequest({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id: 'pinned-catalog',
    method: 'compose',
    params: {
      goal: 'Write a fixed-team brief.',
      model_id: 'fake-model',
      agents: [
        {
          id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: 'Product',
          description: 'PINNED_ALPHA_DESCRIPTION', system_prompt: 'Plan product work.',
        },
        {
          id: 'agent-beta', path: 'agent-beta', name: 'Beta', department: 'Risk',
          description: 'UNPINNED_BETA_DESCRIPTION', system_prompt: 'Review risk.',
        },
      ],
      mode: 'pinned', pinned_agent_ids: ['agent-alpha'], max_agents: 1,
      temperature: 0.2, allow_hitl: false,
    },
  }, channel);

  assert.equal((result.validation as Record<string, unknown>).valid, true);
  assert.match(planningPrompt, /PINNED_ALPHA_DESCRIPTION/);
  assert.doesNotMatch(planningPrompt, /UNPINNED_BETA_DESCRIPTION/);
  assert.match(planningPrompt, /Do not invent a word-count or character-count limit/);
  assert.match(planningPrompt, /never require a later step to remove, replace, fill, or resolve those markers/);
  assert.match(planningPrompt, /Approval accepts or rejects the draft; it does not supply missing facts/);
  assert.match(planningPrompt, /Never silently qualify a user duration with working\/calendar days/);
  assert.match(planningPrompt, /Explicit approval of a visible draft may approve its policy choices/);
  assert.match(planningPrompt, /not as a demand that every human observation, conversation, or physical check/);
  assert.match(planningPrompt, /Never use user_input, goal, or _loop_iteration as a step output variable/);
  assert.match(planningPrompt, /Host execution constraints/);
  input.end();
  output.end();
});
