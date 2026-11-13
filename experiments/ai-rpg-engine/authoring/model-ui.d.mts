// Typed projection consumed by the React chat; validation remains in model-ui.mjs.
export type ParameterPurpose = 'story' | 'summary' | 'memory';
export type ParameterField = {type: string; minimum?: number; maximum?: number; options?: string[]; maxItems?: number; maxItemLength?: number};
export type ParameterEntry = {selectionId: string; selectionRevision: string; capabilityRevision: string; modelId: string; available: boolean; parameterPolicy: {fields: Record<string, ParameterField>}};
export type ParameterDraft = Record<string, {mode: string; value: unknown}>;
export type ParameterConfig = {format?: string; values: Record<string, unknown>};
export function typedParameterDraft(entry: ParameterEntry, configuration?: ParameterConfig | null): ParameterDraft;
export function switchParameterDraft(draft: ParameterDraft, entry: ParameterEntry): ParameterDraft;
export function typedDraftFingerprint(entry: ParameterEntry, draft: ParameterDraft): string;
export function typedDraftIssues(entry: ParameterEntry, draft: ParameterDraft, purpose: ParameterPurpose): {code: string; field: string}[];
export function typedSelection(catalog: {revision: string; models: ParameterEntry[]}, entry: ParameterEntry, draft: ParameterDraft, purpose: ParameterPurpose): {
  format: 'modelmirror.rpg.parameter-selection/2'; catalogRevision: string; selectionId: string; selectionRevision: string; capabilityRevision: string;
  profile: {format: 'modelmirror.rpg.parameter-profile/1'; purpose: ParameterPurpose; modelId: string; parameters: {format: 'modelmirror.rpg.parameter-config/2'; values: Record<string, {mode: 'default'} | {mode: 'explicit'; value: unknown}>}};
};
export function parameterStep(key: string, field: ParameterField, value: unknown): string;
export function modelError(error: {code?: string; path?: string} | null | undefined): string;
export function presetReceiptMatches(body: unknown, operation: unknown): Promise<boolean>;
