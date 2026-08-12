import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import { executeDAG } from '../../vendor/agency-orchestrator/src/core/executor.js';
import { validateWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';
import type {
  AgentDefinition,
  DAGNode,
  StepDefinition,
  StepResult,
  WorkflowDefinition,
} from '../../vendor/agency-orchestrator/src/types.js';

import { JsonlChannel, ModelResponseRouter } from './channel.js';
import { ExecutionBridgeConnector } from './execution_connector.js';
import {
  AGENCY_EXECUTION_PROTOCOL,
  AGENCY_UPSTREAM_REVISION,
  AgencyBridgeError,
  BridgeRequest,
  MAX_EXECUTION_CONCURRENCY,
  MAX_EXECUTION_MODEL_CALLS,
  MAX_EXECUTION_OUTPUT_BYTES,
  MAX_EXECUTION_STEPS,
  asObject,
} from './protocol.js';
import { ExpertDefinition, parseExperts, stringField } from './service.js';

const STEP_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const VARIABLE_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const FORBIDDEN_STEP_FIELDS = [
  'condition',
  'loop',
  'skill',
  'skills',
  'tool',
  'tools',
  'mcp',
  'llm',
  'prompt',
  'provider',
  'api_key',
  'apiKey',
  'base_url',
  'baseUrl',
  'model',
  'max_tokens',
  'temperature',
  'timeout',
  'retry',
  'depends_on_mode',
] as const;

type ExecutionEvent = Record<string, unknown> & { event: string };

function emit(channel: JsonlChannel, requestId: string, event: ExecutionEvent): void {
  channel.write({
    protocol: AGENCY_EXECUTION_PROTOCOL,
    type: 'event',
    id: requestId,
    event,
  });
}

function parseWorkflow(
  value: unknown,
  agents: ExpertDefinition[],
  modelId: string,
): { workflow: WorkflowDefinition; sinkId: string } {
  const raw = asObject(value, 'agency_execution_plan_invalid');
  const rawSteps = raw.steps;
  if (!Array.isArray(rawSteps) || rawSteps.length < 1 || rawSteps.length > MAX_EXECUTION_STEPS) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      `Execution workflow must contain 1-${MAX_EXECUTION_STEPS} steps.`,
    );
  }
  const knownAgents = new Set(agents.map(agent => agent.id));
  const seenIds = new Set<string>();
  const seenOutputs = new Set<string>();
  const steps: StepDefinition[] = rawSteps.map((value, index) => {
    const item = asObject(value, 'agency_execution_plan_invalid');
    for (const field of FORBIDDEN_STEP_FIELDS) {
      if (item[field] !== undefined && item[field] !== null) {
        throw new AgencyBridgeError(
          'agency_execution_plan_invalid',
          `Step ${index + 1} uses unsupported field "${field}".`,
        );
      }
    }
    if (item.type !== undefined && item.type !== 'normal') {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Step ${index + 1} uses unsupported type.`,
      );
    }
    const id = stringField(item, 'id', 64);
    if (!STEP_ID_PATTERN.test(id) || seenIds.has(id)) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `Step id "${id}" is invalid or duplicated.`);
    }
    seenIds.add(id);
    const role = stringField(item, 'role', 160);
    if (!knownAgents.has(role)) {
      throw new AgencyBridgeError('unknown_agent', `Execution step references unknown expert "${role}".`);
    }
    const output = stringField(item, 'output', 128);
    if (!VARIABLE_PATTERN.test(output) || seenOutputs.has(output)) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Step output "${output}" is invalid or duplicated.`,
      );
    }
    seenOutputs.add(output);
    const dependsOn = item.depends_on ?? [];
    if (!Array.isArray(dependsOn)) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `Step "${id}" dependencies are invalid.`);
    }
    const dependencies = dependsOn.map(value => String(value).trim());
    if (dependencies.some(value => !value) || new Set(dependencies).size !== dependencies.length) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `Step "${id}" dependencies are duplicated or empty.`);
    }
    const acceptance = typeof item.acceptance === 'string' ? item.acceptance.trim() : '';
    if (acceptance.length > 4_000) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `Step "${id}" acceptance is too long.`);
    }
    return {
      id,
      role,
      name: typeof item.name === 'string' ? item.name.trim().slice(0, 200) || undefined : undefined,
      emoji: typeof item.emoji === 'string' ? item.emoji.slice(0, 16) : undefined,
      task: stringField(item, 'task', 20_000),
      acceptance: acceptance || undefined,
      output,
      depends_on: dependencies,
      type: 'normal',
    };
  });

  const stepIds = new Set(steps.map(step => step.id));
  const dependedOn = new Set<string>();
  for (const step of steps) {
    for (const dependency of step.depends_on ?? []) {
      if (!stepIds.has(dependency) || dependency === step.id) {
        throw new AgencyBridgeError(
          'agency_execution_plan_invalid',
          `Step "${step.id}" has an unknown or self dependency "${dependency}".`,
        );
      }
      dependedOn.add(dependency);
    }
  }
  const sinks = steps.filter(step => !dependedOn.has(step.id));
  if (sinks.length !== 1) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      'Execution workflow must have exactly one final sink step.',
    );
  }
  const sink = sinks[0];
  if (!sink.acceptance) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      `Final step "${sink.id}" must define acceptance criteria.`,
    );
  }
  for (const step of steps) step.verify = step.id === sink.id;

  const workflow: WorkflowDefinition = {
    name: typeof raw.name === 'string' ? raw.name.trim().slice(0, 200) || 'ModelMirror Expert Team' : 'ModelMirror Expert Team',
    description: typeof raw.description === 'string' ? raw.description.trim().slice(0, 2_000) : undefined,
    agents_dir: '',
    llm: {
      provider: 'modelmirror',
      model: modelId,
      max_tokens: 4096,
      temperature: 0.3,
      timeout: 180_000,
      retry: 0,
    },
    concurrency: MAX_EXECUTION_CONCURRENCY,
    verify: true,
    inputs: [{ name: 'user_input', required: true }],
    steps,
  };
  const errors = validateWorkflow(workflow);
  if (errors.length > 0) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      errors.slice(0, 6).join('; '),
    );
  }
  buildDAG(workflow);
  return { workflow, sinkId: sink.id };
}

function agentResolver(agents: ExpertDefinition[]): (rolePath: string) => AgentDefinition {
  const definitions = new Map<string, AgentDefinition>(agents.map(agent => [
    agent.id,
    {
      name: agent.name,
      description: agent.description,
      emoji: agent.emoji,
      rolePath: agent.id,
      systemPrompt: agent.system_prompt,
    },
  ]));
  return (rolePath: string): AgentDefinition => {
    const definition = definitions.get(rolePath);
    if (!definition) throw new AgencyBridgeError('unknown_agent', `Unknown execution expert "${rolePath}".`);
    return definition;
  };
}

function stepEvent(node: DAGNode): ExecutionEvent {
  const event = node.status === 'completed'
    ? 'agency.step.completed'
    : node.status === 'skipped'
      ? 'agency.step.skipped'
      : 'agency.step.failed';
  return {
    event,
    task_id: node.step.id,
    agent_id: node.step.role,
    status: node.status,
    output: node.result?.slice(0, MAX_EXECUTION_OUTPUT_BYTES),
    error: node.error?.slice(0, 4_000),
    acceptance: node.acceptance ?? node.step.acceptance,
    verification: node.verification,
    usage: node.tokenUsage
      ? { input_tokens: node.tokenUsage.input, output_tokens: node.tokenUsage.output }
      : { input_tokens: 0, output_tokens: 0 },
  };
}

export async function executeRequest(
  request: BridgeRequest,
  channel: JsonlChannel,
): Promise<Record<string, unknown>> {
  if (request.protocol !== AGENCY_EXECUTION_PROTOCOL || request.method !== 'execute') {
    throw new AgencyBridgeError('worker_method_invalid', 'Execution requires bridge protocol v2.');
  }
  const params = request.params;
  const goal = stringField(params, 'goal', 20_000);
  const modelId = stringField(params, 'model_id', 300);
  const agents = parseExperts(params.agents);
  const { workflow, sinkId } = parseWorkflow(params.workflow, agents, modelId);
  const router = new ModelResponseRouter(channel, request.id);
  const connector = new ExecutionBridgeConnector(router, request.id, modelId);
  const stepResults: StepResult[] = [];
  emit(channel, request.id, {
    event: 'agency.run.started',
    status: 'running',
    step_count: workflow.steps.length,
    model_id: modelId,
  });
  try {
    const result = await executeDAG(buildDAG(workflow), {
      connector,
      agentsDir: '',
      llmConfig: workflow.llm,
      concurrency: MAX_EXECUTION_CONCURRENCY,
      inputs: new Map([['user_input', goal]]),
      verify: true,
      resolveAgent: agentResolver(agents),
      stepResultsSink: stepResults,
      onBatchStart: nodes => emit(channel, request.id, {
        event: 'agency.batch.started',
        task_ids: nodes.map(node => node.step.id),
      }),
      onStepStart: node => emit(channel, request.id, {
        event: 'agency.step.started',
        task_id: node.step.id,
        agent_id: node.step.role,
        depends_on: node.step.depends_on ?? [],
        acceptance: node.step.acceptance,
        status: 'running',
      }),
      onStepComplete: node => {
        emit(channel, request.id, stepEvent(node));
        if (node.verification) {
          emit(channel, request.id, {
            event: 'agency.verification.completed',
            task_id: node.step.id,
            verification: node.verification,
          });
        }
      },
    });
    const sink = stepResults.find(step => step.id === sinkId);
    if (!result.success || !sink || sink.status !== 'completed' || !sink.output) {
      const failed = stepResults.find(step => step.status === 'failed');
      if (failed?.error?.includes('agency_execution_timeout')) {
        throw new AgencyBridgeError('agency_execution_timeout', failed.error);
      }
      if (failed?.error?.includes('agency_execution_budget_exceeded')) {
        throw new AgencyBridgeError('agency_execution_budget_exceeded', failed.error);
      }
      throw new AgencyBridgeError('agency_execution_step_failed', 'Agency DAG did not produce a final output.');
    }
    const qualityStatus = sink.verification
      ? sink.verification.pass ? 'passed' : 'failed'
      : 'unavailable';
    const warnings = qualityStatus === 'unavailable'
      ? ['Final acceptance verification was unavailable.']
      : qualityStatus === 'failed'
        ? ['Final output did not pass every acceptance criterion after one rework.']
        : [];
    const payload = {
      success: true,
      final_task_id: sinkId,
      final_output: sink.output,
      quality_status: qualityStatus,
      warnings,
      steps: stepResults,
      model_calls: connector.calls,
      usage: connector.usage,
      duration_ms: result.totalDuration,
      upstream_revision: AGENCY_UPSTREAM_REVISION,
    };
    emit(channel, request.id, {
      event: 'agency.run.completed',
      status: 'completed',
      final_task_id: sinkId,
      final_output: sink.output,
      quality_status: qualityStatus,
      warnings,
      model_calls: connector.calls,
      usage: connector.usage,
    });
    return payload;
  } catch (error) {
    emit(channel, request.id, {
      event: 'agency.run.failed',
      status: 'failed',
      error: error instanceof Error ? error.message.slice(0, 4_000) : String(error).slice(0, 4_000),
      model_calls: connector.calls,
      usage: connector.usage,
    });
    throw error;
  } finally {
    router.close();
  }
}

export function executionHealth(): Record<string, unknown> {
  return {
    enabled: true,
    protocol: AGENCY_EXECUTION_PROTOCOL,
    max_steps: MAX_EXECUTION_STEPS,
    max_concurrency: MAX_EXECUTION_CONCURRENCY,
    max_model_calls: MAX_EXECUTION_MODEL_CALLS,
    max_tokens_per_call: 4096,
    timeout_seconds: 900,
    max_output_bytes: MAX_EXECUTION_OUTPUT_BYTES,
  };
}
