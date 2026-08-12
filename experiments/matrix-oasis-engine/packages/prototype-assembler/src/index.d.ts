export interface PrototypeAssemblyDiagnostic {
  readonly phase: "assembly";
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeAssemblyFailure {
  readonly ok: false;
  readonly diagnostics: readonly PrototypeAssemblyDiagnostic[];
}

export interface PrototypeAssemblyReferencedFile {
  readonly source: "prototype-assets" | "prototype-environment";
  readonly path: string;
}

export interface PrototypeAssemblySuccess {
  readonly ok: true;
  readonly canonicalScenePackJson: string;
  readonly canonicalAssemblyReportJson: string;
  readonly referencedFiles: readonly PrototypeAssemblyReferencedFile[];
}

export interface PrototypeAssemblyRequest {
  readonly authoringGamePackJson: string;
  readonly sceneBlueprintJson: string;
  readonly runtimeGamePackJson: string;
  readonly runtimeReceiptJson: string;
  readonly assetBundleJson: string;
  readonly assetFiles: ReadonlyMap<string, Uint8Array>;
  readonly environmentBundleJson: string;
  readonly environmentFiles: ReadonlyMap<string, Uint8Array>;
}

export declare const PROTOTYPE_ASSEMBLY_PROFILE: Readonly<{
  id: "matrix-oasis.prototype-assembly/1";
  maxZones: 4;
  maxNonEnvironmentBriefs: 2;
  maxPlacements: 32;
  maxPlacementsPerZone: 8;
}>;

export declare class PrototypeAssemblerOperationalError extends Error {
  readonly code: "PROTOTYPE_ASSEMBLER_INTERNAL_ERROR";
}

export declare function assemblePrototypeScene(
  request: PrototypeAssemblyRequest,
): Promise<PrototypeAssemblySuccess | PrototypeAssemblyFailure>;
