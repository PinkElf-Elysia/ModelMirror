import { basename, isAbsolute, join, resolve } from 'node:path';

import {
  listTeams,
  saveTeam,
  slugify as teamSlugify,
  type TeamDefinition,
  type TeamRole,
} from '../../vendor/agency-orchestrator/src/cli/team.js';
import {
  PROMPT_GARDEN,
  appendVersion,
  listPrompts,
  savePrompt,
  slugify as promptSlugify,
  type PromptRecord,
} from '../../vendor/agency-orchestrator/src/cli/prompt.js';

import { AgencyBridgeError, asObject } from './protocol.js';

const MAX_TEAMS = 64;
const MAX_TEMPLATES = 64;

function requiredString(
  source: Record<string, unknown>,
  name: string,
  maxLength: number,
  minLength = 1,
): string {
  const value = typeof source[name] === 'string' ? source[name].trim() : '';
  if (value.length < minLength || value.length > maxLength) {
    throw new AgencyBridgeError('agency_asset_invalid', `${name} is invalid.`);
  }
  return value;
}

function optionalString(
  source: Record<string, unknown>,
  name: string,
  maxLength: number,
): string | undefined {
  if (source[name] === undefined || source[name] === null) return undefined;
  const value = typeof source[name] === 'string' ? source[name].trim() : '';
  if (value.length > maxLength) {
    throw new AgencyBridgeError('agency_asset_invalid', `${name} is invalid.`);
  }
  return value || undefined;
}

function assetDirectories(): { teams: string; prompts: string } {
  const raw = String(process.env.MM_AGENCY_ASSET_ROOT || '').trim();
  if (!raw || raw.length > 1_000 || !isAbsolute(raw)) {
    throw new AgencyBridgeError(
      'agency_asset_store_unavailable',
      'The host-owned Agency asset root is unavailable.',
    );
  }
  const root = resolve(raw);
  return {
    teams: join(root, 'teams'),
    prompts: join(root, 'prompts'),
  };
}

function parseRoles(value: unknown): TeamRole[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 6) {
    throw new AgencyBridgeError('agency_asset_invalid', 'roles must contain 1-6 experts.');
  }
  const seen = new Set<string>();
  return value.map((raw, index) => {
    const item = asObject(raw, 'agency_asset_invalid');
    const role = requiredString(item, 'role', 160);
    if (seen.has(role)) {
      throw new AgencyBridgeError('agency_asset_invalid', `Duplicate team role at index ${index}.`);
    }
    seen.add(role);
    return {
      role,
      name: optionalString(item, 'name', 200),
      emoji: optionalString(item, 'emoji', 16),
      note: optionalString(item, 'note', 500),
    };
  });
}

function publicTeam(file: string, team: TeamDefinition): Record<string, unknown> {
  return {
    ref: basename(file, '.team.yaml'),
    kind: team.kind,
    name: team.name,
    description: team.description,
    roles: team.roles,
    created: team.created,
    source: team.source,
  };
}

function publicPrompt(file: string, record: PromptRecord): Record<string, unknown> {
  const latest = record.versions.at(-1);
  return {
    ref: basename(file, '.prompt.json'),
    kind: record.kind,
    name: record.name,
    mode: record.mode,
    favorite: Boolean(record.favorite),
    content: latest?.content ?? '',
    note: latest?.note,
    version_count: record.versions.length,
    created: record.created,
    updated: latest?.created ?? record.created,
  };
}

function listAssets(): Record<string, unknown> {
  const directories = assetDirectories();
  return {
    teams: listTeams(directories.teams)
      .slice(0, MAX_TEAMS)
      .map(item => publicTeam(item.file, item.team)),
    templates: listPrompts(directories.prompts)
      .slice(0, MAX_TEMPLATES)
      .map(item => publicPrompt(item.file, item.record)),
    garden: PROMPT_GARDEN,
  };
}

function saveTeamAsset(params: Record<string, unknown>): Record<string, unknown> {
  const directories = assetDirectories();
  const raw = asObject(params.team, 'agency_asset_invalid');
  const name = requiredString(raw, 'name', 120);
  const existingTeams = listTeams(directories.teams);
  const reference = teamSlugify(name);
  const collision = existingTeams.find(
    item => basename(item.file, '.team.yaml') === reference && item.team.name !== name,
  );
  if (collision) {
    throw new AgencyBridgeError('agency_asset_invalid', 'A different team already uses this file reference.');
  }
  if (
    existingTeams.length >= MAX_TEAMS
    && !existingTeams.some(item => basename(item.file, '.team.yaml') === reference)
  ) {
    throw new AgencyBridgeError('agency_asset_invalid', 'The team asset limit has been reached.');
  }
  const team: TeamDefinition = {
    kind: 'team',
    name,
    description: optionalString(raw, 'description', 1_000),
    roles: parseRoles(raw.roles),
    created: new Date().toISOString().slice(0, 10),
    source: 'ModelMirror Expert Team',
  };
  const file = saveTeam(team, directories.teams);
  return { team: publicTeam(file, team) };
}

function saveTemplateAsset(params: Record<string, unknown>): Record<string, unknown> {
  const directories = assetDirectories();
  const raw = asObject(params.template, 'agency_asset_invalid');
  const name = requiredString(raw, 'name', 120);
  const content = requiredString(raw, 'content', 20_000, 10);
  const note = optionalString(raw, 'note', 500);
  const now = new Date().toISOString();
  const existingPrompts = listPrompts(directories.prompts);
  const existing = existingPrompts.find(
    item => basename(item.file, '.prompt.json') === promptSlugify(name),
  );
  if (existing && existing.record.name !== name) {
    throw new AgencyBridgeError('agency_asset_invalid', 'A different template already uses this file reference.');
  }
  if (!existing && existingPrompts.length >= MAX_TEMPLATES) {
    throw new AgencyBridgeError('agency_asset_invalid', 'The task template limit has been reached.');
  }
  let record: PromptRecord;
  if (existing) {
    record = existing.record;
    appendVersion(record, { content, note, created: now, source: 'manual' });
  } else {
    record = {
      kind: 'prompt',
      name,
      mode: 'user',
      versions: [{ content, note, created: now, source: 'manual' }],
      created: now,
    };
  }
  const file = savePrompt(record, directories.prompts);
  return { template: publicPrompt(file, record) };
}

export function handleAssetRequest(params: Record<string, unknown>): Record<string, unknown> {
  const action = requiredString(params, 'action', 64);
  if (action === 'list') return listAssets();
  if (action === 'save_team') return saveTeamAsset(params);
  if (action === 'save_template') return saveTemplateAsset(params);
  throw new AgencyBridgeError('agency_asset_action_invalid', 'Asset action is not supported.');
}
