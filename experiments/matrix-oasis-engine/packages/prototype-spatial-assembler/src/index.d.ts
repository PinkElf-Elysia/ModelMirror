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
    renderer: Readonly<{
      profile: "opaque-depth-compose-v1";
      depthBiasMicros: 0;
      depthTestMinAlphaPermille: 50;
      depthCaptureAlphaPermille: 500;
    }>;
    splat: Readonly<{
      path: string;
      sha256: string;
      numGaussians: number;
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
    collider: Readonly<{ assetId: string; placementId: string; path: string; sha256: string }>;
  }>;
  readonly transforms: Readonly<{
    coordinateTransform: "spz-raw-ply-to-godot-v1";
    eulerOrder: "YXZ";
    alignment: Readonly<{
      profile: "collider-fit-30m-v1";
      targetFloorSpanMm: 30000;
      maximumHorizontalSpanMm: 90000;
      colliderBoundsMm: Readonly<{
        minimumMm: readonly [number, number, number];
        maximumMm: readonly [number, number, number];
      }>;
      centerFloorSampleSourceMm: readonly [number, number, number];
      splatProfile: "splat-robust-fit-30m-v1";
      splatBoundsProfile: "source-position-percentile-1-99-v1";
      splatBoundsMm: Readonly<{
        minimumMm: readonly [number, number, number];
        maximumMm: readonly [number, number, number];
      }>;
    }>;
    root: Readonly<{
      translationMm: readonly [number, number, number];
      rotationMilliDegrees: readonly [number, number, number];
    }>;
    splat: Readonly<{
      localTranslationMm: readonly [number, number, number];
      localRotationMilliDegrees: readonly [0, 0, 0];
      scaleMicros: number;
    }>;
    collider: Readonly<{
      localTranslationMm: readonly [number, number, number];
      scaleMicros: number;
    }>;
    walkableEnvelope: Readonly<{
      profile: "source-density-first-surface-v1";
      minimumMm: readonly [number, 0, number];
      maximumMm: readonly [number, number, number];
      wallThicknessMm: 700;
      floorThicknessMm: 200;
      verticalBandMm: readonly [350, 3000];
      lateralBandMm: 4000;
      binSizeMm: 250;
      minimumBinCount: 64;
      peakThresholdPermille: 5;
      adjacentBins: 2;
    }>;
    placementGroundTargetMm: 150;
    placementLayout?: readonly Readonly<{
      placementId: string;
      positionMm: readonly [number, 0, number];
    }>[];
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
  options?: Readonly<{ profile: "matrix-oasis.prototype-spatial-assembly/2" }>,
): Promise<PrototypeSpatialAssemblyResult>;
