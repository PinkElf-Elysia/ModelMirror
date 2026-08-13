import assert from 'node:assert/strict';
import test from 'node:test';
import { PassThrough } from 'node:stream';

import { JsonlChannel } from '../src/channel.js';
import { executeRequest } from '../src/execution.js';
import {
  AGENCY_EXECUTION_PROTOCOL,
  AgencyBridgeError,
  type BridgeRequest,
} from '../src/protocol.js';

const agents = [
  {
    id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: '研究',
    description: 'research', system_prompt: 'You are Alpha.',
  },
  {
    id: 'agent-beta', path: 'agent-beta', name: 'Beta', department: '产品',
    description: 'delivery', system_prompt: 'You are Beta.',
  },
];

function request(
  workflow: Record<string, unknown>,
  skills: Array<Record<string, unknown>> = [],
  resume?: Record<string, unknown>,
): BridgeRequest {
  return {
    protocol: AGENCY_EXECUTION_PROTOCOL,
    type: 'request',
    id: 'execution-test',
    method: 'execute',
    params: {
      goal: 'Build a reliable launch recommendation.',
      model_id: 'fake-model',
      agents,
      skills,
      workflow,
      ...(resume ? { resume } : {}),
    },
  };
}

test('v2 execution correlates out-of-order fan-out responses and verifies the sink', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const messages: Record<string, unknown>[] = [];
  const firstBatch: Record<string, unknown>[] = [];
  let outputBuffer = '';

  const respond = (message: Record<string, unknown>): void => {
    const rawMessages = message.messages as Array<Record<string, unknown>>;
    const system = String(rawMessages?.[0]?.content ?? '');
    const content = system.includes('reviewer') || system.includes('验收员')
      ? '{"pass":true,"failed":[]}'
      : `result-for-${message.request_id}`;
    input.write(`${JSON.stringify({
      protocol: AGENCY_EXECUTION_PROTOCOL,
      type: 'model_response',
      id: message.id,
      request_id: message.request_id,
      ok: true,
      result: { content, usage: { input_tokens: 2, output_tokens: 3 } },
    })}\n`);
  };

  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      messages.push(message);
      if (message.type !== 'model_request') continue;
      if (firstBatch.length < 2) {
        firstBatch.push(message);
        if (firstBatch.length === 2) {
          respond(firstBatch[1]);
          respond(firstBatch[0]);
        }
      } else {
        respond(message);
      }
    }
  });

  const result = await executeRequest(request({
    name: 'fan-out fan-in',
    steps: [
      { id: 'research', role: 'agent-alpha', task: 'Research {{user_input}}', output: 'research_output', depends_on: [] },
      { id: 'risk', role: 'agent-beta', task: 'Assess {{user_input}}', output: 'risk_output', depends_on: [] },
      {
        id: 'synthesis', role: 'agent-beta', depends_on: ['research', 'risk'],
        task: 'Use {{research_output}} and {{risk_output}}', acceptance: 'Must be actionable', output: 'final_output',
      },
    ],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.quality_status, 'passed');
  assert.match(
    String(result.final_output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  const sinkStep = (result.steps as Array<Record<string, unknown>>)
    .find(step => step.id === 'synthesis');
  assert.match(
    String(sinkStep?.output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  assert.equal(result.model_calls, 4);
  assert.deepEqual(result.usage, { input_tokens: 8, output_tokens: 12 });
  const modelRequest = messages.find(message => message.type === 'model_request');
  const modelRequestMessages = modelRequest?.messages as Array<Record<string, unknown>>;
  assert.equal(modelRequest?.timeout_seconds, 240);
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /complete deliverable within 1,600 output tokens/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /acceptance criterion requesting a missing fact does not authorize fabrication/,
  );
  assert.match(
    String(modelRequestMessages?.[1]?.content ?? ''),
    /no more than 1,500 Chinese characters or 900 English words/,
  );
  const verificationRequest = messages.find(message => message.json_response === true);
  assert.equal(verificationRequest?.temperature, 0);
  assert.ok(Number(verificationRequest?.max_tokens) >= 1600);
  assert.ok(Number(verificationRequest?.max_tokens) <= 2000);
  const verificationMessages = verificationRequest?.messages as Array<Record<string, unknown>>;
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /An upstream step is not evidence that it was user-provided/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Do not silently add mandatory prerequisites/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Authoritative original user request/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Build a reliable launch recommendation\./,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /Never claim that a literal label, value, fact, or section is absent/,
  );
  assert.ok(messages.some(message => (
    message.type === 'event'
    && (message.event as Record<string, unknown>).event === 'agency.run.completed'
  )));
  const completedEvent = messages
    .filter(message => message.type === 'event')
    .map(message => message.event as Record<string, unknown>)
    .find(event => event.event === 'agency.run.completed');
  assert.match(
    String(completedEvent?.final_output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  channel.close();
});

test('v2 execution deterministically reworks a sink that exceeds an explicit character limit', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const modelRequests: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      modelRequests.push(message);
      const messages = message.messages as Array<Record<string, unknown>>;
      const system = String(messages?.[0]?.content ?? '');
      const user = String(messages?.[1]?.content ?? '');
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : user.includes('system fact boundary adds')
          ? '短评审包'
          : 'x'.repeat(1_400);
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 2, output_tokens: 3 } },
      })}\n`);
    }
  });

  const result = await executeRequest(request({
    name: 'bounded final',
    steps: [{
      id: 'final', role: 'agent-alpha', task: 'Write a compact review.',
      acceptance: 'The deliverable must contain no more than 1200 characters.', output: 'final_output', depends_on: [],
    }],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.quality_status, 'passed');
  assert.equal(result.model_calls, 3);
  assert.equal(modelRequests.filter(message => message.json_response !== true).length, 2);
  assert.equal(modelRequests.filter(message => message.json_response === true).length, 1);
  assert.ok(modelRequests.some(message => {
    const messages = message.messages as Array<Record<string, unknown>>;
    return String(messages?.[1]?.content ?? '').includes('system fact boundary adds');
  }));
  const finalStep = (result.steps as Array<Record<string, unknown>>)
    .find(step => step.id === 'final');
  assert.deepEqual(finalStep?.verification, { pass: true, failed: [], reworked: true });
  assert.match(String(finalStep?.output ?? ''), /短评审包/);
  channel.close();
});

test('v2 execution resumes from completed steps without billing them again', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const requestedSystems: string[] = [];
  const requestedUsers: string[] = [];
  const events: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'event') events.push(message.event as Record<string, unknown>);
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      requestedSystems.push(system);
      requestedUsers.push(String(rawMessages?.[1]?.content ?? ''));
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : 'new-final-output';
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 5, output_tokens: 7 } },
      })}\n`);
    }
  });

  const workflow = {
    name: 'resume',
    steps: [
      { id: 'research', role: 'agent-alpha', task: 'Research', output: 'research_output', depends_on: [] },
      {
        id: 'synthesis', role: 'agent-beta', task: 'Use {{research_output}}',
        acceptance: 'Must be actionable', output: 'final_output', depends_on: ['research'],
      },
    ],
  };
  const paidResearchOutput = [
    'CONFIRMED_HEAD: preserve the original user constraints.',
    'ordinary supporting detail\n'.repeat(700),
    '## Risks and TBD\nRISK_TBD_SENTINEL: budget and owner remain pending confirmation.',
    'additional supporting detail\n'.repeat(500),
    'CONFIRMED_TAIL: rollback remains mandatory.',
  ].join('\n');
  const result = await executeRequest(request(workflow, [], {
    source_task_id: 'agency_dag_previous',
    prior_model_calls: 1,
    prior_usage: { input_tokens: 11, output_tokens: 13 },
    completed_steps: [{
      task_id: 'research',
      output_variable: 'research_output',
      output: paidResearchOutput,
    }],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.model_calls, 3);
  assert.deepEqual(result.usage, { input_tokens: 21, output_tokens: 27 });
  assert.equal(requestedSystems.length, 2);
  assert.match(requestedUsers[0], /ModelMirror bounded dependency excerpt/);
  assert.match(requestedUsers[0], /CONFIRMED_HEAD/);
  assert.match(requestedUsers[0], /RISK_TBD_SENTINEL/);
  assert.match(requestedUsers[0], /CONFIRMED_TAIL/);
  assert.ok(requestedUsers[0].length < paidResearchOutput.length / 2);
  assert.ok(events.some(event => (
    event.task_id === 'research'
    && event.reused === true
    && event.output === paidResearchOutput
  )));
  assert.ok(events.some(event => event.event === 'agency.run.completed' && event.resumed_from_task_id === 'agency_dag_previous'));
  channel.close();
});

test('v2 execution reports token-limit truncation as an actionable error', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const events: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'event') events.push(message.event as Record<string, unknown>);
      if (message.type !== 'model_request') continue;
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: {
          content: 'partial output',
          finish_reason: 'length',
          usage: { input_tokens: 2, output_tokens: 4096 },
        },
      })}\n`);
    }
  });

  await assert.rejects(
    executeRequest(request({
      name: 'truncated',
      steps: [{
        id: 'final', role: 'agent-alpha', task: 'Write', output: 'final_output',
        acceptance: 'Complete', depends_on: [],
      }],
    }), channel),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'model_output_truncated',
  );
  const failedEvent = events.find(event => event.event === 'agency.run.failed');
  assert.deepEqual(failedEvent?.usage, { input_tokens: 2, output_tokens: 4096 });
  channel.close();
});

test('v2 execution rejects unsupported and multi-sink workflows before model calls', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  await assert.rejects(
    executeRequest(request({
      name: 'invalid',
      steps: [
        {
          id: 'one', role: 'agent-alpha', task: 'one', output: 'one_output',
          acceptance: 'done', depends_on: [], skill: 'forbidden',
        },
        {
          id: 'two', role: 'agent-beta', task: 'two', output: 'two_output',
          acceptance: 'done', depends_on: [],
        },
      ],
    }), channel),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_plan_invalid',
  );
  channel.close();
});

test('v2 execution injects a host-approved method Skill without enabling tools', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let observedSystem = '';
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      observedSystem += system;
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : 'method-aware-result';
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 1, output_tokens: 1 } },
      })}\n`);
    }
  });

  const result = await executeRequest(request({
    name: 'method skill',
    steps: [{
      id: 'analysis', role: 'agent-alpha', task: 'Analyze {{user_input}}',
      acceptance: 'Must be structured', output: 'final_output', depends_on: [],
      skills: ['data-analysis'],
    }],
  }, [{
    skill_id: 'data-analysis',
    name: 'Data Analysis',
    description: 'A bounded analysis method.',
    body: 'METHOD_SKILL_SENTINEL: verify evidence before conclusions.',
    digest: 'a'.repeat(64),
  }]), channel);

  assert.equal(result.success, true);
  assert.match(observedSystem, /METHOD_SKILL_SENTINEL/);
  await assert.rejects(
    executeRequest(request({
      name: 'unknown method',
      steps: [{
        id: 'analysis', role: 'agent-alpha', task: 'Analyze', acceptance: 'Done',
        output: 'final_output', depends_on: [], skills: ['unknown-skill'],
      }],
    }), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_plan_invalid',
  );
  channel.close();
});
