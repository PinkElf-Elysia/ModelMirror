export declare const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT: "matrix-oasis.prototype-spatial-assembly";
export declare const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE: Readonly<{
  id: "matrix-oasis.prototype-spatial-assembly/1";
  panoramaVisible: false;
}>;

export interface PrototypeSpatialAssemblyDiagnostic {
  readonly phase: "assembly" | "integrity" | "schema" | "semantic";
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeSpatialAssembly {
  readonly format: typeof PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT;
  readonly formatVersion: typeof PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION;
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly scene: Readonly<{ id: string; contentVersion: string; title: string }>;
  readonly runtimeIdentity: Readonly<{
    runtimeFormat: string;
    runtimeFormatVersion: string;
    packId: string;
    packContentVersion: string;
    sourceCanonicalSha256: string;
    artifactSha256: string;
  }>;
  readonly sources: Readonly<{
    scenePackSha256: string;
    prototypeAssemblyReportSha256: string;
    spatialEnvironmentBundleSha256: string;
    sceneBlueprintSha256: string;
  }>;
  readonly environment: Readonly<{
    panoramaVisible: false;
    splat: Readonly<{ path: string; sha256: string; numGaussians: number }>;
    collider: Readonly<{ assetId: string; placementId: string; path: string; sha256: string }>;
  }>;
  readonly transforms: Readonly<{
    coordinateTransform: "spz-raw-ply-to-godot-v1";
    root: Readonly<{
      translationMm: readonly [number, number, number];
      rotationMilliDegrees: readonly [number, number, number];
    }>;
    splat: Readonly<{
      localTranslationMm: readonly [number, number, number];
      scaleMicros: number;
    }>;
    collider: Readonly<{
      localTranslationMm: readonly [0, 0, 0];
      scaleMicros: number;
    }>;
  }>;
}

export type PrototypeSpatialAssemblyResult =
  | Readonly<{
      ok: true;
      assembly: PrototypeSpatialAssembly;
      canonicalSpatialAssemblyJson: string;
      canonicalSpatialAssemblyReportJson: string;
      referencedFiles: readonly Readonly<{ source: "spatial-environment"; path: string }>[];
    }>
  | Readonly<{ ok: false; diagnostics: readonly PrototypeSpatialAssemblyDiagnostic[] }>;

export interface PrototypeSpatialAssemblyRequest {
  readonly assemblyReportJson: string;
  readonly scenePackJson: string;
  readonly runtimeGamePackJson: string;
  readonly runtimeReceiptJson: string;
  readonly spatialEnvironmentBundleJson: string;
  readonly spatialEnvironmentFiles: ReadonlyMap<string, Uint8Array>;
}

export declare class PrototypeSpatialAssemblerOperationalError extends Error {
  readonly code: "PROTOTYPE_SPATIAL_ASSEMBLER_INTERNAL_ERROR";
}

export declare function validatePrototypeSpatialAssemblyJson(text: string): Readonly<{
  reportVersion: 1;
  valid: boolean;
  diagnostics: readonly PrototypeSpatialAssemblyDiagnostic[];
}>;

export declare function assemblePrototypeSpatialScene(
  request: PrototypeSpatialAssemblyRequest,
): Promise<PrototypeSpatialAssemblyResult>;
