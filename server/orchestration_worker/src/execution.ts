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
import type { SkillDefinition } from '../../vendor/agency-orchestrator/src/skills/loader.js';

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
const FINAL_RELIABILITY_ACCEPTANCE = `

ModelMirror final reliability requirements (all are mandatory):
1. A confirmed fact must come from the original user input. Recommendations, thresholds, assumptions, and values introduced by upstream steps must never be presented as confirmed facts.
2. Every unprovided number, date, name, vendor, interface, infrastructure choice, target, prerequisite, or policy must be explicitly labeled as a proposal, assumption, or TBD/pending confirmation. An upstream step is not evidence that it was user-provided.
3. Preserve every material user constraint and prohibition in the final recommendation. Do not weaken a human-approval boundary, data boundary, budget ceiling, scope limit, or rollback requirement.
4. Do not silently add mandatory prerequisites. Any new prerequisite must be labeled as a proposal requiring confirmation and must not contradict the original request.
5. Keep confirmed facts, derived conclusions, proposals, and TBD items visibly distinct, with source step IDs for derived recommendations.`;
const FACT_BOUNDARY_MARKER = 'ModelMirror fact and decision boundary';
const FACT_BOUNDARY_ZH = `> **ModelMirror 事实与决策边界（系统附加）**：只有“已确认事实”中可直接追溯到用户原始输入的内容才是事实。其余目标、阈值、预算分配、技术选择、角色、前置条件和政策均为待责任方确认的建议，不是已批准的决定。它们不得覆盖用户原始限制，也不授权自动批准、自动拒绝、写入现有系统或执行其他外部变更。`;
const FACT_BOUNDARY_EN = `> **ModelMirror fact and decision boundary (system-applied)**: Only items under confirmed facts that are directly traceable to the original user input are facts. Every other target, threshold, budget allocation, technical choice, role, prerequisite, and policy is a proposal pending confirmation, not an approved decision. It cannot override the user's original constraints or authorize automatic approval, rejection, writes to existing systems, or other external changes.`;
const SINK_DEPENDENCY_CONTEXT_BUDGET = 15_000;
const SINK_DEPENDENCY_VALUE_MAX = 4_200;
const SINK_CONTEXT_MARKER = '[ModelMirror bounded dependency excerpt: the complete step output remains stored in the run history.]';
const AUTHORITATIVE_INPUT_BLOCK = `Authoritative original user request / 权威原始用户输入（唯一事实源）:
{{user_input}}

Use the original request above to distinguish confirmed facts from derived recommendations. Dependency outputs are analysis, not evidence that a value was user-provided.`;
const FORBIDDEN_STEP_FIELDS = [
  'condition',
  'loop',
  'skill',
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

interface MethodSkillDefinition extends SkillDefinition {
  id: string;
  digest: string;
}

interface ResumeState {
  sourceTaskId: string;
  completedStepIds: Set<string>;
  restoredStepMeta: Map<string, Partial<StepResult>>;
  inputs: Map<string, string>;
  priorModelCalls: number;
  priorUsage: { input_tokens: number; output_tokens: number };
  revision?: {
    targetTaskId: string;
    feedback: string;
    previousOutput: string;
  };
}

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
  skills: MethodSkillDefinition[],
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
  const knownSkills = new Set(skills.map(skill => skill.id));
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
    const rawSkills = item.skills ?? [];
    if (!Array.isArray(rawSkills) || rawSkills.length > 1) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Step "${id}" method Skills are invalid.`,
      );
    }
    const methodSkills = rawSkills.map(value => String(value).trim());
    if (
      methodSkills.some(value => !value || !knownSkills.has(value))
      || new Set(methodSkills).size !== methodSkills.length
    ) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Step "${id}" references an unavailable method Skill.`,
      );
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
      skills: methodSkills,
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
  sink.task = `${AUTHORITATIVE_INPUT_BLOCK}\n\n${sink.task.replace(
    /\{\{\s*(?:user_input|goal)\s*\}\}/g,
    '[authoritative original input above]',
  )}`;
  sink.acceptance = `${sink.acceptance}${FINAL_RELIABILITY_ACCEPTANCE}`;
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
      timeout: 240_000,
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

function parseSkills(value: unknown): MethodSkillDefinition[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 3) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      'Execution method Skill catalog is invalid.',
    );
  }
  const seen = new Set<string>();
  return value.map(raw => {
    const item = asObject(raw, 'agency_execution_plan_invalid');
    const id = stringField(item, 'skill_id', 160);
    if (seen.has(id)) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Duplicate method Skill "${id}".`,
      );
    }
    seen.add(id);
    const digest = stringField(item, 'digest', 64);
    if (!/^[0-9a-f]{64}$/.test(digest)) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Method Skill "${id}" digest is invalid.`,
      );
    }
    return {
      id,
      digest,
      name: stringField(item, 'name', 200),
      description: typeof item.description === 'string'
        ? item.description.trim().slice(0, 2_000)
        : '',
      body: stringField(item, 'body', 20_000),
    };
  });
}

function applyFactBoundary(output: string, goal: string): string {
  if (output.includes(FACT_BOUNDARY_MARKER) || output.includes('ModelMirror 事实与决策边界')) {
    return output;
  }
  const boundary = /[\u3400-\u9fff]/.test(goal) ? FACT_BOUNDARY_ZH : FACT_BOUNDARY_EN;
  return `${boundary}\n\n${output}`;
}

function compactDependencyOutput(value: string, budget: number): string {
  if (value.length <= budget) return value;
  const markerBudget = SINK_CONTEXT_MARKER.length + 80;
  const contentBudget = Math.max(600, budget - markerBudget);
  const headBudget = Math.floor(contentBudget * 0.28);
  const tailBudget = Math.floor(contentBudget * 0.22);
  const signalBudget = contentBudget - headBudget - tailBudget;
  const head = value.slice(0, headBudget).trimEnd();
  const tail = value.slice(-tailBudget).trimStart();
  const middle = value.slice(headBudget, value.length - tailBudget);
  const signalPattern = /^\s{0,3}(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|[|>])|(?:事实|结论|建议|风险|指标|预算|约束|待确认|验收|回退|冲突|依赖|决策|TBD|fact|conclusion|recommend|risk|metric|budget|constraint|pending|acceptance|rollback|conflict|dependency|decision)/i;
  const signalLines: string[] = [];
  let signalLength = 0;
  for (const rawLine of middle.split(/\r?\n/)) {
    const line = rawLine.trimEnd().slice(0, 600);
    if (!line.trim() || !signalPattern.test(line)) continue;
    if (signalLength + line.length + 1 > signalBudget) break;
    signalLines.push(line);
    signalLength += line.length + 1;
  }
  const signal = signalLines.length > 0
    ? signalLines.join('\n')
    : middle.slice(0, signalBudget).trim();
  return [
    SINK_CONTEXT_MARKER,
    head,
    '[... non-signal detail omitted from this synthesis view ...]',
    signal,
    '[... complete source remains available in the upstream step result ...]',
    tail,
  ].filter(Boolean).join('\n');
}

function sinkTemplateContext(
  workflow: WorkflowDefinition,
  sinkId: string,
): (node: DAGNode, context: ReadonlyMap<string, string>) => Map<string, string> {
  const stepsById = new Map(workflow.steps.map(step => [step.id, step]));
  const sink = stepsById.get(sinkId)!;
  const outputVariables = (sink.depends_on ?? [])
    .map(dependency => stepsById.get(dependency)?.output)
    .filter((value): value is string => Boolean(value));
  return (node, context) => {
    if (node.step.id !== sinkId || outputVariables.length === 0) return context as Map<string, string>;
    const values = outputVariables.map(variable => ({
      variable,
      value: context.get(variable) ?? '',
    }));
    const totalLength = values.reduce((total, item) => total + item.value.length, 0);
    if (totalLength <= SINK_DEPENDENCY_CONTEXT_BUDGET) return context as Map<string, string>;
    const bounded = new Map(context);
    let remaining = SINK_DEPENDENCY_CONTEXT_BUDGET;
    for (let index = 0; index < values.length; index += 1) {
      const remainingValues = values.length - index;
      const fairShare = Math.max(1_200, Math.floor(remaining / remainingValues));
      const budget = Math.min(values[index].value.length, SINK_DEPENDENCY_VALUE_MAX, fairShare);
      bounded.set(values[index].variable, compactDependencyOutput(values[index].value, budget));
      remaining = Math.max(0, remaining - budget);
    }
    return bounded;
  };
}

function finalOutputValidator(
  sinkId: string,
  goal: string,
): (node: DAGNode, output: string, acceptance: string) => {
  pass: boolean;
  failed: { criterion: string; why: string }[];
} | null {
  return (node, output, acceptance) => {
    if (node.step.id !== sinkId) return null;
    const match = acceptance.match(
      /(?:不超过|最多|上限(?:为)?|≤)\s*([0-9][0-9,]*)\s*(?:个)?(?:中文)?字符|(?:no more than|at most|maximum(?: of)?)\s*([0-9][0-9,]*)\s*(?:Chinese\s+)?characters?/i,
    );
    if (!match) return null;
    const rawLimit = match[1] ?? match[2];
    const limit = Number(rawLimit.replaceAll(',', ''));
    if (!Number.isSafeInteger(limit) || limit < 1) return null;
    const boundary = /[\u3400-\u9fff]/.test(goal) ? FACT_BOUNDARY_ZH : FACT_BOUNDARY_EN;
    const boundaryReserve = output.includes(FACT_BOUNDARY_MARKER)
      || output.includes('ModelMirror 事实与决策边界')
      ? 0
      : Array.from(`${boundary}\n\n`).length;
    const bodyLength = Array.from(output.trim()).length;
    const finalLength = bodyLength + boundaryReserve;
    if (finalLength <= limit) return { pass: true, failed: [] };
    const criterion = acceptance
      .split(/\r?\n/)
      .find(line => line.includes(match[0]))
      ?.trim() || match[0];
    const zh = /[\u3400-\u9fff]/.test(criterion);
    return {
      pass: false,
      failed: [{
        criterion,
        why: zh
          ? `正文 ${bodyLength} 个字符，系统事实边界另占 ${boundaryReserve} 个字符，最终共 ${finalLength} 个字符；正文最多 ${Math.max(1, limit - boundaryReserve)} 个字符。`
          : `The body has ${bodyLength} characters and the system fact boundary adds ${boundaryReserve}, for ${finalLength} total; keep the body within ${Math.max(1, limit - boundaryReserve)} characters.`,
      }],
    };
  };
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

function skillResolver(skills: MethodSkillDefinition[]): (name: string) => SkillDefinition | null {
  const definitions = new Map(skills.map(skill => [skill.id, skill]));
  return (name: string): SkillDefinition | null => definitions.get(name) ?? null;
}

function finiteCount(value: unknown, maximum: number, field: string): number {
  const parsed = Number(value ?? 0);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > maximum) {
    throw new AgencyBridgeError('agency_execution_plan_invalid', `${field} is invalid.`);
  }
  return parsed;
}

function executionStringField(
  source: Record<string, unknown>,
  name: string,
  maxLength: number,
  code: string,
): string {
  const value = typeof source[name] === 'string' ? source[name].trim() : '';
  if (!value || value.length > maxLength) {
    throw new AgencyBridgeError(code, `${name} is invalid.`);
  }
  return value;
}

function parseCompletedSteps(
  value: unknown,
  workflow: WorkflowDefinition,
  goal: string,
  field: string,
  minimum: number,
  errorCode = 'agency_execution_plan_invalid',
): Pick<ResumeState, 'completedStepIds' | 'restoredStepMeta' | 'inputs'> {
  if (!Array.isArray(value) || value.length < minimum || value.length > MAX_EXECUTION_STEPS) {
    throw new AgencyBridgeError(
      errorCode,
      `${field} must contain ${minimum}-${MAX_EXECUTION_STEPS} completed steps.`,
    );
  }
  const stepsById = new Map(workflow.steps.map(step => [step.id, step]));
  const completedStepIds = new Set<string>();
  const restoredStepMeta = new Map<string, Partial<StepResult>>();
  const inputs = new Map<string, string>([['user_input', goal], ['goal', goal]]);
  for (const rawCompleted of value) {
    const completed = asObject(rawCompleted, errorCode);
    const taskId = executionStringField(completed, 'task_id', 64, errorCode);
    if (completedStepIds.has(taskId)) {
      throw new AgencyBridgeError(errorCode, `${field} step "${taskId}" is duplicated.`);
    }
    const step = stepsById.get(taskId);
    if (!step?.output) {
      throw new AgencyBridgeError(errorCode, `${field} step "${taskId}" is not in the workflow.`);
    }
    const outputVariable = executionStringField(completed, 'output_variable', 128, errorCode);
    if (outputVariable !== step.output) {
      throw new AgencyBridgeError(errorCode, `${field} output for step "${taskId}" does not match the workflow.`);
    }
    const output = executionStringField(completed, 'output', MAX_EXECUTION_OUTPUT_BYTES, errorCode);
    if (Buffer.byteLength(output, 'utf8') > MAX_EXECUTION_OUTPUT_BYTES) {
      throw new AgencyBridgeError(
        errorCode,
        `${field} output for step "${taskId}" exceeds the 64 KiB limit.`,
      );
    }
    completedStepIds.add(taskId);
    inputs.set(outputVariable, output);
    restoredStepMeta.set(taskId, {
      agentName: typeof completed.agent_name === 'string' ? completed.agent_name.slice(0, 200) : undefined,
      agentEmoji: typeof completed.agent_emoji === 'string' ? completed.agent_emoji.slice(0, 16) : undefined,
      acceptance: typeof completed.acceptance === 'string' ? completed.acceptance.slice(0, 4_000) : undefined,
    });
  }
  for (const taskId of completedStepIds) {
    const step = stepsById.get(taskId)!;
    const missingDependency = (step.depends_on ?? []).find(dependency => !completedStepIds.has(dependency));
    if (missingDependency) {
      throw new AgencyBridgeError(
        errorCode,
        `${field} step "${taskId}" is missing completed dependency "${missingDependency}".`,
      );
    }
  }
  return { completedStepIds, restoredStepMeta, inputs };
}

function parseResume(value: unknown, workflow: WorkflowDefinition, goal: string): ResumeState | null {
  if (value === undefined || value === null) return null;
  const raw = asObject(value, 'agency_execution_plan_invalid');
  const sourceTaskId = stringField(raw, 'source_task_id', 200);
  const priorModelCalls = finiteCount(raw.prior_model_calls, MAX_EXECUTION_MODEL_CALLS, 'prior_model_calls');
  const rawUsage = asObject(raw.prior_usage ?? {}, 'agency_execution_plan_invalid');
  const priorUsage = {
    input_tokens: finiteCount(rawUsage.input_tokens, Number.MAX_SAFE_INTEGER, 'prior_usage.input_tokens'),
    output_tokens: finiteCount(rawUsage.output_tokens, Number.MAX_SAFE_INTEGER, 'prior_usage.output_tokens'),
  };
  return {
    sourceTaskId,
    ...parseCompletedSteps(raw.completed_steps, workflow, goal, 'resume.completed_steps', 1),
    priorModelCalls,
    priorUsage,
  };
}

function parseRevision(value: unknown, workflow: WorkflowDefinition, goal: string): ResumeState | null {
  if (value === undefined || value === null) return null;
  const raw = asObject(value, 'agency_execution_revision_invalid');
  const sourceTaskId = executionStringField(raw, 'source_task_id', 200, 'agency_execution_revision_invalid');
  const targetTaskId = executionStringField(raw, 'target_task_id', 64, 'agency_execution_revision_invalid');
  const feedback = executionStringField(raw, 'feedback', 4_000, 'agency_execution_revision_invalid');
  if (feedback.length < 10) {
    throw new AgencyBridgeError('agency_execution_revision_invalid', 'revision.feedback must contain 10-4000 characters.');
  }
  const previousOutput = executionStringField(
    raw,
    'previous_output',
    MAX_EXECUTION_OUTPUT_BYTES,
    'agency_execution_revision_invalid',
  );
  if (Buffer.byteLength(previousOutput, 'utf8') > MAX_EXECUTION_OUTPUT_BYTES) {
    throw new AgencyBridgeError('agency_execution_revision_invalid', 'revision.previous_output exceeds the 64 KiB limit.');
  }
  const target = workflow.steps.find(step => step.id === targetTaskId);
  if (!target) {
    throw new AgencyBridgeError('agency_execution_revision_invalid', `Revision target "${targetTaskId}" is not in the workflow.`);
  }
  const restored = parseCompletedSteps(
    raw.completed_steps ?? [],
    workflow,
    goal,
    'revision.completed_steps',
    0,
    'agency_execution_revision_invalid',
  );
  const affected = new Set<string>([targetTaskId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const step of workflow.steps) {
      if (!affected.has(step.id) && (step.depends_on ?? []).some(id => affected.has(id))) {
        affected.add(step.id);
        changed = true;
      }
    }
  }
  const invalidRestored = [...restored.completedStepIds].find(taskId => affected.has(taskId));
  if (invalidRestored) {
    throw new AgencyBridgeError(
      'agency_execution_revision_invalid',
      `Revision cannot restore affected step "${invalidRestored}".`,
    );
  }
  return {
    sourceTaskId,
    ...restored,
    priorModelCalls: 0,
    priorUsage: { input_tokens: 0, output_tokens: 0 },
    revision: { targetTaskId, feedback, previousOutput },
  };
}

function stepEvent(
  node: DAGNode,
  connector: ExecutionBridgeConnector,
  resume: ResumeState | null,
): ExecutionEvent {
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
    method_skill_ids: node.step.skills ?? [],
    verification: node.verification ?? resume?.restoredStepMeta.get(node.step.id)?.verification,
    reused: Boolean(resume?.completedStepIds.has(node.step.id)),
    usage: node.tokenUsage
      ? { input_tokens: node.tokenUsage.input, output_tokens: node.tokenUsage.output }
      : { input_tokens: 0, output_tokens: 0 },
    model_calls: connector.calls,
    cumulative_usage: { ...connector.usage },
  };
}

function failedExecutionError(failed: StepResult | undefined): AgencyBridgeError {
  const error = String(failed?.error || '');
  const knownCodes = [
    'model_output_truncated',
    'model_response_empty',
    'model_response_invalid',
    'model_gateway_timeout',
    'model_gateway_failed',
    'model_gateway_quota_exceeded',
    'agency_execution_timeout',
    'agency_execution_budget_exceeded',
  ];
  const code = knownCodes.find(candidate => error.includes(candidate));
  return code
    ? new AgencyBridgeError(code, error)
    : new AgencyBridgeError('agency_execution_step_failed', 'Agency DAG did not produce a final output. Retry only the failed and downstream steps.');
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
  const skills = parseSkills(params.skills);
  const { workflow, sinkId } = parseWorkflow(params.workflow, agents, skills, modelId);
  if (params.resume !== undefined && params.revision !== undefined) {
    throw new AgencyBridgeError('agency_execution_plan_invalid', 'Execution cannot combine resume and revision.');
  }
  const resume = parseResume(params.resume, workflow, goal)
    ?? parseRevision(params.revision, workflow, goal);
  const router = new ModelResponseRouter(channel, request.id);
  const connector = new ExecutionBridgeConnector(router, request.id, modelId, resume ? {
    calls: resume.priorModelCalls,
    usage: resume.priorUsage,
  } : undefined);
  const stepResults: StepResult[] = [];
  emit(channel, request.id, {
    event: 'agency.run.started',
    status: 'running',
    step_count: workflow.steps.length,
    model_id: modelId,
    resumed_from_task_id: resume?.revision ? undefined : resume?.sourceTaskId,
    reused_task_ids: resume ? [...resume.completedStepIds] : [],
    revision_parent_task_id: resume?.revision ? resume.sourceTaskId : undefined,
    revision_target_task_id: resume?.revision?.targetTaskId,
    model_calls: connector.calls,
    cumulative_usage: { ...connector.usage },
  });
  try {
    const result = await executeDAG(buildDAG(workflow), {
      connector,
      agentsDir: '',
      llmConfig: workflow.llm,
      concurrency: MAX_EXECUTION_CONCURRENCY,
      inputs: resume?.inputs ?? new Map([['user_input', goal], ['goal', goal]]),
      verify: true,
      skipStepIds: resume?.completedStepIds,
      restoredStepMeta: resume?.restoredStepMeta,
      feedback: resume?.revision ? {
        stepId: resume.revision.targetTaskId,
        text: resume.revision.feedback,
        previousOutput: resume.revision.previousOutput,
      } : undefined,
      resolveAgent: agentResolver(agents),
      resolveSkill: skillResolver(skills),
      prepareTemplateContext: sinkTemplateContext(workflow, sinkId),
      validateOutput: finalOutputValidator(sinkId, goal),
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
        if (node.step.id === sinkId && node.status === 'completed' && node.result?.trim()) {
          node.result = applyFactBoundary(node.result, goal);
          const stored = stepResults.find(step => step.id === sinkId);
          if (stored) stored.output = node.result;
        }
        emit(channel, request.id, stepEvent(node, connector, resume));
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
      throw failedExecutionError(failed);
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
      resumed_from_task_id: resume?.revision ? undefined : resume?.sourceTaskId,
      reused_task_ids: resume ? [...resume.completedStepIds] : [],
      revision_parent_task_id: resume?.revision ? resume.sourceTaskId : undefined,
      revision_target_task_id: resume?.revision?.targetTaskId,
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
      resumed_from_task_id: resume?.revision ? undefined : resume?.sourceTaskId,
      reused_task_ids: resume ? [...resume.completedStepIds] : [],
      revision_parent_task_id: resume?.revision ? resume.sourceTaskId : undefined,
      revision_target_task_id: resume?.revision?.targetTaskId,
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
