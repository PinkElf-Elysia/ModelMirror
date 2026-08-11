export interface ScenePackDiagnostic { readonly phase: "parse" | "schema" | "semantic" | "integrity"; readonly severity: "error"; readonly code: string; readonly path: string; readonly message: string; readonly relatedPath?: string; }
export interface ScenePackValidationReport { readonly reportVersion: 1; readonly valid: boolean; readonly diagnostics: readonly ScenePackDiagnostic[]; }
export declare class ScenePackValidatorOperationalError extends Error { readonly code: "SCENE_PACK_VALIDATOR_INTERNAL_ERROR"; }
export declare function validateScenePackJson(sceneText: string, runtimeText: string, receiptText: string): Promise<ScenePackValidationReport>;
