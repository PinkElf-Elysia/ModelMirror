export type ResourceKind = 'world' | 'identity' | 'talent' | 'item' | 'background';
export type Resource = { id: string; kind?: ResourceKind; displayName: string; description: string; worldRefs?: string[]; rankLabel?: string; tierLabel?: string; tags?: string[] };
export type Choice = { source: 'package'; resourceRef: string } | { source: 'custom'; resource: Resource & { kind: ResourceKind } };
export type Player = {
  format: 'modelmirror.ai-rpg.player-setup'; formatVersion: '0.1.0'; setupId: string;
  cardPackageRef: { id: string; version: string };
  character: { name: string; gender?: string; age?: number; appearance: string; personality: string; preferences: string[]; notes?: string };
  opening: { mode: string; openingRef: string }; world: Choice; currentIdentity: Choice;
  inherentBackgrounds: Choice[]; possessions: { resource: Choice; quantity: number }[];
  talents: { resource: Choice; owned: boolean; active: boolean }[];
  characterPower: { status: 'unspecified' } | { status: 'declared'; rankLabel: string; description?: string };
  runtimePermissions: never[];
};
export type Diagnostic = { code: string; path: string };
export type Check = { valid: boolean; diagnostics: Diagnostic[] };
export type Scene = { id: string; worldRef: string; openingRef: string; displayName: string; identityRefs: string[]; talentRefs: string[]; itemRefs: string[]; backgroundRefs: string[] };
export type Catalog = {
  card: { id: string; version: string; displayName: string };
  resources: Record<'worlds' | 'identities' | 'talents' | 'items' | 'backgrounds', Resource[]>;
  scenes: Scene[]; modes: { id: string; available: boolean; reason?: string }[];
};
export type WorldDraft = { resourceId: string; displayName: string; introduction: string; reference: string };
export type Draft = { worldDraft?: WorldDraft; id: string; revision: number; playerSetup: Player; sceneRef: string; bundleId: string; catalog: Catalog; ready: Check };
export type Summary = { id: string; revision: number; title: string; characterName: string; updatedAt: string; turnCount: number; status: string };
export type Bootstrap = { journeys: Summary[]; drafts: Summary[]; capabilities: string[]; catalog: Catalog; blankPlayer: Player };
export type Operation = { id: string; status: string; kind: string; text: string; updatedAt: string; sequence: number; draft: string; error: string | null; evidenceKind: 'mock' | 'real'; receipt: { preparedSha256: string; rawTurnExchangeSha256: string | null } | null };
export type Turn = { id: string; input: { kind: 'action' | 'speech' | 'query' | 'command'; text: string; commandRef?: string }; narrative: string; suggestions: { id: string; label: string; text: string }[]; information: { id: string; title: string; presentation: string; values: { id: string; label: string; value: string | number | boolean | string[] }[] }[]; uncertainties: { code: string; description: string }[] };
export type Journey = Summary & { playerSetup: Player; sceneRef: string; turns: Turn[]; bindings: Record<string, string>; operation?: Operation | null; execution?: 'mock' | 'real' | 'disabled' };
