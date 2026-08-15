import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import {
  executeDAG,
  InteractionRequired,
  type InteractionRequest,
} from '../../vendor/agency-orchestrator/src/core/executor.js';
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
  AGENCY_HITL_PROTOCOL,
  AGENCY_UPSTREAM_REVISION,
  AgencyBridgeError,
  BridgeRequest,
  MAX_EXECUTION_CONCURRENCY,
  MAX_EXECUTION_MODEL_CALLS,
  MAX_EXECUTION_OUTPUT_BYTES,
  MAX_EXECUTION_OUTPUT_TOKENS,
  MAX_EXECUTION_STEPS,
  asObject,
} from './protocol.js';
import {
  ExpertDefinition,
  parseExperts,
  stringField,
  validatePlainTextTemplateReferences,
} from './service.js';
import {
  closedFactAcceptanceContract,
  closedFactTaskContract,
  normalizeClosedFactQna,
  closedListAcceptanceContract,
  closedListTaskContract,
} from './fact_contract.js';

const STEP_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const VARIABLE_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const FINAL_RELIABILITY_ACCEPTANCE = `

ModelMirror final reliability requirements (all are mandatory):
1. A confirmed external fact must come from the original user request or an explicitly resolved human_input step. An explicitly approved draft may supply approved policy decisions for finalization, but it does not prove external facts, and it never overrides the original request.
2. Every unprovided external fact, number, date, name, vendor, interface, infrastructure choice, or target must be labeled as a proposal, assumption, or TBD/pending confirmation. Policy choices visibly included in a draft that the user explicitly approved may be presented as approved decisions, while factual claims inside that draft remain derived analysis.
3. Preserve every material user constraint and prohibition in the final recommendation. Do not weaken a human-approval boundary, data boundary, budget ceiling, scope limit, or rollback requirement.
4. Do not silently add mandatory prerequisites outside an explicitly approved draft. Any other new prerequisite must be labeled as a proposal requiring confirmation and must not contradict the original request.
5. Keep confirmed facts, derived conclusions, proposals, and TBD items visibly distinct, with source step IDs for derived recommendations.
6. Do not infer cross-shift, cross-period, or per-person availability from an aggregate or per-shift headcount. Every staffing table must be internally consistent: listed role counts must equal the declared team or shift total, and reusable template rows must not be summed into a daily deployment without user-provided availability.
7. If a model-generated dependency repeats a confirmed duration with a different unit, the original user's exact unit wins. For the same requirement, copy the original unit exactly; a different unit may appear only as a separately labeled proposal/TBD.`;
const FACT_BOUNDARY_MARKER = 'ModelMirror fact and decision boundary';
const FACT_BOUNDARY_ZH = `> **ModelMirror 事实与决策边界（系统附加）**：只有可直接追溯到用户原始请求或已解决人工输入节点的内容才是已确认外部事实。用户明确批准的草案可作为已批准政策决策的来源，但不能证明草案中的外部事实，也不能覆盖用户原始限制；其他内容仍须标为建议或待确认。本产出不授权自动批准、自动拒绝、写入现有系统或执行其他外部变更。`;
const FACT_BOUNDARY_EN = `> **ModelMirror fact and decision boundary (system-applied)**: Only items directly traceable to the original user request or a resolved human-input step are confirmed external facts. A draft explicitly approved by the user may supply approved policy decisions, but it does not prove external facts inside that draft and cannot override the original constraints; other content remains proposed or pending confirmation. This output does not authorize automatic approval, rejection, writes to existing systems, or other external changes.`;
const EXPLICIT_CHARACTER_LIMIT_PATTERN = /(?:不超过|最多|上限(?:为)?|≤)\s*([0-9][0-9,]*)\s*(?:个)?(?:中文)?(?:字符|字)|(?:no more than|at most|maximum(?: of)?)\s*([0-9][0-9,]*)\s*(?:Chinese\s+)?characters?/i;
const SINK_DEPENDENCY_CONTEXT_BUDGET = 15_000;
const SINK_DEPENDENCY_VALUE_MAX = 4_200;
const SINK_CONTEXT_MARKER = '[ModelMirror bounded dependency excerpt: the complete step output remains stored in the run history.]';
const SINGLE_STEP_EXECUTION_BOUNDARY = `ModelMirror single-step execution boundary / 单步骤执行边界:
- Complete only the current DAG step described below.
- The overall user goal is context and constraints, not an instruction to simulate or output the other workflow steps.
- Never produce, answer, or imitate a human_input, approval, later expert step, or the final deliverable unless this current step explicitly asks for it.
- Return only this step's requested deliverable and obey its local length bound.`;
const AUTHORITATIVE_INPUT_BLOCK = `Authoritative original user request / 权威原始用户请求（初始事实源）:
{{user_input}}

Use the original request above to distinguish confirmed external facts from derived recommendations. Model-generated dependency outputs are not evidence that a value was user-provided. If the user explicitly approved a visible draft, its policy choices may be finalized as approved decisions, but its factual claims remain derived and the original request wins every conflict. If a dependency changes a confirmed duration unit, preserve the original user's exact unit. A separately labeled resolved human_input block is also authoritative user input.`;
const RESOLVED_HUMAN_INPUT_HEADER = `Resolved human input (authoritative user-provided facts) / 已解决的人工输入（权威用户事实）:`;
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
const RESERVED_CONTEXT_VARIABLES = new Set(['user_input', 'goal', '_loop_iteration']);

function scopedExpertTask(task: string): string {
  const withoutPlannerGoalEcho = task.replace(
    /\n{2,}(?:用户任务|原始用户任务|User (?:request|goal)|Original user (?:request|goal))\s*[:：]\s*\n?\s*\{\{\s*(?:user_input|goal)\s*\}\}\s*$/iu,
    '',
  ).trim();
  return `${SINGLE_STEP_EXECUTION_BOUNDARY}\n\n${withoutPlannerGoalEcho || task.trim()}`;
}

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
  priorActiveDurationMs: number;
  revision?: {
    targetTaskId: string;
    feedback: string;
    previousOutput: string;
  };
  interaction?: {
    stepId: string;
    kind: 'human_input' | 'approval';
    value: string;
  };
}

function emit(
  channel: JsonlChannel,
  requestId: string,
  event: ExecutionEvent,
  protocol: typeof AGENCY_EXECUTION_PROTOCOL | typeof AGENCY_HITL_PROTOCOL = AGENCY_EXECUTION_PROTOCOL,
): void {
  channel.write({
    protocol,
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
  allowHitl: boolean,
  goal: string,
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
    const stepType = item.type === 'human_input' || item.type === 'approval'
      ? item.type
      : 'normal';
    for (const field of FORBIDDEN_STEP_FIELDS) {
      if (field === 'prompt' && stepType !== 'normal') continue;
      if (item[field] !== undefined && item[field] !== null) {
        throw new AgencyBridgeError(
          'agency_execution_plan_invalid',
          `Step ${index + 1} uses unsupported field "${field}".`,
        );
      }
    }
    if (item.type !== undefined && stepType === 'normal' && item.type !== 'normal') {
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
    if (stepType !== 'normal' && !allowHitl) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', 'HITL execution requires bridge protocol v3.');
    }
    const role = typeof item.role === 'string' ? item.role.trim().slice(0, 160) : '';
    if (stepType === 'normal' && !knownAgents.has(role)) {
      throw new AgencyBridgeError('unknown_agent', `Execution step references unknown expert "${role}".`);
    }
    if (stepType !== 'normal' && role) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `HITL step "${id}" cannot bind an expert.`);
    }
    const output = stringField(item, 'output', 128);
    if (!VARIABLE_PATTERN.test(output) || seenOutputs.has(output) || RESERVED_CONTEXT_VARIABLES.has(output)) {
      throw new AgencyBridgeError(
        'agency_execution_plan_invalid',
        `Step output "${output}" is invalid, duplicated, or reserved by ModelMirror.`,
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
    if (stepType !== 'normal' && (acceptance || methodSkills.length > 0)) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `HITL step "${id}" cannot define acceptance or Skills.`);
    }
    const prompt = stepType === 'normal' ? undefined : stringField(item, 'prompt', 4_000);
    return {
      id,
      role,
      name: typeof item.name === 'string' ? item.name.trim().slice(0, 200) || undefined : undefined,
      emoji: typeof item.emoji === 'string' ? item.emoji.slice(0, 16) : undefined,
      task: stringField(item, 'task', 20_000),
      acceptance: acceptance || undefined,
      output,
      depends_on: dependencies,
      type: stepType,
      prompt,
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
  if (sink.type !== 'normal' || !sink.acceptance) {
    throw new AgencyBridgeError(
      'agency_execution_plan_invalid',
      `Final step "${sink.id}" must define acceptance criteria.`,
    );
  }
  for (const step of steps) {
    if (step.type === 'normal' && step.id !== sink.id) {
      step.task = `${scopedExpertTask(step.task)}${closedFactTaskContract(goal)}`;
    }
  }
  sink.task = `${SINGLE_STEP_EXECUTION_BOUNDARY}\n\n${AUTHORITATIVE_INPUT_BLOCK}${closedFactTaskContract(goal)}\n\n${sink.task.replace(
    /\{\{\s*(?:user_input|goal)\s*\}\}/g,
    '[authoritative original input above]',
  )}${finalBodyBudgetInstruction(sink.acceptance, goal)}`;
  sink.acceptance = `${sink.acceptance}${FINAL_RELIABILITY_ACCEPTANCE}${closedFactAcceptanceContract(goal)}`;
  const interactions = steps.filter(step => step.type === 'human_input' || step.type === 'approval');
  if (interactions.length > 2) {
    throw new AgencyBridgeError('agency_execution_plan_invalid', 'Execution workflow may contain at most two HITL steps.');
  }
  const children = new Map(steps.map(step => [step.id, [] as string[]]));
  for (const step of steps) for (const dependency of step.depends_on ?? []) children.get(dependency)?.push(step.id);
  const approvalDraftStepIds = new Set(
    interactions
      .filter(step => step.type === 'approval')
      .flatMap(step => step.depends_on ?? []),
  );
  const closedQnaDraftTemplate = normalizeClosedFactQna(goal, '', false);
  const hasExactClosedQnaContract = closedQnaDraftTemplate.length > 0;
  for (const step of steps) {
    if (hasExactClosedQnaContract && (approvalDraftStepIds.has(step.id) || step.id === sink.id)) {
      const dependencyVariables = [...step.task.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g)]
        .map(match => match[1])
        .filter((value, index, values) => (
          value !== 'user_input' && value !== 'goal' && values.indexOf(value) === index
        ));
      const qnaTemplate = step.id === sink.id
        ? normalizeClosedFactQna(goal, '', true)
        : closedQnaDraftTemplate;
      const dependencyContext = dependencyVariables.length > 0
        ? `\n\nApproved dependency context (it cannot expand the closed fact set):\n${dependencyVariables
          .map(variable => `${variable}: {{${variable}}}`)
          .join('\n')}`
        : '';
      step.task = `${SINGLE_STEP_EXECUTION_BOUNDARY}\n\n${AUTHORITATIVE_INPUT_BLOCK}${closedFactTaskContract(goal)}

This is a system-authored closed-set Q&A task. It replaces any conflicting model-generated request for an introduction, contact section, example, suggestion, placeholder, or other unrequested structure. Reproduce the following structure from the authoritative facts without adding another section or topic:

${qnaTemplate}${dependencyContext}`;
      step.acceptance = `The deliverable must contain exactly the system-authored closed-set Q&A structure in the assigned task, with one Q&A per authoritative fact and no additional section, topic, fact, introduction, contact detail, example, suggestion, or placeholder.${FINAL_RELIABILITY_ACCEPTANCE}${closedFactAcceptanceContract(goal)}`;
    } else if (approvalDraftStepIds.has(step.id)) {
      step.acceptance = `${step.acceptance || 'The draft must fully satisfy its assigned task and every constraint in the original user request.'}${FINAL_RELIABILITY_ACCEPTANCE}${closedFactAcceptanceContract(goal)}`;
    }
    step.verify = step.id === sink.id || approvalDraftStepIds.has(step.id);
  }
  const byId = new Map(steps.map(step => [step.id, step]));
  const walk = (initial: string[], next: (id: string) => string[]): Set<string> => {
    const seen = new Set<string>();
    const pending = [...initial];
    while (pending.length) {
      const current = pending.pop()!;
      if (seen.has(current)) continue;
      seen.add(current);
      pending.push(...next(current));
    }
    return seen;
  };
  for (const step of interactions) {
    const ancestors = walk(step.depends_on ?? [], id => byId.get(id)?.depends_on ?? []);
    const descendants = walk(children.get(step.id) ?? [], id => children.get(id) ?? []);
    if (steps.some(other => other.id !== step.id && !ancestors.has(other.id) && !descendants.has(other.id))) {
      throw new AgencyBridgeError('agency_execution_plan_invalid', `HITL step "${step.id}" must be a full DAG barrier.`);
    }
  }

  const workflow: WorkflowDefinition = {
    name: typeof raw.name === 'string' ? raw.name.trim().slice(0, 200) || 'ModelMirror Expert Team' : 'ModelMirror Expert Team',
    description: typeof raw.description === 'string' ? raw.description.trim().slice(0, 2_000) : undefined,
    agents_dir: '',
    llm: {
      provider: 'modelmirror',
      model: modelId,
      max_tokens: MAX_EXECUTION_OUTPUT_TOKENS,
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
  errors.push(...validatePlainTextTemplateReferences(workflow));
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

function hasClosedOutputSectionContract(goal: string): boolean {
  return /(?:仅|只)(?:需|需要|应|可)?(?:输出|包含|包括|保留|提供)\s*([^。；;\n]{2,240})/u.test(goal);
}

export function applyFactBoundary(output: string, goal: string): string {
  if (output.includes(FACT_BOUNDARY_MARKER) || output.includes('ModelMirror 事实与决策边界')) {
    return output;
  }
  if (hasClosedOutputSectionContract(goal)) return output;
  const boundary = factBoundary(goal);
  return `${boundary}\n\n${output}`;
}

export function normalizeClosedMaterialList(
  authoritativeInput: string,
  output: string,
): string {
  const closedMaterialMatches = [...authoritativeInput.matchAll(
    /(?:可使用|可用|允许使用)?\s*(?:的)?材料\s*(?:只有|仅包括|仅限(?:于)?|只包括)\s*([^。；;\n]{2,160})/gu,
  )];
  const closedMaterialText = closedMaterialMatches.at(-1)?.[1]?.trim();
  if (!closedMaterialText) return output;
  const allowedMaterials = closedMaterialText
    .split(/[、，,]|(?:以及|或者|或|和|及)/u)
    .map(item => item.trim())
    .filter(item => item.length >= 2);
  if (allowedMaterials.length === 0) return output;
  const normalize = (value: string): string => value
    .toLowerCase()
    .replace(/(?:现有|已有)|[\s*_`#：:。.，,、()（）\[\]"'“”《》]/giu, '');
  const lines = output.split(/\r?\n/u);
  const heading = lines.findIndex(line => /^\s*#{1,6}\s+.*材料(?:清单|列表)/u.test(line));
  if (heading < 0) return output;
  const end = lines.findIndex((line, index) => (
    index > heading && /^\s*#{1,6}\s+\S/u.test(line)
  ));
  const stop = end > heading ? end : lines.length;
  let changed = false;
  for (let index = heading + 1; index < stop; index += 1) {
    const match = lines[index]?.match(/^(\s*(?:[-*+]|\d+[.、)]))\s+(.+)$/u);
    if (!match) continue;
    const content = match[2] ?? '';
    const matched = allowedMaterials.filter(item => normalize(content).includes(normalize(item)));
    if (matched.length === 0) continue;
    const canonical = `${match[1]} ${matched.join('、')}`;
    if (canonical !== lines[index]) {
      lines[index] = canonical;
      changed = true;
    }
  }
  return changed ? lines.join('\n') : output;
}

export function normalizeClosedOutputWrapperTitle(
  authoritativeInput: string,
  output: string,
): string {
  if (!hasClosedOutputSectionContract(authoritativeInput)) return output;
  const lines = output.split(/\r?\n/u);
  const firstContent = lines.findIndex(line => line.trim().length > 0);
  if (firstContent < 0 || !/^\s*#\s+\S/u.test(lines[firstContent] ?? '')) return output;
  const hasExplicitTitleSection = lines.some((line, index) => (
    index > firstContent
    && /^\s*#{2,6}\s+(?:(?:第)?[一二两三四五六七八九十\d]+[.、)）]?\s*)?标题\s*$/u.test(line)
  ));
  if (!hasExplicitTitleSection) return output;
  lines.splice(firstContent, 1);
  while (lines[firstContent]?.trim() === '') lines.splice(firstContent, 1);
  return lines.join('\n').trim();
}

export function factBoundary(goal: string): string {
  const base = /[\u3400-\u9fff]/.test(goal) ? FACT_BOUNDARY_ZH : FACT_BOUNDARY_EN;
  const explicitNoAction = explicitNoActionPhrase(goal);
  if (!explicitNoAction) return base;
  return /[\u3400-\u9fff]/.test(goal)
    ? `${base} 用户原始非执行边界：不得执行真实${explicitNoAction.trim()}。`
    : base;
}

function explicitNoActionPhrase(value: string): string | undefined {
  return value.match(
    /(?:不得|不应|不会|不)(?:执行|进行|实施)\s*(?:任何)?真实(?:的)?([^。；\n]{2,160})/u,
  )?.[1];
}

function explicitCharacterLimit(value: string): { limit: number; matched: string } | null {
  const match = value.match(EXPLICIT_CHARACTER_LIMIT_PATTERN);
  if (!match) return null;
  const rawLimit = match[1] ?? match[2];
  const limit = Number(rawLimit.replaceAll(',', ''));
  if (!Number.isSafeInteger(limit) || limit < 1) return null;
  return { limit, matched: match[0] };
}

function finalBodyBudgetInstruction(acceptance: string, goal: string): string {
  const parsed = explicitCharacterLimit(acceptance);
  if (!parsed) return '';
  const boundaryReserve = hasClosedOutputSectionContract(goal)
    ? 0
    : Array.from(`${factBoundary(goal)}\n\n`).length;
  const bodyLimit = Math.max(1, parsed.limit - boundaryReserve);
  return /[\u3400-\u9fff]/.test(acceptance)
    ? `\n\nModelMirror 最终长度硬预算（系统注入）：完成正文后系统还会自动添加 ${boundaryReserve} 个字符的事实边界。正文最多 ${bodyLimit} 个字符，正文与事实边界合计不得超过验收要求的 ${parsed.limit} 个字符；不要在正文中重复该事实边界。`
    : `\n\nModelMirror final length budget (system-injected): after the body, the system adds a ${boundaryReserve}-character fact boundary. Keep the generated body within ${bodyLimit} characters so body plus boundary remains within the ${parsed.limit}-character acceptance limit; do not repeat the boundary in the body.`;
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

export function additionalFactBoundaryFailures(
  authoritativeInput: string,
  output: string,
): { criterion: string; why: string }[] {
  const failed: { criterion: string; why: string }[] = [];
  const cadenceLines = output.split(/\r?\n/).filter(line => {
    const cadence = line.match(/(每(?:年|季度|月|周))[^\n]{0,40}(?:一次|组织|核查|审查|抽查|审计|复盘|报告|清理|轮换)/u)?.[1];
    if (!cadence || authoritativeInput.includes(cadence)) return false;
    return !/(?:待确认|建议|示例|假设|proposal|tbd|pending)/iu.test(line);
  }).slice(0, 4);
  if (cadenceLines.length > 0) {
    failed.push({
      criterion: '用户未提供的周期性审查、核查或运营频率必须逐项标为建议或待确认。',
      why: `以下周期性政策不是权威用户事实，且未在同一条中标为建议或待确认：${cadenceLines.map(line => line.trim().slice(0, 300)).join(' | ')}`,
    });
  }

  const organizationTotal = authoritativeInput.match(/(\d{1,3})\s*人(?:运营|团队|机构)/u)?.[1];
  if (organizationTotal) {
    const subgroupPattern = new RegExp(`(?:普通|一线|运营|执行)?(?:员工|成员|志愿者)[^\\n]{0,20}(?:共|合计)\\s*${organizationTotal}\\s*人`, 'u');
    const subgroupLine = output.split(/\r?\n/).find(line => subgroupPattern.test(line));
    const listsAdditionalRole = /(?:馆长|负责人|经理|主管|所有者|director|manager|owner)/iu.test(output);
    if (subgroupLine && listsAdditionalRole) {
      failed.push({
        criterion: '组织总人数不得直接改写为某个子角色的人数。',
        why: `权威输入只确认组织共 ${organizationTotal} 人；正文又列出其他角色，却把全部 ${organizationTotal} 人归为一个子角色：${subgroupLine.trim().slice(0, 300)}`,
      });
    }
  }

  const durationPattern = /(\d+(?:\.\d+)?)\s*(?:个)?(工作日|自然日|天|年|月|周|小时|分钟|business\s+days?|calendar\s+days?|days?|years?|months?|weeks?|hours?|minutes?)/giu;
  const normalizeDuration = (value: string): string => value.toLowerCase().replace(/[\s个]+/gu, '');
  const authoritativeMatches = [...authoritativeInput.matchAll(durationPattern)];
  const authoritativeDurations = new Set(authoritativeMatches.map(match => normalizeDuration(match[0])));
  const durationMinutes = (match: RegExpMatchArray): number | null => {
    const value = Number(match[1]);
    const unit = match[2].toLowerCase().replace(/\s+/gu, '');
    if (!Number.isFinite(value)) return null;
    if (/^(?:周|weeks?)$/u.test(unit)) return value * 7 * 24 * 60;
    if (/^(?:天|days?)$/u.test(unit)) return value * 24 * 60;
    if (/^(?:小时|hours?)$/u.test(unit)) return value * 60;
    if (/^(?:分钟|minutes?)$/u.test(unit)) return value;
    return null;
  };
  const authoritativeByNumber = new Map<string, Set<string>>();
  for (const match of authoritativeMatches) {
    const values = authoritativeByNumber.get(match[1]) ?? new Set<string>();
    values.add(match[0].trim());
    authoritativeByNumber.set(match[1], values);
  }
  const durationUnitDrifts: string[] = [];
  const unprovidedDurations: string[] = [];
  const chineseWeekday = new Map([
    ['一', 1], ['二', 2], ['三', 3], ['四', 4], ['五', 5], ['六', 6], ['日', 7], ['天', 7],
  ]);
  const supportedWeekdayCounts = [...authoritativeInput.matchAll(/周([一二三四五六日天])\s*(?:至|到|—|-)\s*周?([一二三四五六日天])/gu)]
    .map(match => {
      const start = chineseWeekday.get(match[1]);
      const end = chineseWeekday.get(match[2]);
      if (!start || !end || end < start) return null;
      return { range: match[0].replace(/\s+/gu, ''), days: end - start + 1 };
    })
    .filter((value): value is { range: string; days: number } => value !== null);
  for (const line of output.split(/\r?\n/)) {
    if (/(?:待确认|建议|示例|假设|proposal|tbd|pending)/iu.test(line)) continue;
    for (const match of line.matchAll(durationPattern)) {
      if ((match.index ?? 0) > 0 && line[(match.index ?? 0) - 1] === '第') continue;
      if (authoritativeDurations.has(normalizeDuration(match[0]))) continue;
      const compactLine = line.replace(/\s+/gu, '');
      const candidateMinutes = durationMinutes(match);
      const isInlineEquivalent = candidateMinutes !== null && authoritativeMatches.some(authoritative => {
        const authoritativeMinutes = durationMinutes(authoritative);
        return authoritativeMinutes === candidateMinutes
          && compactLine.toLowerCase().includes(normalizeDuration(authoritative[0]));
      });
      if (isInlineEquivalent) continue;
      const isSupportedWeekdayCount = /天/u.test(match[0]) && supportedWeekdayCounts.some(item => (
        item.days === Number(match[1]) && compactLine.includes(item.range)
      ));
      if (isSupportedWeekdayCount) continue;
      const expected = authoritativeByNumber.get(match[1]);
      if (expected) {
        durationUnitDrifts.push(`${line.trim().slice(0, 260)}（用户原文单位：${[...expected].join('/')}）`);
      } else {
        unprovidedDurations.push(line.trim().slice(0, 300));
      }
    }
  }
  const uniqueUnitDrifts = [...new Set(durationUnitDrifts)].slice(0, 4);
  const uniqueUnprovidedDurations = [...new Set(unprovidedDurations)].slice(0, 4);
  if (uniqueUnitDrifts.length > 0) {
    failed.push({
      criterion: '不得把用户已确认的时限静默改成另一种单位。',
      why: `以下条目复用了用户时限数字却改变了单位：${uniqueUnitDrifts.join(' | ')}。若指同一要求，逐处恢复用户原始单位；若是另一项建议，必须单独标为建议或待确认。`,
    });
  }
  if (uniqueUnprovidedDurations.length > 0) {
    failed.push({
      criterion: '用户未提供的时限或周期必须在同一条中标为建议、示例或待确认。',
      why: `以下带单位时限不是权威用户事实，且未在同一条中标注：${uniqueUnprovidedDurations.join(' | ')}。返工时必须删除这些未知值，或在每个值紧邻位置明确写“待确认”“建议”或“示例”；“目标”“理论”“实际以现场情况为准”等泛化措辞不算逐项标注。`,
    });
  }
  const countPattern = /(\d+(?:\.\d+)?)\s*(件|张|把|支|套|元|万元|%)/gu;
  const normalizeCount = (value: string, unit: string): string => `${Number(value)}${unit}`;
  const authoritativeCounts = new Set(
    [...authoritativeInput.matchAll(countPattern)].map(match => normalizeCount(match[1], match[2])),
  );
  const chineseNumbers = new Map([
    ['一', 1], ['二', 2], ['两', 2], ['三', 3], ['四', 4], ['五', 5],
    ['六', 6], ['七', 7], ['八', 8], ['九', 9], ['十', 10],
  ]);
  for (const match of authoritativeInput.matchAll(/([一二两三四五六七八九十])\s*(件|张|把|支|套)/gu)) {
    const value = chineseNumbers.get(match[1]);
    if (value) authoritativeCounts.add(normalizeCount(String(value), match[2]));
  }
  const unprovidedCounts: string[] = [];
  for (const line of output.split(/\r?\n/)) {
    if (/(?:待确认|待补充|建议|示例|假设|proposal|tbd|pending|_{2,})/iu.test(line)) continue;
    for (const match of line.matchAll(countPattern)) {
      if ((match.index ?? 0) > 0 && line[(match.index ?? 0) - 1] === '第') continue;
      const trailing = line.slice((match.index ?? 0) + match[0].length);
      if (authoritativeCounts.has(normalizeCount(match[1], match[2]))) continue;
      unprovidedCounts.push(line.trim().slice(0, 300));
    }
  }
  const uniqueUnprovidedCounts = [...new Set(unprovidedCounts)].slice(0, 4);
  if (uniqueUnprovidedCounts.length > 0) {
    failed.push({
      criterion: '用户未提供的业务数量必须在同一条中标为建议、示例或待确认。',
      why: `以下带单位数量不是权威用户事实，且未在同一条中标注：${uniqueUnprovidedCounts.join(' | ')}。返工时必须删除这些未知值，或在每个值紧邻位置明确写“待确认”“建议”或“示例”。章节编号、方案数量等文档结构计数不受此限制。`,
    });
  }
  const prohibitedContentClauses = [...authoritativeInput.matchAll(
    /(?:不得|禁止|不要|不允许)(?:再)?(?:新增|包含|引入|提供)?\s*([^。；;\n]{2,220})/gu,
  )].map(match => match[1] ?? '');
  const prohibitedContent = prohibitedContentClauses.join('、');
  if (prohibitedContent) {
    const prohibitedBudgetTerms = ['预算', '金额', '费用', '成本']
      .filter(term => prohibitedContent.includes(term));
    const prohibitedBudgetPattern = new RegExp(
      `(?:${prohibitedBudgetTerms.join('|')}|\\d+\\s*(?:元|万元))`,
      'u',
    );
    const categories = [
      {
        name: '日期或时限',
        enabled: /(?:日期|日程|响应时限|时限|持续时间|处理时长|周期)/u.test(prohibitedContent),
        pattern: /(?:日期|日程|响应时限|持续时间|处理时长|\d+\s*(?:分钟|小时|天|日))/u,
      },
      {
        name: '人数',
        enabled: /(?:人数|用户数|人次|受影响用户)/u.test(prohibitedContent),
        pattern: /(?:人数|用户数|人次|受影响[^。；;\n]{0,16}用户|\d+\s*(?:人|名用户|位用户)|(?:(?:数|几)(?:十|百|千|万)|成百上千|成千上万|许多|大量)[^。；;\n]{0,8}(?:人|同事|员工|用户|参与者|陌生人))/u,
      },
      {
        name: '预算',
        enabled: prohibitedBudgetTerms.length > 0,
        pattern: prohibitedBudgetPattern,
      },
      {
        name: '软件或应用',
        enabled: /(?:软件|应用)/u.test(prohibitedContent),
        pattern: /(?:使用|采用|安装|引入|新增|购买|部署|配置)[^。；;\n]{0,24}(?:软件|应用(?:程序)?)|(?:软件|应用程序)(?:名称|清单|工具|系统|平台|版本|账号|[：:])/u,
      },
      {
        name: '系统或平台',
        enabled: /(?:系统|平台)/u.test(prohibitedContent),
        pattern: /(?:系统|平台)/u,
      },
      {
        name: '工具',
        enabled: /工具/u.test(prohibitedContent),
        pattern: /工具/u,
      },
      {
        name: '通知或联系渠道',
        enabled: /(?:通知渠道|联系渠道|联系方式|电话|邮箱|邮件|IM|群组|短信|手机)/iu.test(prohibitedContent),
        pattern: /(?:通知渠道|联系渠道|联系方式|电话|邮箱|邮件|IM|群组|短信|手机)/iu,
      },
      {
        name: '真实执行动作',
        enabled: /(?:执行真实操作|真实操作|外部操作|执行动作|真实行动)/u.test(prohibitedContent),
        pattern: /(?:(?:执行|启动|实施)(?:升级|修复|操作|流程)|(?:立即|依次|实际|自动|直接)(?:联系|转接|通知|发送|拨打|修改|发布|写入)|(?:→|则|后)\s*(?:联系|转接|通知|发送|拨打|修改|发布|写入)|记录并监控)/u,
      },
    ].filter(category => category.enabled);
    const negated = /(?:不得|禁止|请勿|勿|不要|不允许|不应|不包含|未包含|(?:未|没有(?:任何)?)(?:新增|添加|引入|包含|提供)(?:的)?|(?:没有|无)(?:任何)?[^。；;\n]{0,120}(?:额外|新增|添加)(?:的)?(?:信息|字段|内容)?|不添加|不使用|不执行|不联系|不通知|不发送|不修改|不发布|仅描述|不授权)/u;
    const outputLines = output.split(/\r?\n/u);
    const lineIsNegated = (index: number): boolean => {
      if (negated.test(outputLines[index] ?? '')) return true;
      if (!/^\s*[-*+]\s+/u.test(outputLines[index] ?? '')) return false;
      for (let cursor = index - 1; cursor >= Math.max(0, index - 10); cursor -= 1) {
        const prior = outputLines[cursor] ?? '';
        if (!prior.trim() || /^\s*[-*+]\s+/u.test(prior)) continue;
        return negated.test(prior);
      }
      return false;
    };
    const violations = outputLines.flatMap((line, index) => (
      lineIsNegated(index)
        ? []
        : categories.filter(category => category.pattern.test(line)).map(category => ({ category: category.name, line }))
    )).slice(0, 8);
    if (violations.length > 0) {
      failed.push({
        criterion: `用户明确禁止新增或执行：${prohibitedContent}。`,
        why: `最终产出仍包含被禁止的字段或动作：${violations.map(item => `${item.category}=${item.line.trim().slice(0, 180)}`).join(' | ')}。返工时必须删除，不得改为示例或待确认。`,
      });
    }
  }
  const hasFirstTimeAudience = /(?:适用对象[^。；;\n]{0,80})?(?:只有|仅限|仅面向)?[^。；;\n]{0,30}(?:第一次|首次)参加[^。；;\n]{0,50}(?:同事|员工|人员|参与者)/u.test(authoritativeInput);
  if (hasFirstTimeAudience) {
    const lines = output.split(/\r?\n/u);
    const titleLines = new Set<number>();
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index] ?? '';
      if (/^\s*#\s+\S/u.test(line)) titleLines.add(index);
      if (/^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*标题\s*(?:\*\*|__)?\s*(?:[：:]|$)/u.test(line)) {
        const next = lines.findIndex((candidate, candidateIndex) => candidateIndex > index && candidate.trim().length > 0);
        if (next > index) titleLines.add(next);
      }
    }
    const shiftedAudienceQualifier = [...titleLines]
      .map(index => lines[index] ?? '')
      .find(line => (
        /(?:第一次|首次)[^。；;\n]{0,24}(?:读书会|活动|培训|课程|会议)/u.test(line)
        && !/(?:参加|参与|同事|员工|人员)/u.test(line)
      ));
    if (shiftedAudienceQualifier) {
      failed.push({
        criterion: '“第一次/首次”只限定参与者经历，不得改写为活动、课程或会议的序号。',
        why: `标题把“第一次参加的对象”错误改成了“第一次/首次活动”：${shiftedAudienceQualifier.trim().slice(0, 300)}。返工时应改为“面向首次参与者”或删除活动序号。`,
      });
    }
  }
  const closedOutputMatch = authoritativeInput.match(
    /(?:(?:最终(?:输出|产出)?|输出|产出|文档|页面|正文)[^。；;\n]{0,24}(?:仅|只)(?:需|需要|应|可)?(?:包含|包括|保留|提供)|(?:仅|只)(?:需|需要|应|可)?(?:输出|保留|提供))\s*[：:]?\s*([^。；;\n]{2,240})/u,
  );
  const closedOutputText = closedOutputMatch?.[1]?.trim();
  if (closedOutputText) {
    const normalizeSection = (value: string): string => value
      .toLowerCase()
      .replace(/第?[一二两三四五六七八九十0-9]+\s*(?:个|项|条|部分|章节)?/gu, '')
      .replace(/(?:最终|完整|一页|markdown|版)/giu, '')
      .replace(/适用人群/gu, '适用对象')
      .replace(/讨论议题/gu, '讨论议程')
      .replace(/物料清单/gu, '材料清单')
      .replace(/[\s*_`#：:，,。、；;（）()\[\]"'“”‘’]+/gu, '');
    const allowedSections = closedOutputText
      .split(/[、，,]|(?:以及|和|与)/u)
      .map(item => normalizeSection(item))
      .filter(item => item.length >= 2);
    const isAllowedSection = (label: string): boolean => {
      const normalized = normalizeSection(label);
      return allowedSections.some(allowed => (
        normalized === allowed || normalized.includes(allowed) || allowed.includes(normalized)
      ));
    };
    const lines = output.split(/\r?\n/u);
    const headings = lines.map((line, index) => {
      const match = line.match(/^\s*(#{1,6})\s+(.+?)\s*$/u);
      return match ? { index, level: match[1].length, label: match[2] } : null;
    }).filter((item): item is { index: number; level: number; label: string } => item !== null);
    const firstH1 = headings.find(item => item.level === 1);
    const sectionLevel = firstH1 ? 2 : Math.min(...headings.map(item => item.level));
    const unexpectedHeadings = headings.filter(item => (
      item.index !== firstH1?.index
      && item.level === sectionLevel
      && !isAllowedSection(item.label)
    )).map(item => item.label);
    const unexpectedStrongLabels = lines.map(line => (
      line.match(/^\s*\*\*([^*：:\n]{1,50})[：:][^*]*\*\*/u)?.[1] ?? ''
    )).filter(label => label && !isAllowedSection(label));
    const unexpectedSections = [...new Set([...unexpectedHeadings, ...unexpectedStrongLabels])].slice(0, 6);
    if (allowedSections.length > 0 && unexpectedSections.length > 0) {
      failed.push({
        criterion: `用户明确要求最终产出只包含这些栏目：${closedOutputText}。`,
        why: `最终产出增加了封闭栏目白名单之外的顶层栏目：${unexpectedSections.join('、')}。返工时删除这些栏目及其内容，不得仅改名或标为待确认。`,
      });
    }
  }
  const closedMaterialMatches = [...authoritativeInput.matchAll(
    /(?:可使用|可用|允许使用)?\s*(?:的)?材料\s*(?:只有|仅包括|仅限(?:于)?|只包括)\s*([^。；;\n]{2,160})/gu,
  )];
  const closedMaterialText = closedMaterialMatches.at(-1)?.[1]?.trim();
  if (closedMaterialText) {
    const normalizeMaterial = (value: string): string => value
      .toLowerCase()
      .replace(/(?:现有|已有)|[\s*_`#：:。.，,、()（）\[\]"'“”《》]/giu, '');
    const allowedMaterials = closedMaterialText
      .split(/[、，,]|(?:以及|或者|或|和|及)/u)
      .map(item => normalizeMaterial(item))
      .filter(item => item.length >= 2);
    const lines = output.split(/\r?\n/);
    const materialHeading = lines.findIndex(line => /^\s*#{1,6}\s+.*材料(?:清单|列表)/u.test(line));
    const materialSection = materialHeading < 0
      ? []
      : lines.slice(materialHeading + 1, lines.findIndex((line, index) => (
        index > materialHeading && /^\s*#{1,6}\s+\S/u.test(line)
      )) > materialHeading ? lines.findIndex((line, index) => (
        index > materialHeading && /^\s*#{1,6}\s+\S/u.test(line)
      )) : lines.length);
    const materialNoun = /(?:模板|文档|资料|书|便签|笔|投影|白板|工具|设备|软件|系统|材料)/u;
    const extraMaterialLines = materialSection.filter(line => {
      if (!/^\s*[-*]\s+\S/u.test(line)) return false;
      const content = line.replace(/^\s*[-*]\s+/u, '');
      const [label, ...rest] = content.split(/[：:]/u);
      const normalizedContent = normalizeMaterial(content);
      const matchesAllowedMaterial = allowedMaterials.some(item => normalizeMaterial(label).includes(item));
      if (matchesAllowedMaterial) {
        const remainder = allowedMaterials.reduce(
          (current, item) => current.replaceAll(item, ''),
          normalizedContent,
        );
        const addsSubtype = /(?:版本|型号|规格|尺寸|类型|品类|配套|附件|替代|可选|均可)/u.test(content);
        return addsSubtype || materialNoun.test(remainder);
      }
      const candidates = (rest.length ? rest.join(':') : content).split(/[、，,；;]/u);
      return candidates.some(candidate => (
        materialNoun.test(candidate)
        && !allowedMaterials.some(item => normalizeMaterial(candidate).includes(item))
      ));
    });
    const proposesOtherMaterials = lines.filter(line => (
      /(?:其他|额外|另行|除[^。；;\n]{0,60}外)[^。；;\n]{0,80}(?:材料|资源|工具)/u.test(line)
      && !/(?:不得|不使用|不包含|无需|禁止|严禁)/u.test(line)
    ));
    const violations = [...new Set([...extraMaterialLines, ...proposesOtherMaterials])].slice(0, 4);
    if (allowedMaterials.length > 0 && violations.length > 0) {
      failed.push({
        criterion: `材料清单是封闭集合，只能包含用户确认的：${closedMaterialText}。`,
        why: `以下条目增加了封闭清单之外的材料或资源，即使标为待确认也不允许：${violations.map(line => line.trim().slice(0, 300)).join(' | ')}。返工时删除额外项，只保留用户确认材料及其用途说明。`,
      });
    }
  }
  const requiresPendingTargets = /(?:未提供|缺失|未知|未明确)[^。；\n]{0,40}(?:目标(?:值)?|指标)[^。；\n]{0,30}(?:待确认|待补充|TBD|pending)|(?:目标(?:值)?|指标)[^。；\n]{0,40}(?:未提供|缺失|未知|未明确)[^。；\n]{0,30}(?:待确认|待补充|TBD|pending)/iu.test(authoritativeInput);
  if (requiresPendingTargets) {
    const targetLines = output.split(/\r?\n/).filter(line => {
      const withoutOrdinal = line.replace(/^\s*(?:#{1,6}\s*)?(?:[-*+]\s*)?\d+[.)、]\s*/u, '');
      return /目标(?:值|样本量)?/u.test(withoutOrdinal)
        && /\d/u.test(withoutOrdinal)
        && !/(?:待确认|待补充|建议|示例|假设|proposal|tbd|pending)/iu.test(withoutOrdinal);
    }).slice(0, 4);
    if (targetLines.length > 0) {
      failed.push({
        criterion: '用户要求未提供的目标值必须逐项标为待确认。',
        why: `以下数值目标没有在同一条中标为待确认：${targetLines.map(line => line.trim().slice(0, 300)).join(' | ')}。返工时必须删除这些未知目标，或在每个目标值紧邻位置明确写“待确认”；仅写“目标”“理论”或“实际以现场情况为准”不算待确认。`,
      });
    }
  }
  const perEventCapacity = authoritativeInput.match(/每(?:场|次)[^。；\n]{0,30}?(?:最多|上限|不超过)?\s*(\d+(?:\.\d+)?)\s*(?:名|人|份)/u);
  if (perEventCapacity) {
    const cap = perEventCapacity[1];
    const unsupportedAggregate = output.split(/\r?\n/).find(line => (
      line.includes(cap)
      && (
        new RegExp(`${cap}\\s*(?:名|人|份)\\s*(?:/|每)\\s*(?:天|日|周|月)`, 'u').test(line)
        || (/(?:每日|每天|每周|总计|总量|总样本|理论最大)/u.test(line) && /(?:场|次)/u.test(line))
      )
      && !/(?:待确认|待补充|建议|示例|假设|proposal|tbd|pending)/iu.test(line)
      && !/(?:不得|禁止|不要|不可|不应|避免)(?:使用|采用|按照|基于)?[^。；\n]{0,180}(?:计算|推导|换算|预设|汇总|聚合)/u.test(line)
    ));
    if (unsupportedAggregate) {
      failed.push({
        criterion: '不得在未提供每日场次数时，把用户确认的每场容量推导为每日、每周或总量。',
        why: `权威输入只确认每场上限 ${cap}；以下条目跨场次聚合了容量：${unsupportedAggregate.trim().slice(0, 300)}。返工时必须保留“每场”单位，并删除聚合值，或把每日场次数与聚合量逐项标为待确认。`,
      });
    }
  }
  const extensionLine = output.split(/\r?\n/).find(line => (
    /(?:顺延|延期|延后|延至|next\s+business\s+day|roll(?:ed)?\s+over)/iu.test(line)
    && !/(?:待确认|建议|示例|假设|proposal|tbd|pending)/iu.test(line)
  ));
  const authoritativeDefinesExtension = /(?:顺延|延期|延后|延至|next\s+business\s+day|roll(?:ed)?\s+over|extension\s+policy)/iu.test(authoritativeInput);
  if (extensionLine && !authoritativeDefinesExtension) {
    failed.push({
      criterion: '不得为用户时限静默增加非工作日顺延、宽限期或其他延期规则。',
      why: `以下延期政策不是权威用户事实，且未标为建议或待确认：${extensionLine.trim().slice(0, 300)}`,
    });
  }
  const explicitNoAction = explicitNoActionPhrase(authoritativeInput);
  if (explicitNoAction) {
    const normalizeScope = (value: string): string => value.toLowerCase().replace(/[\s*_`#：:。.，,、()（）\[\]]+/gu, '');
    const prohibitedActions = explicitNoAction
      .split(/[、，,]|(?:以及|或者|或|和|及)/u)
      .map(action => action.replace(/^(?:任何|真实(?:的)?)/u, '').trim())
      .filter(action => action.length >= 2);
    const negativeScopeLines = output.split(/[。；;\r\n]+/u).filter(line => (
      /(?:不得|不应|不会|不执行|不涉及|不进行|不实施|不授权|禁止|严禁|must\s+not|does\s+not|do\s+not|not\s+authoriz)/iu.test(line)
    ));
    const missingActions = prohibitedActions.filter(action => (
      !negativeScopeLines.some(line => normalizeScope(line).includes(normalizeScope(action)))
    ));
    if (missingActions.length > 0) {
      failed.push({
        criterion: `最终产出必须明确保留用户的非执行边界：不得执行真实${explicitNoAction.trim()}。`,
        why: `最终产出没有在否定或非授权语境中明确覆盖：${missingActions.join('、')}。仅描述流程或使用笼统的“其他外部变更”不足以保留用户指定的行动边界。`,
      });
    }
  }
  return failed;
}

function executionOutputValidator(
  sinkId: string,
  approvalDraftStepIds: ReadonlySet<string>,
  goal: string,
  resolvedHumanInput: string,
): (node: DAGNode, output: string, acceptance: string) => {
  pass: boolean;
  failed: { criterion: string; why: string }[];
} | null {
  return (node, output, acceptance) => {
    const isSink = node.step.id === sinkId;
    if (!isSink && !approvalDraftStepIds.has(node.step.id)) return null;
    const factBoundaryFailures = additionalFactBoundaryFailures(
      `${goal}\n${resolvedHumanInput}`,
      `${factBoundary(goal)}\n${output}`,
    );
    if (factBoundaryFailures.length > 0) return { pass: false, failed: factBoundaryFailures };
    if (!isSink) return null;
    const hasPerShiftCapacity = /(?:每班|per[- ]shift)[^\n]{0,80}\d+\s*(?:人|people|staff)/iu.test(resolvedHumanInput);
    const unsupportedDailyStaffingLine = output.split(/\r?\n/).find(line => (
      /每日[^\n]{0,80}(?:人员|人数|人力|用工|配置|需求|总数|总计|部署|计划|到岗)[^\n]{0,80}\d+\s*人|\d+\s*人\s*(?:\/\s*天|每天)/u.test(line)
      || /daily[^\n]{0,24}(?:staffing|headcount|people|staff|total)[^\n]{0,40}\d+/iu.test(line)
    ));
    if (hasPerShiftCapacity && unsupportedDailyStaffingLine) {
      const zh = /[\u3400-\u9fff]/u.test(resolvedHumanInput);
      return {
        pass: false,
        failed: [{
          criterion: zh
            ? '不得从用户提供的每班人数推导每日或跨班次人员合计。'
            : 'Do not infer daily or cross-shift staffing from a user-provided per-shift headcount.',
          why: zh
            ? `权威人工输入只确认每班人数，未确认跨班次或跨日个人可用性。请删除这条每日人员合计，不要仅改成“待确认”：${unsupportedDailyStaffingLine.trim().slice(0, 300)}`
            : `The authoritative human input confirms per-shift capacity only, not cross-shift or daily availability. Delete this daily total rather than relabeling it TBD: ${unsupportedDailyStaffingLine.trim().slice(0, 300)}`,
        }],
      };
    }
    const parsed = explicitCharacterLimit(acceptance);
    if (!parsed) return null;
    const { limit, matched } = parsed;
    const boundary = factBoundary(goal);
    const boundaryReserve = output.includes(FACT_BOUNDARY_MARKER)
      || output.includes('ModelMirror 事实与决策边界')
      || hasClosedOutputSectionContract(goal)
      ? 0
      : Array.from(`${boundary}\n\n`).length;
    const bodyLength = Array.from(output.trim()).length;
    const finalLength = bodyLength + boundaryReserve;
    if (finalLength <= limit) return { pass: true, failed: [] };
    const criterion = acceptance
      .split(/\r?\n/)
      .find(line => line.includes(matched))
      ?.trim() || matched;
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
  const priorActiveDurationMs = finiteCount(
    raw.prior_active_duration_ms,
    900_000,
    'prior_active_duration_ms',
  );
  return {
    sourceTaskId,
    ...parseCompletedSteps(raw.completed_steps, workflow, goal, 'resume.completed_steps', 1),
    priorModelCalls,
    priorUsage,
    priorActiveDurationMs,
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
    priorActiveDurationMs: 0,
    revision: { targetTaskId, feedback, previousOutput },
  };
}

function parseInteractionResume(
  value: unknown,
  workflow: WorkflowDefinition,
  goal: string,
): ResumeState | null {
  if (value === undefined || value === null) return null;
  const code = 'agency_interaction_invalid';
  const raw = asObject(value, code);
  const sourceTaskId = executionStringField(raw, 'source_task_id', 200, code);
  const stepId = executionStringField(raw, 'step_id', 64, code);
  const kind = raw.kind === 'approval' || raw.kind === 'human_input' ? raw.kind : null;
  const valueText = executionStringField(raw, 'value', 20_000, code);
  const target = workflow.steps.find(step => step.id === stepId);
  if (!kind || target?.type !== kind) {
    throw new AgencyBridgeError(code, 'Interaction target does not match the frozen workflow.');
  }
  const priorModelCalls = finiteCount(raw.prior_model_calls, MAX_EXECUTION_MODEL_CALLS, 'prior_model_calls');
  const rawUsage = asObject(raw.prior_usage ?? {}, code);
  const priorUsage = {
    input_tokens: finiteCount(rawUsage.input_tokens, Number.MAX_SAFE_INTEGER, 'prior_usage.input_tokens'),
    output_tokens: finiteCount(rawUsage.output_tokens, Number.MAX_SAFE_INTEGER, 'prior_usage.output_tokens'),
  };
  const priorActiveDurationMs = finiteCount(
    raw.prior_active_duration_ms,
    900_000,
    'prior_active_duration_ms',
  );
  return {
    sourceTaskId,
    ...parseCompletedSteps(raw.completed_steps ?? [], workflow, goal, 'interaction_resume.completed_steps', 0, code),
    priorModelCalls,
    priorUsage,
    priorActiveDurationMs,
    interaction: { stepId, kind, value: valueText },
  };
}

function resolvedHumanInputEntries(
  workflow: WorkflowDefinition,
  resume: ResumeState | null,
): string[] {
  if (!resume) return [];
  const entries: string[] = [];
  for (const step of workflow.steps) {
    if (step.type !== 'human_input' || !step.output) continue;
    const value = resume.interaction?.kind === 'human_input' && resume.interaction.stepId === step.id
      ? resume.interaction.value
      : resume.completedStepIds.has(step.id)
        ? resume.inputs.get(step.output)
        : undefined;
    if (value) entries.push(`[${step.id}] ${value}`);
  }
  return entries;
}

function attachResolvedHumanInputs(
  workflow: WorkflowDefinition,
  sinkId: string,
  entries: string[],
): void {
  if (entries.length === 0) return;
  const sink = workflow.steps.find(step => step.id === sinkId);
  if (!sink) return;
  const resolved = entries.join('\n');
  sink.task = `${sink.task}\n\n${RESOLVED_HUMAN_INPUT_HEADER}\n${resolved}\n\nThese values were supplied directly by the user and may be treated as confirmed facts. Model-generated dependency outputs remain derived analysis.${closedListTaskContract(resolved)}`;
  sink.acceptance = `${sink.acceptance ?? ''}\n- Preserve every fact in the explicitly labeled resolved human_input block accurately. Do not relabel those user-provided values as TBD, omit them, or replace them with model-derived values.${closedListAcceptanceContract(resolved)}`;
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
    'agency_execution_quality_failed',
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
  const protocol = request.protocol === AGENCY_HITL_PROTOCOL
    ? AGENCY_HITL_PROTOCOL
    : request.protocol === AGENCY_EXECUTION_PROTOCOL
      ? AGENCY_EXECUTION_PROTOCOL
      : null;
  if (!protocol || request.method !== 'execute') {
    throw new AgencyBridgeError('worker_method_invalid', 'Execution requires bridge protocol v2 or v3.');
  }
  const params = request.params;
  const goal = stringField(params, 'goal', 20_000);
  const modelId = stringField(params, 'model_id', 300);
  const agents = parseExperts(params.agents);
  const skills = parseSkills(params.skills);
  const allowHitl = protocol === AGENCY_HITL_PROTOCOL;
  const { workflow, sinkId } = parseWorkflow(params.workflow, agents, skills, modelId, allowHitl, goal);
  const approvalDraftStepIds = new Set(
    workflow.steps
      .filter(step => step.type === 'approval')
      .flatMap(step => step.depends_on ?? []),
  );
  const continuationCount = [params.resume, params.revision, params.interaction_resume]
    .filter(value => value !== undefined && value !== null).length;
  if (continuationCount > 1) {
    throw new AgencyBridgeError('agency_execution_plan_invalid', 'Execution continuations are mutually exclusive.');
  }
  const resume = parseResume(params.resume, workflow, goal)
    ?? parseRevision(params.revision, workflow, goal)
    ?? parseInteractionResume(params.interaction_resume, workflow, goal);
  const resolvedHumanInputs = resolvedHumanInputEntries(workflow, resume);
  attachResolvedHumanInputs(workflow, sinkId, resolvedHumanInputs);
  const authoritativeInputs = `${goal}\n${resolvedHumanInputs.join('\n')}`;
  const router = new ModelResponseRouter(channel, request.id, protocol);
  const connector = new ExecutionBridgeConnector(router, request.id, modelId, protocol, resume ? {
    calls: resume.priorModelCalls,
    usage: resume.priorUsage,
  } : undefined);
  const stepResults: StepResult[] = [];
  const segmentStarted = Date.now();
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
  }, protocol);
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
      resolveInteraction: allowHitl
        ? async (interaction: InteractionRequest): Promise<string> => {
            if (
              resume?.interaction
              && resume.interaction.stepId === interaction.stepId
              && resume.interaction.kind === interaction.kind
            ) return resume.interaction.value;
            throw new InteractionRequired(interaction);
          }
        : undefined,
      feedback: resume?.revision ? {
        stepId: resume.revision.targetTaskId,
        text: resume.revision.feedback,
        previousOutput: resume.revision.previousOutput,
      } : undefined,
      resolveAgent: agentResolver(agents),
      resolveSkill: skillResolver(skills),
      prepareTemplateContext: sinkTemplateContext(workflow, sinkId),
      normalizeOutput: (node, output) => normalizeClosedOutputWrapperTitle(
        authoritativeInputs,
        approvalDraftStepIds.has(node.step.id) || node.step.id === sinkId
          ? normalizeClosedFactQna(
            authoritativeInputs,
            normalizeClosedMaterialList(authoritativeInputs, output),
            node.step.id === sinkId,
          )
          : normalizeClosedMaterialList(authoritativeInputs, output),
      ),
      validateOutput: executionOutputValidator(
        sinkId,
        approvalDraftStepIds,
        goal,
        resolvedHumanInputs.join('\n'),
      ),
      rejectUnverifiedOutput: node => (
        approvalDraftStepIds.has(node.step.id) && node.verification?.pass !== true
          ? new AgencyBridgeError(
            'agency_execution_quality_failed',
            `agency_execution_quality_failed: Approval draft step "${node.step.id}" did not pass acceptance verification after one rework. Retry the failed step before asking for approval.`,
          )
          : null
      ),
      stepResultsSink: stepResults,
      onBatchStart: nodes => emit(channel, request.id, {
        event: 'agency.batch.started',
        task_ids: nodes.map(node => node.step.id),
      }, protocol),
      onStepStart: node => emit(channel, request.id, {
        event: 'agency.step.started',
        task_id: node.step.id,
        agent_id: node.step.role,
        depends_on: node.step.depends_on ?? [],
        acceptance: node.step.acceptance,
        status: 'running',
      }, protocol),
      onStepComplete: node => {
        if (node.step.id === sinkId && node.status === 'completed' && node.result?.trim()) {
          node.result = applyFactBoundary(node.result, goal);
          const stored = stepResults.find(step => step.id === sinkId);
          if (stored) stored.output = node.result;
        }
        emit(channel, request.id, stepEvent(node, connector, resume), protocol);
        if (node.verification) {
          emit(channel, request.id, {
            event: 'agency.verification.completed',
            task_id: node.step.id,
            verification: node.verification,
          }, protocol);
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
      active_duration_ms: (resume?.priorActiveDurationMs ?? 0) + (Date.now() - segmentStarted),
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
    }, protocol);
    return payload;
  } catch (error) {
    if (error instanceof InteractionRequired && allowHitl) {
      const completedSteps = stepResults
        .filter(step => step.status === 'completed' && step.output && step.output_var)
        .map(step => ({
          task_id: step.id,
          output: step.output,
          output_variable: step.output_var,
          acceptance: step.acceptance,
          agent_name: step.agentName,
          agent_emoji: step.agentEmoji,
        }));
      const activeDurationMs = (resume?.priorActiveDurationMs ?? 0) + (Date.now() - segmentStarted);
      const wait = {
        step_id: error.request.stepId,
        kind: error.request.kind,
        prompt: error.request.prompt.slice(0, 4_000),
        content_preview: error.request.content.slice(0, 8_000),
        output_variable: error.request.outputVariable,
      };
      emit(channel, request.id, {
        event: 'agency.interaction.pending',
        task_id: error.request.stepId,
        status: 'waiting',
        request_type: error.request.kind,
        model_calls: connector.calls,
        cumulative_usage: { ...connector.usage },
      }, protocol);
      return {
        success: false,
        status: 'waiting',
        wait,
        completed_steps: completedSteps,
        model_calls: connector.calls,
        usage: connector.usage,
        active_duration_ms: activeDurationMs,
        upstream_revision: AGENCY_UPSTREAM_REVISION,
      };
    }
    emit(channel, request.id, {
      event: 'agency.run.failed',
      status: 'failed',
      error: error instanceof Error ? error.message.slice(0, 4_000) : String(error).slice(0, 4_000),
      model_calls: connector.calls,
      usage: connector.usage,
    }, protocol);
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
    max_tokens_per_call: MAX_EXECUTION_OUTPUT_TOKENS,
    timeout_seconds: 900,
    max_output_bytes: MAX_EXECUTION_OUTPUT_BYTES,
  };
}
