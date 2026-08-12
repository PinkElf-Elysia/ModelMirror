export type PrototypeEnvironmentDiagnosticPhase =
  | "plan"
  | "provider"
  | "schema"
  | "semantic"
  | "integrity";

export interface PrototypeEnvironmentDiagnostic {
  readonly phase: PrototypeEnvironmentDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeEnvironmentFailure {
  readonly ok: false;
  readonly diagnostics: readonly PrototypeEnvironmentDiagnostic[];
}

export interface PrototypeEnvironmentFile {
  readonly path:
    | "assets/environment-panorama.png"
    | "assets/environment-collider.glb";
  readonly bytes: Uint8Array;
}

export interface PrototypeEnvironmentPlan {
  readonly ok: true;
  readonly plan: Readonly<{
    scene: Readonly<{ id: string; contentVersion: string; title: string }>;
    blueprint: Readonly<{
      format: "matrix-oasis.scene-blueprint";
      formatVersion: "0.1.0";
      canonicalSha256: string;
    }>;
    environmentPromptSha256: string;
  }>;
}

export interface PrototypeEnvironmentBundle {
  readonly format: "matrix-oasis.prototype-environment-bundle";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly scene: Readonly<{ id: string; contentVersion: string; title: string }>;
  readonly blueprint: Readonly<{
    format: "matrix-oasis.scene-blueprint";
    formatVersion: "0.1.0";
    canonicalSha256: string;
  }>;
  readonly provider: Readonly<{
    id: "world-labs-marble";
    model: "marble-1.1";
    environmentPromptSha256: string;
  }>;
  readonly assets: Readonly<{
    panorama: Readonly<{
      path: "assets/environment-panorama.png";
      format: "png";
      width: number;
      height: number;
      byteLength: number;
      sha256: string;
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
}

export interface MarbleWorldProviderConfig {
  readonly endpoint: string;
  readonly apiKey: string;
  readonly allowedAssetHosts: readonly string[];
  readonly timeoutMs?: number;
  readonly pollIntervalMs?: number;
}

export interface MarbleWorldProvider {
  readonly provider: "marble";
  readonly model: "marble-1.1";
}

export declare const MARBLE_PROVIDER_ENDPOINT: string;
export declare const MARBLE_PROVIDER_MODEL: "marble-1.1";
export declare const MARBLE_PROVIDER_LIMITS: Readonly<{
  timeoutMs: 120000;
  responseBytes: 1048576;
  panoramaBytes: 67108864;
  colliderBytes: 33554432;
  pollAttempts: 180;
  pollIntervalMs: 10000;
  promptCharacters: 4096;
}>;

export declare const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT:
  "matrix-oasis.prototype-environment-bundle";
export declare const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_ENVIRONMENT_LIMITS: Readonly<{
  manifestBytes: 262144;
  panoramaBytes: 67108864;
  panoramaWidth: 16384;
  panoramaHeight: 8192;
  colliderBytes: 33554432;
}>;

export declare class PrototypeEnvironmentPipelineOperationalError extends Error {
  readonly code: "PROTOTYPE_ENVIRONMENT_PIPELINE_INTERNAL_ERROR";
}

export declare function createMarbleWorldProvider(
  config: MarbleWorldProviderConfig,
): MarbleWorldProvider;
export declare function planPrototypeEnvironment(
  sceneBlueprintJson: string,
): PrototypeEnvironmentPlan | PrototypeEnvironmentFailure;
export declare function materializePrototypeEnvironment(
  request: Readonly<{
    plan: PrototypeEnvironmentPlan;
    approval: Readonly<{
      blueprintSha256: string;
      model: "marble-1.1";
      maxCreateRequests: 1;
      maxPollAttempts: 180;
      maxWorldGets: 1;
      maxDownloads: 2;
      creditLimit: 1600;
      usdLimitCents: 150;
    }>;
  }>,
  provider: MarbleWorldProvider,
): Promise<
  | Readonly<{
      ok: true;
      bundle: PrototypeEnvironmentBundle;
      canonicalBundleJson: string;
      canonicalReportJson: string;
      files: readonly PrototypeEnvironmentFile[];
    }>
  | PrototypeEnvironmentFailure
>;
export declare function validatePrototypeEnvironmentBundleJson(
  text: string,
  files: ReadonlyMap<string, Uint8Array>,
): Readonly<{
  reportVersion: 1;
  valid: boolean;
  diagnostics: readonly PrototypeEnvironmentDiagnostic[];
}>;
