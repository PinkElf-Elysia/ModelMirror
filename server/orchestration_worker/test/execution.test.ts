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

function request(workflow: Record<string, unknown>): BridgeRequest {
  return {
    protocol: AGENCY_EXECUTION_PROTOCOL,
    type: 'request',
    id: 'execution-test',
    method: 'execute',
    params: { goal: 'Build a reliable launch recommendation.', model_id: 'fake-model', agents, workflow },
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
  assert.equal(result.model_calls, 4);
  assert.deepEqual(result.usage, { input_tokens: 8, output_tokens: 12 });
  assert.ok(messages.some(message => (
    message.type === 'event'
    && (message.event as Record<string, unknown>).event === 'agency.run.completed'
  )));
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
