import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import type { RoleSummary } from '../../vendor/agency-orchestrator/src/cli/compose.js';
import { composeWorkflow } from '../../vendor/agency-orchestrator/src/cli/compose.js';
import { setConnectorFactory, resetConnectorFactory } from '../../vendor/agency-orchestrator/src/connectors/factory.js';
import { buildDAG } from '../../vendor/agency-orchestrator/src/core/dag.js';
import { parseWorkflow, validateWorkflow } from '../../vendor/agency-orchestrator/src/core/parser.js';
import type { WorkflowDefinition } from '../../vendor/agency-orchestrator/src/types.js';

import { BridgeConnector } from './bridge_connector.js';
import { JsonlChannel } from './channel.js';
import {
  AGENCY_BRIDGE_PROTOCOL,
  AGENCY_UPSTREAM_REVISION,
  AgencyBridgeError,
  BridgeRequest,
  MAX_MESSAGE_BYTES,
  MAX_MODEL_CALLS,
  asObject,
} from './protocol.js';

interface ExpertDefinition {
  id: string;
  path: string;
  name: string;
  department: string;
  description: string;
  system_prompt: string;
  emoji?: string;
}

function stringField(
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

function parseExperts(value: unknown): ExpertDefinition[] {
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
    const known = new Set(agents.map(agent => agent.id));
    for (const step of workflow.steps) {
      if (step.role && !known.has(step.role)) {
        errors.push(`step "${step.id}" references unknown ModelMirror agent "${step.role}"`);
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
  const boundedGoal = pins
    ? goal
    : `${goal}\n\nModelMirror constraint: select at most ${maxAgents} experts from the catalog.`;
  setConnectorFactory(() => connector);
  try {
    const generated = await composeWorkflow({
      description: boundedGoal,
      agentsDir: '',
      roles: roleCatalog(agents),
      llmConfig: {
        provider: 'modelmirror',
        model: modelId,
        max_tokens: 4096,
        temperature,
        retry: 0,
      },
      outputName: 'plan.yaml',
      saveDir: outputRoot,
      agentsDirName: 'modelmirror-experts',
      pinnedRoles: pins,
      lang: 'zh',
    });
    const validation = validateWorkflowText(generated.yaml, agents);
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
      yaml: generated.yaml,
      warnings: generated.warnings,
      repair_used: generated.repairUsed,
      model_calls: connector.calls,
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
      methods: ['health', 'compose', 'validate'],
      max_message_bytes: MAX_MESSAGE_BYTES,
      max_model_calls: MAX_MODEL_CALLS,
    };
  }
  if (request.method === 'validate') {
    const yamlText = stringField(request.params, 'yaml', MAX_MESSAGE_BYTES - 1024);
    return validateWorkflowText(yamlText, parseExperts(request.params.agents));
  }
  return compose(request, channel);
}
