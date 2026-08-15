import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import yaml from 'js-yaml';

import type { RoleSummary } from '../../vendor/agency-orchestrator/src/cli/compose.js';
import {
  composeWorkflow,
  extractYamlFromResponse,
} from '../../vendor/agency-orchestrator/src/cli/compose.js';
import { setConnectorFactory, resetConnectorFactory } from '../../vendor/agency-orchestrator/src/connectors/factory.js';
import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import { parseWorkflow, validateWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';
import type {
  LLMConfig,
  WorkflowDefinition,
} from '../../vendor/agency-orchestrator/src/types.js';

import { handleAssetRequest } from './assets.js';
import { BridgeConnector } from './bridge_connector.js';
import { JsonlChannel } from './channel.js';
import { extractClosedAuthoritativeFactSet } from './fact_contract.js';
import {
  AGENCY_BRIDGE_PROTOCOL,
  AGENCY_UPSTREAM_REVISION,
  AgencyBridgeError,
  BridgeRequest,
  MAX_MESSAGE_BYTES,
  MAX_MODEL_CALLS,
  MAX_PLANNING_OUTPUT_TOKENS,
  MAX_EXECUTION_STEPS,
  asObject,
} from './protocol.js';

export interface ExpertDefinition {
  id: string;
  path: string;
  name: string;
  department: string;
  description: string;
  system_prompt: string;
  emoji?: string;
}

const MAX_WORKFLOW_STEPS = MAX_EXECUTION_STEPS;
const RESERVED_CONTEXT_VARIABLES = new Set(['user_input', 'goal', '_loop_iteration']);

export function stringField(
  source: Record<string, unknown>,
  name: string,
  maxLength: number,
): string {
  const value = typeof source[name] === 'string' ? source[name].trim() : '';
  if (!value || value.length > maxLength) {
    throw new AgencyBridgeError('worker_request_invalid', `${name} is invalid.`);
  }
  return value;
}

export function parseExperts(value: unknown): ExpertDefinition[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 512) {
    throw new AgencyBridgeError('worker_request_invalid', 'agents must contain 1-512 experts.');
  }
  const seen = new Set<string>();
  return value.map((raw) => {
    const item = asObject(raw, 'worker_request_invalid');
    const id = stringField(item, 'id', 160);
    const path = stringField(item, 'path', 160);
    if (path !== id) {
      throw new AgencyBridgeError('worker_request_invalid', 'Expert path must equal the real ModelMirror agent id.');
    }
    if (seen.has(id)) {
      throw new AgencyBridgeError('duplicate_agent', `Duplicate expert id: ${id}`);
    }
    seen.add(id);
    return {
      id,
      path,
      name: stringField(item, 'name', 200),
      department: stringField(item, 'department', 120),
      description: stringField(item, 'description', 2_000),
      system_prompt: stringField(item, 'system_prompt', 16_000),
      emoji: typeof item.emoji === 'string' ? item.emoji.slice(0, 16) : undefined,
    };
  });
}

function roleCatalog(agents: ExpertDefinition[]): RoleSummary[] {
  return agents.map(agent => ({
    path: agent.path,
    name: agent.name,
    emoji: agent.emoji,
    description: agent.description,
    category: agent.department,
  }));
}

function serializeDag(workflow: WorkflowDefinition): Record<string, unknown> {
  const dag = buildDAG(workflow);
  return {
    levels: dag.levels,
    nodes: [...dag.nodes.values()].map(node => ({
      id: node.step.id,
      role: node.step.role,
      dependencies: node.dependencies,
      dependents: node.dependents,
    })),
  };
}

export function validatePlainTextTemplateReferences(workflow: WorkflowDefinition): string[] {
  const errors: string[] = [];
  for (const step of workflow.steps) {
    const fields = [step.task, step.prompt, typeof step.acceptance === 'string' ? step.acceptance : undefined]
      .filter((value): value is string => Boolean(value));
    const reported = new Set<string>();
    for (const field of fields) {
      for (const match of field.matchAll(/\{\{\s*([A-Za-z_]\w*)\s*(?:\.|\[)[^}]+\}\}/gu)) {
        const reference = match[0];
        if (reported.has(reference)) continue;
        reported.add(reference);
        errors.push(
          `step "${step.id}" uses unsupported structured template reference ${reference}; ModelMirror step outputs are plain text, so reference the whole upstream output variable instead`,
        );
      }
    }
  }
  return errors;
}

export function validateUncertaintyPolicy(
  goal: string,
  workflow: WorkflowDefinition,
): string[] {
  const normalizedGoal = goal.toLowerCase();
  const requiresPendingMarkers = (
    ['缺失信息', '未知信息', '未提供信息', '未明确的信息', 'missing information', 'unknown information', 'unprovided information']
      .some(signal => normalizedGoal.includes(signal))
    && ['待确认', 'tbd', 'pending'].some(marker => normalizedGoal.includes(marker))
  );
  const byId = new Map(workflow.steps.map(step => [step.id, step]));
  if (!requiresPendingMarkers) return [];
  const hasHumanInputAncestor = (stepId: string): boolean => {
    const seen = new Set<string>();
    const pending = [...(byId.get(stepId)?.depends_on ?? [])];
    while (pending.length > 0) {
      const current = pending.pop()!;
      if (seen.has(current)) continue;
      seen.add(current);
      const dependency = byId.get(current);
      if (dependency?.type === 'human_input') return true;
      pending.push(...(dependency?.depends_on ?? []));
    }
    return false;
  };
  const resolvesPendingMarker = (value: string): boolean => {
    const normalized = value.toLowerCase();
    if (/(?:无|不(?:含|包含|保留|允许|出现))[^\n]{0,16}(?:待定|待确认|待补充|待填写|占位|tbd|pending)|(?:no|without)[^\n]{0,16}(?:tbd|pending|placeholder)/iu.test(normalized)) {
      return true;
    }
    const actions = ['替换', '填充', '移除', '消除', '解决', '清除', 'replace', 'fill', 'remove', 'resolve'];
    if (/(?:替换|填充|移除|消除|解决|清除|replace|fill|remove|resolve)[^\n]{0,24}(?:待确认|待补充|待填写|待定|占位|tbd|pending|placeholder)/iu.test(normalized)) {
      return true;
    }
    return ['待确认', '待补充', '待填写', '待定', '占位', 'tbd', 'pending', 'placeholder'].some(marker => {
      let markerIndex = normalized.indexOf(marker);
      while (markerIndex >= 0) {
        const markerPhrase = normalized.slice(markerIndex, markerIndex + 48);
        if (actions.some(action => markerPhrase.includes(action))) return true;
        markerIndex = normalized.indexOf(marker, markerIndex + marker.length);
      }
      return false;
    });
  };

  return workflow.steps.flatMap(step => {
    if (step.type === 'human_input' || hasHumanInputAncestor(step.id)) return [];
    const text = `${step.task ?? ''}\n${step.acceptance ?? ''}`;
    return resolvesPendingMarker(text)
      ? [`step "${step.id}" requires unresolved TBD/pending placeholders to be removed without preceding human_input; approval does not supply missing external facts${requiresPendingMarkers ? ' and this also contradicts the user\'s explicit uncertainty policy' : ''}`]
      : [];
  });
}

export function validateGoalProhibitions(
  goal: string,
  workflow: WorkflowDefinition,
): string[] {
  const forbidsConcreteStaffing = /(?:禁止|不得|不要|不(?:执行|进行|生成|制作|安排)).{0,16}(?:真实|实际|具体)?(?:人员)?排班/u.test(goal);
  if (!forbidsConcreteStaffing) return [];

  const errors: string[] = [];
  const isNegated = (text: string, index: number): boolean => {
    const before = text.slice(Math.max(0, index - 20), index);
    return /(?:不|禁止|不得|不要|避免).{0,10}$/u.test(before)
      || /(?:do not|don't|never|must not|avoid).{0,12}$/i.test(before);
  };
  for (const step of workflow.steps) {
    if (step.type === 'human_input' || step.type === 'approval') continue;
    const text = `${step.task ?? ''}\n${step.acceptance ?? ''}`;
    const aliases = [
      ...text.matchAll(/志愿者\s*[A-ZＡ-Ｚ](?![A-Za-z0-9Ａ-Ｚａ-ｚ０-９])/giu),
      ...text.matchAll(/[\p{Script=Han}]{1,12}(?:员|者)\s*[A-ZＡ-Ｚ](?![A-Za-z0-9Ａ-Ｚａ-ｚ０-９])/gu),
      ...text.matchAll(/volunteer\s+[A-Z](?![A-Za-z0-9])/giu),
      ...text.matchAll(/(?:person|clerk|worker|staff member)\s+[A-Z](?![A-Za-z0-9])/giu),
    ];
    const requestsIndividualAliases = aliases.some(match => {
      const negatedInsideMatch = /(?:不|禁止|不得|不要|避免).{0,10}(?:员|者)/u.test(match[0]);
      return !negatedInsideMatch && !isNegated(text, match.index ?? 0);
    });
    const assignmentMatches = [
      ...text.matchAll(/(?:将|把).{0,30}(?:志愿者|人员).{0,30}(?:分配|安排).{0,30}(?:日期|工作日|班次)/gu),
      ...text.matchAll(/assign.{0,30}(?:volunteers?|people).{0,30}(?:dates?|days?|shifts?)/gi),
    ];
    const requestsConcreteAssignment = assignmentMatches.some(match => !isNegated(text, match.index ?? 0));
    if (requestsIndividualAliases || requestsConcreteAssignment) {
      errors.push(
        `step "${step.id}" contradicts the user's prohibition on real staffing by requesting individual aliases or assignments; use role slots and capacity placeholders marked TBD without assigning people to dates or shifts`,
      );
    }
  }
  return errors;
}

export function validateGoalContentProhibitions(
  goal: string,
  workflow: WorkflowDefinition,
): string[] {
  const clauses = [...goal.matchAll(
    /(?:不得|禁止|不要|不允许)(?:再)?(?:新增|包含|引入|提供)?\s*([^。；;\n]{2,220})/gu,
  )].map(match => match[1] ?? '');
  const prohibited = clauses.join('、');
  if (!prohibited) return [];
  const prohibitedBudgetTerms = ['预算', '金额', '费用', '成本']
    .filter(term => prohibited.includes(term));
  const prohibitedBudgetPattern = new RegExp(
    `(?:${prohibitedBudgetTerms.join('|')}|\\d+\\s*(?:元|万元))`,
    'u',
  );
  const categories = [
    {
      name: 'dates or durations',
      enabled: /(?:日期|日程|响应时限|时限|持续时间|处理时长|周期)/u.test(prohibited),
      pattern: /(?:日期|日程|响应时限|持续时间|处理时长|\d+\s*(?:分钟|小时|天|日))/u,
    },
    {
      name: 'headcounts',
      enabled: /(?:人数|用户数|人次|受影响用户)/u.test(prohibited),
      pattern: /(?:人数|用户数|人次|受影响[^。；;\n]{0,16}用户|\d+\s*(?:人|名用户|位用户)|(?:(?:数|几)(?:十|百|千|万)|成百上千|成千上万|许多|大量)[^。；;\n]{0,8}(?:人|同事|员工|用户|参与者|陌生人))/u,
    },
    {
      name: 'budgets',
      enabled: prohibitedBudgetTerms.length > 0,
      pattern: prohibitedBudgetPattern,
    },
    {
      name: 'software or applications',
      enabled: /(?:软件|应用)/u.test(prohibited),
      pattern: /(?:使用|采用|安装|引入|新增|购买|部署|配置)[^。；;\n]{0,24}(?:软件|应用(?:程序)?)|(?:软件|应用程序)(?:名称|清单|工具|系统|平台|版本|账号|[：:])/u,
    },
    {
      name: 'systems or platforms',
      enabled: /(?:系统|平台)/u.test(prohibited),
      pattern: /(?:系统|平台)/u,
    },
    {
      name: 'tools',
      enabled: /工具/u.test(prohibited),
      pattern: /工具/u,
    },
    {
      name: 'notification or contact channels',
      enabled: /(?:通知渠道|联系渠道|联系方式|电话|邮箱|邮件|IM|群组|短信|手机)/iu.test(prohibited),
      pattern: /(?:通知渠道|联系渠道|联系方式|电话|邮箱|邮件|IM|群组|短信|手机)/iu,
    },
    {
      name: 'real execution actions',
      enabled: /(?:执行真实操作|真实操作|外部操作|执行动作|真实行动)/u.test(prohibited),
      pattern: /(?:(?:执行|启动|实施)(?:升级|修复|操作|流程)|(?:立即|依次|实际|自动|直接)(?:联系|转接|通知|发送|拨打|修改|发布|写入)|(?:→|则|后)\s*(?:联系|转接|通知|发送|拨打|修改|发布|写入)|记录并监控)/u,
    },
  ].filter(category => category.enabled);
  if (categories.length === 0) return [];
  const errors: string[] = [];
  const negated = /(?:不得|禁止|请勿|勿|不要|不允许|不应|不包含|未包含|(?:未|没有(?:任何)?)(?:新增|添加|引入|包含|提供|虚构|编造|杜撰)(?:的)?|(?:没有|无)(?:任何)?[^。；;\n]{0,120}(?:额外|新增|添加)(?:的)?(?:信息|字段|内容)?|不添加|不使用|不执行|不联系|不通知|不发送|不修改|不发布|不虚构|不编造|不杜撰|仅描述|不授权)/u;
  const lineIsNegated = (lines: string[], index: number): boolean => {
    if (negated.test(lines[index] ?? '')) return true;
    if (!/^\s*[-*+]\s+/u.test(lines[index] ?? '')) return false;
    for (let cursor = index - 1; cursor >= Math.max(0, index - 10); cursor -= 1) {
      const prior = lines[cursor] ?? '';
      if (!prior.trim() || /^\s*[-*+]\s+/u.test(prior)) continue;
      return negated.test(prior);
    }
    return false;
  };
  for (const step of workflow.steps) {
    if (step.type === 'human_input' || step.type === 'approval') continue;
    const lines = `${step.task ?? ''}\n${step.acceptance ?? ''}`.split(/\r?\n/u);
    for (const category of categories) {
      const violatingLine = lines.find((line, index) => category.pattern.test(line) && !lineIsNegated(lines, index));
      if (violatingLine) {
        errors.push(
          `step "${step.id}" positively requests prohibited ${category.name}: ${violatingLine.trim().slice(0, 180)}; remove the field/action instead of filling it as TBD`,
        );
      }
    }
  }
  return [...new Set(errors)];
}

export function validateGoalDurationUnits(
  goal: string,
  workflow: WorkflowDefinition,
): string[] {
  const pattern = /(\d+(?:\.\d+)?)\s*(?:个)?(工作日|自然日|天|年|月|周|小时|分钟|business\s+days?|calendar\s+days?|days?|years?|months?|weeks?|hours?|minutes?)/giu;
  const canonicalUnit = (unit: string): string => unit.toLowerCase().replace(/\s+/g, '');
  const goalUnits = new Map<string, Set<string>>();
  for (const match of goal.matchAll(pattern)) {
    const units = goalUnits.get(match[1]) ?? new Set<string>();
    units.add(canonicalUnit(match[2]));
    goalUnits.set(match[1], units);
  }
  if (goalUnits.size === 0) return [];
  const errors: string[] = [];
  const goalDefinesExtensionPolicy = /(?:顺延|延期|延后|延至|next\s+business\s+day|roll(?:ed)?\s+over|extension\s+policy)/iu.test(goal);
  for (const step of workflow.steps) {
    const text = `${step.task ?? ''}\n${step.acceptance ?? ''}\n${step.prompt ?? ''}`;
    for (const line of text.split(/\r?\n/)) {
      if (/(?:待确认|建议|示例|假设|proposal|tbd|pending)/iu.test(line)) continue;
      if (!goalDefinesExtensionPolicy && /(?:顺延|延期|延后|延至|next\s+business\s+day|roll(?:ed)?\s+over)/iu.test(line)) {
        errors.push(
          `step "${step.id}" silently adds a deadline-extension policy; remove it or mark it as a proposal/TBD unless the user explicitly supplied it`,
        );
      }
      for (const match of line.matchAll(pattern)) {
        const allowed = goalUnits.get(match[1]);
        const unit = canonicalUnit(match[2]);
        if (allowed && !allowed.has(unit)) {
          errors.push(
            `step "${step.id}" changes the user's duration ${match[1]} ${[...allowed].join('/')} to ${match[1]} ${unit}; preserve the exact unit or mark the alternative as a proposal/TBD`,
          );
        }
      }
    }
  }
  return [...new Set(errors)];
}

export function removeUnsupportedExtensionClauses(
  yamlText: string,
  goal: string,
): { yaml: string; changed: boolean } {
  if (/(?:顺延|延期|延后|延至|next\s+business\s+day|roll(?:ed)?\s+over|extension\s+policy)/iu.test(goal)) {
    return { yaml: yamlText, changed: false };
  }
  try {
    const doc = yaml.load(yamlText) as Record<string, unknown>;
    if (!doc || !Array.isArray(doc.steps)) return { yaml: yamlText, changed: false };
    let changed = false;
    const zhClause = /(?:如|若|遇|逢|法定节假日|节假日|非工作日)[^。；;\n]{0,50}(?:顺延|延期|延后|延至)[^。；;\n]{0,100}[。；;]?/giu;
    const enClause = /\b(?:if|when|on)?[^.\n]{0,60}(?:non[- ]business|weekend|holiday)[^.\n]{0,60}(?:roll(?:ed)?\s+over|extend(?:ed)?|next\s+business\s+day)[^.\n]{0,80}[.;]?/giu;
    for (const rawStep of doc.steps) {
      if (!rawStep || typeof rawStep !== 'object') continue;
      const step = rawStep as Record<string, unknown>;
      for (const field of ['task', 'acceptance', 'prompt'] as const) {
        if (typeof step[field] !== 'string') continue;
        const original = step[field] as string;
        const cleaned = original
          .replace(zhClause, '')
          .replace(enClause, '')
          .replace(/[ \t]{2,}/g, ' ')
          .trim();
        if (cleaned && cleaned !== original.trim()) {
          step[field] = cleaned;
          changed = true;
        }
      }
    }
    return changed
      ? { yaml: yaml.dump(doc, { lineWidth: -1, noRefs: true }), changed: true }
      : { yaml: yamlText, changed: false };
  } catch {
    return { yaml: yamlText, changed: false };
  }
}

export function normalizeAllowedResourceAcceptance(
  yamlText: string,
  goal: string,
): { yaml: string; changed: boolean } {
  const explicitlyBindsEveryAction = /(?:所有|全部|每(?:个|项))[^。；;\n]{0,24}(?:动作|检查项|步骤|操作|活动)[^。；;\n]{0,24}(?:必须|仅(?:能|可|限)?|只能)[^。；;\n]{0,40}(?:使用|通过|依赖)/u.test(goal);
  if (explicitlyBindsEveryAction) return { yaml: yamlText, changed: false };
  try {
    const doc = yaml.load(yamlText) as Record<string, unknown>;
    if (!doc || !Array.isArray(doc.steps)) return { yaml: yamlText, changed: false };
    let changed = false;
    for (const rawStep of doc.steps) {
      if (!rawStep || typeof rawStep !== 'object') continue;
      const step = rawStep as Record<string, unknown>;
      if (typeof step.acceptance !== 'string') continue;
      const original = step.acceptance;
      const lines = original.split(/\r?\n/).map(line => line.replace(
        /^(\s*(?:(?:[-*]|\d+[.)、])\s*)?)(?:所有|全部|每(?:个|项))\s*(?:检查项|步骤|操作|动作)(?:均|都)?\s*(?:仅(?:能|可|限)?|只(?:能)?|必须)\s*(?:使用|通过|依赖(?:于)?)\s*([^。；;\n]{1,100}?)(?:两种|三种)?(?:现有)?(?:资源|工具|系统|介质)?\s*$/u,
        (_match, prefix: string, allowed: string) => `${prefix}检查清单不得引入${allowed.trim()}以外的新工具、软件或系统`,
      ));
      const normalized = lines.join('\n');
      if (normalized !== original) {
        step.acceptance = normalized;
        changed = true;
      }
    }
    return changed
      ? { yaml: yaml.dump(doc, { lineWidth: -1, noRefs: true }), changed: true }
      : { yaml: yamlText, changed: false };
  } catch {
    return { yaml: yamlText, changed: false };
  }
}

function validateWorkflowText(
  yamlText: string,
  agents: ExpertDefinition[],
  allowHitl = false,
  goal = '',
): Record<string, unknown> {
  const root = mkdtempSync(join(tmpdir(), 'mm-agency-validate-'));
  const workflowPath = join(root, 'workflow.yaml');
  try {
    writeFileSync(workflowPath, yamlText, 'utf8');
    const workflow = parseWorkflow(workflowPath);
    const errors = validateWorkflow(workflow);
    errors.push(...validatePlainTextTemplateReferences(workflow));
    if (goal) {
      errors.push(...validateUncertaintyPolicy(goal, workflow));
      errors.push(...validateGoalProhibitions(goal, workflow));
      errors.push(...validateGoalContentProhibitions(goal, workflow));
      errors.push(...validateGoalDurationUnits(goal, workflow));
    }
    if ((workflow.inputs?.length ?? 0) > 0) {
      errors.push(
        'top-level workflow inputs are unsupported in Expert Team preview; embed the planning goal directly in expert tasks',
      );
    }
    if (workflow.steps.length > MAX_WORKFLOW_STEPS) {
      errors.push(`workflow contains more than ${MAX_WORKFLOW_STEPS} steps`);
    }
    const known = new Set(agents.map(agent => agent.id));
    const dependedOn = new Set(
      workflow.steps.flatMap(step => step.depends_on ?? []),
    );
    const interactions = workflow.steps.filter(
      step => step.type === 'approval' || step.type === 'human_input',
    );
    const sinks = workflow.steps.filter(step => !dependedOn.has(step.id));
    if (interactions.length > 2) errors.push('workflow contains more than two HITL steps');
    if (sinks.length !== 1) {
      errors.push('workflow must have exactly one final DAG sink');
    } else if (sinks[0]?.type === 'approval' || sinks[0]?.type === 'human_input') {
      errors.push(`final DAG sink "${sinks[0].id}" must be a regular expert step, not a HITL step`);
    }
    for (const step of workflow.steps) {
      const interactive = step.type === 'approval' || step.type === 'human_input';
      if (interactive && !allowHitl) {
        errors.push(
          `step "${step.id}" uses unsupported type "${step.type}" in Expert Team preview`,
        );
      }
      if (interactive && allowHitl) {
        if (!step.prompt?.trim()) errors.push(`HITL step "${step.id}" must define a prompt`);
        if (step.role?.trim()) errors.push(`HITL step "${step.id}" cannot bind a role`);
        if (step.acceptance?.trim()) errors.push(`HITL step "${step.id}" cannot define acceptance`);
        if ((step.skills?.length ?? 0) > 0 || step.skill) {
          errors.push(`HITL step "${step.id}" cannot bind Skills`);
        }
      }
      if (step.role && !known.has(step.role)) {
        errors.push(`step "${step.id}" references unknown ModelMirror agent "${step.role}"`);
      }
      if (step.output && RESERVED_CONTEXT_VARIABLES.has(step.output)) {
        errors.push(`step "${step.id}" output "${step.output}" collides with a reserved ModelMirror context variable`);
      }
      if (
        !dependedOn.has(step.id)
        && step.type !== 'approval'
        && step.type !== 'human_input'
        && (typeof step.acceptance !== 'string' || !step.acceptance.trim())
      ) {
        errors.push(`final step "${step.id}" must define non-empty acceptance criteria`);
      }
    }
    if (allowHitl && interactions.length > 0) {
      const byId = new Map(workflow.steps.map(step => [step.id, step]));
      const children = new Map(workflow.steps.map(step => [step.id, [] as string[]]));
      for (const step of workflow.steps) {
        for (const dependency of step.depends_on ?? []) children.get(dependency)?.push(step.id);
      }
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
        const parallel = workflow.steps.find(
          other => other.id !== step.id && !ancestors.has(other.id) && !descendants.has(other.id),
        );
        if (parallel) errors.push(`HITL step "${step.id}" must be a full DAG barrier`);
      }
    }
    let dag: Record<string, unknown> | null = null;
    try {
      dag = serializeDag(workflow);
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error));
    }
    return { valid: errors.length === 0, errors, warnings: [], workflow, dag };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function validationErrors(validation: Record<string, unknown>): string[] {
  return Array.isArray(validation.errors)
    ? validation.errors.map(error => String(error)).slice(0, 20)
    : [];
}

function hostPlanningRules(allowHitl: boolean): string {
  const interactionRules = allowHitl
    ? `HITL is a durable checkpoint, so it overrides the upstream "parallel first" guidance.
Use at most two human_input or approval steps and preserve every checkpoint explicitly requested by the user, in order.
Each HITL step MUST be a full DAG barrier: every earlier step must be its ancestor and every later step must be its descendant.
To construct a barrier, set its depends_on to every current frontier/sink step before it, then make every first step after it depend on the HITL step.
Never leave an expert branch parallel to or bypassing a HITL step. HITL steps have prompt/output but no role, acceptance, Skill, model, or Provider override.
Never use user_input, goal, or _loop_iteration as a step output variable; those names are reserved by the host runtime.
ModelMirror step outputs are plain text. Never use dotted or indexed template references such as {{requirements.format}} or {{items[0]}}; reference the whole upstream output variable and state which value to use in plain language.
After the last HITL step, continue to exactly one regular expert final sink.`
    : 'Do not generate human_input or approval steps.';
  return `${interactionRules}
The workflow has 1-${MAX_WORKFLOW_STEPS} total steps, is acyclic, and has exactly one regular expert final sink.
That final sink has a concrete, non-empty scalar-string acceptance field.
Preserve the user's uncertainty policy in every task and acceptance criterion.
If the goal requires missing facts to remain TBD/pending and no preceding human_input step collects those facts, never require a later step to remove, replace, fill, or resolve those markers. Approval accepts or rejects the draft; it does not supply missing facts.
Never introduce concrete dates, durations, vendors, products, retention periods, thresholds, or other values absent from the goal or an upstream human_input output.
When the original goal explicitly declares that its facts "only include" a list, treat that list as exhaustive. Preserve it in downstream tasks and acceptance criteria; only a resolved human_input may add external facts, while model drafts and approvals may not.
Never silently qualify a user duration with working/calendar days, non-business-day rollover, grace periods, or deadline extensions absent from the goal. If useful, label the alternative inline as a proposal/TBD while preserving the user's exact rule.
A task may request proposed operating policies. Explicit approval of a visible draft may approve those policy choices for finalization, but it does not supply missing external facts, and the original goal overrides every conflict.
Treat a list of allowed artifacts, tools, or systems as a prohibition on introducing alternatives. Do not turn it into a requirement that every human observation, conversation, or physical check must be performed through those artifacts unless the user explicitly says every action must use them.
If the goal forbids real or actual staffing, a reusable template may contain only unlettered role slots and aggregate capacity. Never introduce individual aliases such as Volunteer A, Clerk A, or a lettered worker title, and never assign people to dates or shifts.
Do not invent a word-count or character-count limit unless the user explicitly supplied one.`;
}

async function repairInvalidWorkflowOnce(options: {
  connector: BridgeConnector;
  yaml: string;
  validation: Record<string, unknown>;
  llmConfig: LLMConfig;
  allowedRoleIds: string[];
  pinned: boolean;
  maxAgents: number;
  goal: string;
  allowHitl: boolean;
}): Promise<string | null> {
  const errors = validationErrors(options.validation);
  if (errors.length === 0 || options.connector.calls >= MAX_MODEL_CALLS) return null;
  const hitlRules = hostPlanningRules(options.allowHitl);
  const prohibitionRepairRules = errors.some(error => error.includes('prohibition on real staffing'))
    ? `The real-staffing prohibition is literal: remove every individual alias (for example Volunteer A, Clerk A, a lettered worker title, or a lettered person range) from task and acceptance text. Do not merely rename people. Use only unlettered role slots and aggregate capacity, mark unconfirmed slots TBD, and never assign a person or alias to a date or shift.`
    : '';
  const systemPrompt = `You repair Agency Orchestrator workflow YAML for ModelMirror Expert Team preview.
Return one complete YAML code block and nothing else. Preserve the workflow goal and useful task intent.
Fix every validator error. The result must have 1-${MAX_WORKFLOW_STEPS} total steps, an acyclic DAG,
unique step ids and output variables, scalar-string acceptance fields, boolean verify fields, and variable
references sourced only from upstream step outputs. Do not create a top-level inputs section. ${hitlRules}
Never use user_input, goal, or _loop_iteration as a step output variable; rename the output and every downstream template reference together.
ModelMirror step outputs are plain text. Replace every dotted or indexed template reference with the whole upstream output variable and describe the needed value in plain language.
Embed the supplied planning goal directly into the regular expert task text.
Do not replace the requested deliverable with a generic business plan or unrelated template. Preserve explicit numeric upper/lower bounds verbatim in the final task and acceptance criteria; never invert them.
Treat every explicit "do not add / 不得新增" list as a semantic prohibition across every intermediate expert task, not merely the final acceptance text. Never positively request, exemplify, or fill a prohibited field or action as TBD; omit it from the workflow content.
The workflow must have exactly one final DAG sink. It must be a regular expert step with concrete, non-empty acceptance criteria.
Do not invent dates, markets, traffic, user counts, infrastructure, cloud providers, databases, GPUs, or named people.
When the goal does not provide such facts, label them as pending assumptions instead of presenting them as facts.
${prohibitionRepairRules}
${options.pinned
    ? `Use exactly these role ids and keep every listed role represented: ${options.allowedRoleIds.join(', ')}.`
    : `Select at most ${options.maxAgents} role ids from this allowed set: ${options.allowedRoleIds.join(', ')}.`}`;
  const userPrompt = `Planning goal:\n${options.goal}\n\nValidator errors:\n${errors.map(error => `- ${error}`).join('\n')}\n\nPrevious YAML:\n\n\`\`\`yaml\n${options.yaml}\n\`\`\``;
  const result = await options.connector.chat(systemPrompt, userPrompt, {
    ...options.llmConfig,
    temperature: 0,
    max_tokens: options.llmConfig.max_tokens || MAX_PLANNING_OUTPUT_TOKENS,
  });
  const repaired = extractYamlFromResponse(result.content);
  return repaired && repaired.includes('steps:') ? repaired : null;
}

function pinnedIds(params: Record<string, unknown>, agents: ExpertDefinition[]): string[] | undefined {
  const mode = params.mode === 'pinned' ? 'pinned' : params.mode === 'auto' ? 'auto' : null;
  if (!mode) throw new AgencyBridgeError('worker_request_invalid', 'mode must be auto or pinned.');
  if (mode === 'auto') return undefined;
  if (!Array.isArray(params.pinned_agent_ids) || params.pinned_agent_ids.length === 0) {
    throw new AgencyBridgeError('worker_request_invalid', 'Pinned mode requires expert ids.');
  }
  const ids = params.pinned_agent_ids.map(value => String(value));
  if (new Set(ids).size !== ids.length) {
    throw new AgencyBridgeError('duplicate_agent', 'Pinned expert ids must be unique.');
  }
  const known = new Set(agents.map(agent => agent.id));
  const missing = ids.filter(id => !known.has(id));
  if (missing.length > 0) {
    throw new AgencyBridgeError('unknown_agent', `Unknown pinned expert ids: ${missing.join(', ')}`);
  }
  return ids;
}

async function compose(
  request: BridgeRequest,
  channel: JsonlChannel,
): Promise<Record<string, unknown>> {
  const params = request.params;
  const goal = stringField(params, 'goal', 20_000);
  const modelId = stringField(params, 'model_id', 256);
  const agents = parseExperts(params.agents);
  const pins = pinnedIds(params, agents);
  const maxAgents = Number(params.max_agents ?? 5);
  if (!Number.isInteger(maxAgents) || maxAgents < 1 || maxAgents > 6) {
    throw new AgencyBridgeError('worker_request_invalid', 'max_agents must be between 1 and 6.');
  }
  const temperature = Number(params.temperature ?? 0.2);
  if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
    throw new AgencyBridgeError('worker_request_invalid', 'temperature must be between 0 and 2.');
  }
  const allowHitl = params.allow_hitl === true;

  const connector = new BridgeConnector(channel, request.id);
  const outputRoot = mkdtempSync(join(tmpdir(), 'mm-agency-compose-'));
  const planningAgents = pins
    ? agents.filter(agent => pins.includes(agent.id))
    : agents;
  const hitlGoal = allowHitl
    ? 'Use at most two human_input or approval steps, only for genuinely missing required information or an explicit high-risk decision. When the goal explicitly asks to pause for user input, a choice, or approval, preserve every requested checkpoint in the same order; never replace a requested choice with generic intake or omit an approval. Each HITL step must be a full DAG barrier with prompt/output and without role, acceptance, or Skills. Continue after every HITL step to exactly one regular expert final sink. '
    : 'Do not generate approval or human_input steps. ';
  const closedFacts = extractClosedAuthoritativeFactSet(goal);
  const closedFactGoal = closedFacts
    ? `The original request defines this exhaustive authoritative fact set: ${closedFacts}. Preserve it explicitly in the final task and acceptance criterion. Do not add plausible factual or operational details outside it; only a resolved human_input may extend it, while a draft or approval may not. `
    : '';
  const boundedGoal = `${goal}\n\nModelMirror constraints: select at most ${maxAgents} experts from the catalog; `
    + `generate no more than ${MAX_WORKFLOW_STEPS} total steps. ${hitlGoal}`
    + closedFactGoal
    + 'The workflow must have exactly one final DAG sink, and it must be a regular expert step with a non-empty YAML scalar-string acceptance criterion. '
    + 'Never use user_input, goal, or _loop_iteration as a step output variable because those names are reserved by the host runtime. '
    + 'and the DAG must be acyclic. Do not invent dates, markets, user counts, traffic, infrastructure, cloud providers, '
    + 'databases, GPUs, or named people. Mark missing facts as pending assumptions and use role names instead of personal names. '
    + 'Do not replace the requested deliverable with a generic business plan or unrelated template. Preserve explicit numeric upper/lower bounds verbatim in the final task and acceptance criterion; never invert them. '
    + 'Never add working/calendar-day qualifications, non-business-day rollover, grace periods, or deadline extensions absent from the goal. Explicit approval of a visible draft may approve its policy choices for finalization, but it does not supply missing external facts and cannot override the goal. '
    + 'Treat named allowed artifacts, tools, or systems as a ban on introducing alternatives, not as a demand that every human observation, conversation, or physical check be performed through those artifacts unless the goal explicitly says so. '
    + 'Do not invent a word-count or character-count limit when the user goal does not explicitly contain one. '
    + 'Keep the workflow YAML compact: include only facts relevant to each step, do not repeat the entire goal in every task, '
    + 'and keep each acceptance criterion to one checkable line.';
  setConnectorFactory(() => connector);
  try {
    const generated = await composeWorkflow({
      description: boundedGoal,
      agentsDir: '',
      roles: roleCatalog(planningAgents),
      llmConfig: {
        provider: 'modelmirror',
        model: modelId,
        max_tokens: MAX_PLANNING_OUTPUT_TOKENS,
        temperature,
        retry: 0,
      },
      outputName: 'plan.yaml',
      autoRun: true,
      saveDir: outputRoot,
      agentsDirName: 'modelmirror-experts',
      pinnedRoles: pins,
      lang: 'zh',
      systemPromptAppendix: hostPlanningRules(allowHitl),
    });
    const normalizedExtensions = removeUnsupportedExtensionClauses(generated.yaml, goal);
    const normalizedResources = normalizeAllowedResourceAcceptance(normalizedExtensions.yaml, goal);
    let finalYaml = normalizedResources.yaml;
    let validation = validateWorkflowText(finalYaml, agents, allowHitl, goal);
    // Upstream warnings are unresolved validator errors, not an audit log.
    // Keep them only when they still describe the final candidate; otherwise
    // repair_used is the truthful trace that the initial YAML changed.
    let warnings = [...generated.warnings];
    let repairUsed = generated.repairUsed || normalizedExtensions.changed || normalizedResources.changed;
    const initialWorkflow = asObject(asObject(validation).workflow);
    const initialSteps = Array.isArray(initialWorkflow.steps) ? initialWorkflow.steps : [];
    const known = new Set(agents.map(agent => agent.id));
    const initialRoleIds = [...new Set(
      initialSteps
        .map(step => asObject(step).role)
        .filter(role => typeof role === 'string' && known.has(role)),
    )] as string[];
    const maxAgentsExceeded = !pins && initialRoleIds.length > maxAgents;
    if ((validation.valid !== true || maxAgentsExceeded) && connector.calls < MAX_MODEL_CALLS) {
      const repairValidation = maxAgentsExceeded
        ? {
          ...asObject(validation),
          valid: false,
          errors: [
            ...validationErrors(asObject(validation)),
            `Workflow selects ${initialRoleIds.length} experts but max_agents is ${maxAgents}.`,
          ],
        }
        : asObject(validation);
      const workflow = asObject(validation.workflow);
      const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
      const generatedRoleIds = [...new Set(
        steps
          .map(step => asObject(step).role)
          .filter(role => typeof role === 'string' && known.has(role)),
      )] as string[];
      const allowedRoleIds = pins ?? generatedRoleIds;
      if (allowedRoleIds.length > 0) {
        try {
          const repaired = await repairInvalidWorkflowOnce({
            connector,
            yaml: finalYaml,
            validation: repairValidation,
            llmConfig: {
              provider: 'modelmirror',
              model: modelId,
              max_tokens: MAX_PLANNING_OUTPUT_TOKENS,
              temperature,
              retry: 0,
            },
            allowedRoleIds,
            pinned: Boolean(pins),
            maxAgents,
            goal,
            allowHitl,
          });
          if (repaired) {
            finalYaml = repaired;
            validation = validateWorkflowText(finalYaml, agents, allowHitl, goal);
            repairUsed = true;
          }
        } catch (error) {
          warnings.push(
            `ModelMirror final validation repair failed: ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
    }
    const validationObject = asObject(validation);
    if (validationObject.valid === true) {
      warnings = warnings.filter(warning => !generated.warnings.includes(warning));
    }
    const workflow = asObject(validationObject.workflow);
    const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
    const selectedIds = [...new Set(
      steps.map(step => asObject(step).role).filter(
        role => typeof role === 'string' && known.has(role),
      ),
    )] as string[];
    if (pins) {
      const selected = new Set(selectedIds);
      if (pins.some(id => !selected.has(id)) || selectedIds.some(id => !pins.includes(id))) {
        throw new AgencyBridgeError(
          'pinned_roles_mismatch',
          'Generated workflow did not use exactly the complete pinned line-up.',
        );
      }
    } else if (selectedIds.length > maxAgents) {
      throw new AgencyBridgeError('max_agents_exceeded', 'Generated workflow selected too many experts.');
    }
    return {
      yaml: finalYaml,
      warnings,
      repair_used: repairUsed,
      model_calls: connector.calls,
      usage: connector.usage,
      validation,
      selected_agent_ids: selectedIds,
      selected_agents: agents.filter(agent => selectedIds.includes(agent.id)),
    };
  } finally {
    resetConnectorFactory();
    rmSync(outputRoot, { recursive: true, force: true });
  }
}

export async function handleRequest(
  request: BridgeRequest,
  channel: JsonlChannel,
): Promise<Record<string, unknown>> {
  if (request.method === 'health') {
    return {
      status: 'ok',
      protocol: AGENCY_BRIDGE_PROTOCOL,
      upstream_revision: AGENCY_UPSTREAM_REVISION,
      methods: ['health', 'compose', 'validate', 'assets'],
      max_message_bytes: MAX_MESSAGE_BYTES,
      max_model_calls: MAX_MODEL_CALLS,
    };
  }
  if (request.method === 'validate') {
    const yamlText = stringField(request.params, 'yaml', MAX_MESSAGE_BYTES - 1024);
    return validateWorkflowText(yamlText, parseExperts(request.params.agents));
  }
  if (request.method === 'assets') {
    return handleAssetRequest(request.params);
  }
  return compose(request, channel);
}
