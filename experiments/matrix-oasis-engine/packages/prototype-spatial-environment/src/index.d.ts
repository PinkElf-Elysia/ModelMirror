export type PrototypeSpatialEnvironmentDiagnosticPhase =
  | "input"
  | "schema"
  | "semantic"
  | "integrity"
  | "conversion";

export interface PrototypeSpatialEnvironmentDiagnostic {
  readonly phase: PrototypeSpatialEnvironmentDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeSpatialEnvironmentFile {
  readonly path:
    | "assets/environment.compressed.ply"
    | "assets/environment-collider.glb";
  readonly bytes: Uint8Array;
}

export interface PrototypeSpatialEnvironmentBundle {
  readonly format: "matrix-oasis.prototype-spatial-environment-bundle";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly scene: Readonly<{ id: string; contentVersion: string; title: string }>;
  readonly blueprint: Readonly<{
    format: "matrix-oasis.scene-blueprint";
    formatVersion: "0.1.0";
    canonicalSha256: string;
  }>;
  readonly source: Readonly<{
    environmentBundleSha256: string;
    format: "spz";
    byteLength: number;
    sha256: string;
  }>;
  readonly assets: Readonly<{
    splat: Readonly<{
      path: "assets/environment.compressed.ply";
      format: "compressed-ply";
      byteLength: number;
      sha256: string;
      numGaussians: number;
      numLods: 1;
      shBands: number;
      derivation: Readonly<{
        profile: "identity-v1" | "mpmm-uniform-v1";
        targetNumGaussians: 640000;
        sourceNumGaussians: number;
        fullResolutionCompressedPly: Readonly<{
          byteLength: number;
          sha256: string;
          numGaussians: number;
        }>;
      }>;
    }>;
    collider: Readonly<{
      path: "assets/environment-collider.glb";
      format: "glb";
      byteLength: number;
      sha256: string;
      metrics: Readonly<{
        nodeCount: number;
        meshCount: number;
        surfaceCount: number;
        triangleCount: number;
      }>;
    }>;
  }>;
  readonly calibration: Readonly<{
    coordinateTransform: "spz-raw-ply-to-godot-v1";
    metricScaleMicros: number;
    groundPlaneOffsetMm: number;
    godotTranslationMm: readonly [number, number, number];
    godotRotationMilliDegrees: readonly [number, number, number];
  }>;
  readonly statistics: Readonly<{
    sourceBounds: Readonly<{
      minimumMm: readonly [number, number, number];
      maximumMm: readonly [number, number, number];
    }>;
    runtimeRobustBounds: Readonly<{
      profile: "source-position-percentile-1-99-v1";
      minimumMm: readonly [number, number, number];
      maximumMm: readonly [number, number, number];
    }>;
    sourceMeanMm: readonly [number, number, number];
    rendererCenterCompensationMm: readonly [number, number, number];
    sourceInteriorEnvelope: Readonly<{
      profile: "source-density-first-surface-v1";
      coordinateSpace: "splat-robust-fit-30m-v1";
      minimumMm: readonly [number, 0, number];
      maximumMm: readonly [number, number, number];
      verticalBandMm: readonly [350, 3000];
      lateralBandMm: 4000;
      binSizeMm: 250;
      minimumBinCount: 64;
      peakThresholdPermille: 5;
      adjacentBins: 2;
    }> | null;
  }>;
  readonly toolchain: Readonly<{
    converter: Readonly<{ id: "@playcanvas/splat-transform"; version: "3.3.0" }>;
    decoder: Readonly<{ id: "@adobe/spz"; version: "0.2.2" }>;
  }>;
}

export type PrototypeSpatialEnvironmentFailure = Readonly<{
  ok: false;
  diagnostics: readonly PrototypeSpatialEnvironmentDiagnostic[];
}>;

export declare const PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT:
  "matrix-oasis.prototype-spatial-environment-bundle";
export declare const PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS: Readonly<{
  manifestBytes: number;
  spzBytes: number;
  compressedPlyBytes: number;
  colliderBytes: number;
  totalBundleBytes: number;
  maxSplats: number;
  runtimeSplatTarget: 640000;
  decimationMemoryBudgetBytes: number;
}>;

export declare class PrototypeSpatialEnvironmentOperationalError extends Error {
  readonly code: "PROTOTYPE_SPATIAL_ENVIRONMENT_INTERNAL_ERROR";
}

export declare function materializePrototypeSpatialEnvironment(
  request: Readonly<{
    environmentBundleJson: string;
    environmentFiles: ReadonlyMap<string, Uint8Array>;
    spzBytes: Uint8Array;
    calibration: Readonly<{
      coordinateTransform: "spz-raw-ply-to-godot-v1";
      metricScaleMicros: number;
      groundPlaneOffsetMm: number;
      godotTranslationMm: readonly [number, number, number];
      godotRotationMilliDegrees: readonly [number, number, number];
    }>;
  }>,
): Promise<
  | Readonly<{
      ok: true;
      bundle: PrototypeSpatialEnvironmentBundle;
      canonicalBundleJson: string;
      canonicalReportJson: string;
      files: readonly PrototypeSpatialEnvironmentFile[];
    }>
  | PrototypeSpatialEnvironmentFailure
>;

export declare function validatePrototypeSpatialEnvironmentBundleJson(
  text: string,
  files: ReadonlyMap<string, Uint8Array>,
): Promise<Readonly<{
  reportVersion: 1;
  valid: boolean;
  diagnostics: readonly PrototypeSpatialEnvironmentDiagnostic[];
}>>;
