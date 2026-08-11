export type PrototypeGenerationDiagnosticPhase = "parse" | "schema" | "semantic";

export interface PrototypeGenerationDiagnostic {
  readonly phase: PrototypeGenerationDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly relatedPath?: string;
  readonly location?: Readonly<{ line: number; column: number }>;
}

export interface PrototypeGenerationValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly PrototypeGenerationDiagnostic[];
}

export interface SceneBlueprintScene {
  readonly id: string;
  readonly contentVersion: string;
  readonly title: string;
  readonly environmentPrompt: string;
  readonly visualStylePrompt: string;
}

export interface SceneBlueprintZone {
  readonly id: string;
  readonly label: string;
  readonly description: string;
}

export type SceneBlueprintAssetKind =
  | "environment"
  | "prop"
  | "character-placeholder";
export type SceneBlueprintAssetRole = "visual" | "collider";

export interface SceneBlueprintAssetBrief {
  readonly id: string;
  readonly kind: SceneBlueprintAssetKind;
  readonly prompt: string;
  readonly entityId: string | null;
  readonly roles: readonly SceneBlueprintAssetRole[];
}

export interface SceneBlueprintPlacement {
  readonly id: string;
  readonly assetBriefId: string;
  readonly zoneId: string;
  readonly entityId: string | null;
}

export interface SceneBlueprintNodeBinding {
  readonly nodeId: string;
  readonly zoneId: string;
  readonly visiblePlacementIds: readonly string[];
}

export interface SceneBlueprint {
  readonly format: "matrix-oasis.scene-blueprint";
  readonly formatVersion: "0.1.0";
  readonly scene: SceneBlueprintScene;
  readonly zones: readonly SceneBlueprintZone[];
  readonly assetBriefs: readonly SceneBlueprintAssetBrief[];
  readonly placements: readonly SceneBlueprintPlacement[];
  readonly nodeBindings: readonly SceneBlueprintNodeBinding[];
}

export interface GenerationProposal {
  readonly format: "matrix-oasis.prototype-generation-proposal";
  readonly formatVersion: "0.1.0";
  readonly authoringGamePack: Readonly<Record<string, unknown>>;
  readonly sceneBlueprint: SceneBlueprint;
}

export interface PreparedGenerationProposal {
  readonly ok: true;
  readonly value: GenerationProposal;
  readonly canonicalProposalJson: string;
  readonly canonicalAuthoringJson: string;
  readonly canonicalSceneBlueprintJson: string;
  readonly validationReport: PrototypeGenerationValidationReport & { readonly valid: true };
}

export interface RejectedGenerationProposal {
  readonly ok: false;
  readonly validationReport: PrototypeGenerationValidationReport & { readonly valid: false };
}

export declare const GENERATION_PROPOSAL_FORMAT: "matrix-oasis.prototype-generation-proposal";
export declare const GENERATION_PROPOSAL_FORMAT_VERSION: "0.1.0";
export declare const SCENE_BLUEPRINT_FORMAT: "matrix-oasis.scene-blueprint";
export declare const SCENE_BLUEPRINT_FORMAT_VERSION: "0.1.0";
export declare const GENERATION_PROPOSAL_SCHEMA: Readonly<Record<string, unknown>>;
export declare const SCENE_BLUEPRINT_SCHEMA: Readonly<Record<string, unknown>>;
export declare const PROTOTYPE_GENERATION_LIMITS: Readonly<{
  documentDepth: 256;
  zones: 16;
  assetBriefs: 16;
  placements: 128;
  nodeBindings: 4096;
  environmentPromptCharacters: 4096;
  visualStylePromptCharacters: 2048;
  briefPromptCharacters: 2048;
}>;

export declare class PrototypeGenerationContractOperationalError extends Error {
  readonly code: "PROTOTYPE_GENERATION_CONTRACT_INTERNAL_ERROR";
}

export declare function validateGenerationProposalJson(
  text: string,
): PrototypeGenerationValidationReport;
export declare function prepareGenerationProposalJson(
  text: string,
): PreparedGenerationProposal | RejectedGenerationProposal;
