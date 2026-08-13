import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

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
import {
  AGENCY_BRIDGE_PROTOCOL,
  AGENCY_UPSTREAM_REVISION,
  AgencyBridgeError,
  BridgeRequest,
  MAX_MESSAGE_BYTES,
  MAX_MODEL_CALLS,
  MAX_PLANNING_OUTPUT_TOKENS,
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

const MAX_WORKFLOW_STEPS = 8;

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

function validateWorkflowText(yamlText: string, agents: ExpertDefinition[]): Record<string, unknown> {
  const root = mkdtempSync(join(tmpdir(), 'mm-agency-validate-'));
  const workflowPath = join(root, 'workflow.yaml');
  try {
    writeFileSync(workflowPath, yamlText, 'utf8');
    const workflow = parseWorkflow(workflowPath);
    const errors = validateWorkflow(workflow);
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
    for (const step of workflow.steps) {
      if (step.type === 'approval' || step.type === 'human_input') {
        errors.push(
          `step "${step.id}" uses unsupported type "${step.type}" in Expert Team preview`,
        );
      }
      if (step.role && !known.has(step.role)) {
        errors.push(`step "${step.id}" references unknown ModelMirror agent "${step.role}"`);
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

async function repairInvalidWorkflowOnce(options: {
  connector: BridgeConnector;
  yaml: string;
  validation: Record<string, unknown>;
  llmConfig: LLMConfig;
  allowedRoleIds: string[];
  pinned: boolean;
  maxAgents: number;
  goal: string;
}): Promise<string | null> {
  const errors = validationErrors(options.validation);
  if (errors.length === 0 || options.connector.calls >= MAX_MODEL_CALLS) return null;
  const systemPrompt = `You repair Agency Orchestrator workflow YAML for ModelMirror Expert Team preview.
Return one complete YAML code block and nothing else. Preserve the workflow goal and useful task intent.
Fix every validator error. The result must have 1-${MAX_WORKFLOW_STEPS} regular expert steps, an acyclic DAG,
unique step ids and output variables, scalar-string acceptance fields, boolean verify fields, and variable
references sourced only from upstream step outputs. Do not create a top-level inputs section and do not use
approval or human_input steps. Embed the supplied planning goal directly into the regular expert task text.
Every final DAG sink step must define concrete, non-empty acceptance criteria.
Do not invent dates, markets, traffic, user counts, infrastructure, cloud providers, databases, GPUs, or named people.
When the goal does not provide such facts, label them as pending assumptions instead of presenting them as facts.
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

  const connector = new BridgeConnector(channel, request.id);
  const outputRoot = mkdtempSync(join(tmpdir(), 'mm-agency-compose-'));
  const boundedGoal = `${goal}\n\nModelMirror constraints: select at most ${maxAgents} experts from the catalog; `
    + `generate no more than ${MAX_WORKFLOW_STEPS} regular expert task steps; do not generate approval or human_input steps. `
    + 'Every acceptance value must be a YAML scalar string, every final step must have non-empty acceptance criteria, '
    + 'and the DAG must be acyclic. Do not invent dates, markets, user counts, traffic, infrastructure, cloud providers, '
    + 'databases, GPUs, or named people. Mark missing facts as pending assumptions and use role names instead of personal names. '
    + 'Keep the workflow YAML compact: include only facts relevant to each step, do not repeat the entire goal in every task, '
    + 'and keep each acceptance criterion to one checkable line.';
  setConnectorFactory(() => connector);
  try {
    const generated = await composeWorkflow({
      description: boundedGoal,
      agentsDir: '',
      roles: roleCatalog(agents),
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
    });
    let finalYaml = generated.yaml;
    let validation = validateWorkflowText(finalYaml, agents);
    const warnings = [...generated.warnings];
    let repairUsed = generated.repairUsed;
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
          });
          if (repaired) {
            finalYaml = repaired;
            validation = validateWorkflowText(finalYaml, agents);
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
    const workflow = asObject(validationObject.workflow);
    const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
    const selectedIds = [...new Set(
      steps.map(step => asObject(step).role).filter(role => typeof role === 'string'),
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
