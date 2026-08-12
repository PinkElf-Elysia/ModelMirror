export type PrototypeAssetRole = "visual" | "collider";
export type PrototypeAssetBriefKind =
  | "environment"
  | "prop"
  | "character-placeholder";

export interface PrototypeAssetBundleScene {
  readonly id: string;
  readonly contentVersion: string;
  readonly title: string;
}

export interface PrototypeAssetBundleBrief {
  readonly id: string;
  readonly kind: PrototypeAssetBriefKind;
  readonly entityId: string | null;
  readonly roles: readonly PrototypeAssetRole[];
}

export interface PrototypeAssetBundleBlueprint {
  readonly format: "matrix-oasis.scene-blueprint";
  readonly formatVersion: "0.1.0";
  readonly canonicalSha256: string;
  readonly assetBriefs: readonly PrototypeAssetBundleBrief[];
}

export interface PrototypeAssetRuntimeIdentity {
  readonly format: "matrix-oasis.runtime-game-pack";
  readonly formatVersion: "0.1.0";
  readonly id: string;
  readonly contentVersion: string;
  readonly authoringCanonicalSha256: string;
  readonly artifactSha256: string;
}

export interface PrototypeAssetBounds {
  readonly min: readonly [number, number, number];
  readonly max: readonly [number, number, number];
}

export interface PrototypeAssetMetrics {
  readonly nodeCount: number;
  readonly meshCount: number;
  readonly surfaceCount: number;
  readonly triangleCount: number;
  readonly maxTextureWidth: number;
  readonly maxTextureHeight: number;
  readonly boundsMm: PrototypeAssetBounds;
}

export interface PrototypeAssetFile {
  readonly id: string;
  readonly path: string;
  readonly format: "glb";
  readonly roles: readonly PrototypeAssetRole[];
  readonly normalizationProfile:
    | "matrix-oasis.glb-normalization/1"
    | "kenney-prototype-room-v1";
  readonly byteLength: number;
  readonly sha256: string;
  readonly metrics: PrototypeAssetMetrics;
}

export type PrototypeAssetSource =
  | Readonly<{
      type: "builtin-template";
      template: "kenney-prototype-room-v1";
    }>
  | Readonly<{
      type: "meshy-text-to-3d";
      provider: "meshy";
      model: "meshy-6";
    }>;

export interface PrototypeAssetMaterialization {
  readonly assetBriefId: string;
  readonly source: PrototypeAssetSource;
  readonly assets: readonly PrototypeAssetFile[];
}

export interface PrototypeAssetBundle {
  readonly format: "matrix-oasis.prototype-asset-bundle";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly scene: PrototypeAssetBundleScene;
  readonly blueprint: PrototypeAssetBundleBlueprint;
  readonly runtimeIdentity: PrototypeAssetRuntimeIdentity;
  readonly environmentTemplate: "kenney-prototype-room-v1";
  readonly materializations: readonly PrototypeAssetMaterialization[];
}

export type PrototypeAssetDiagnosticPhase =
  | "parse"
  | "schema"
  | "semantic"
  | "integrity";

export interface PrototypeAssetDiagnostic {
  readonly phase: PrototypeAssetDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeAssetValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly PrototypeAssetDiagnostic[];
}

export declare const PROTOTYPE_ASSET_BUNDLE_FORMAT:
  "matrix-oasis.prototype-asset-bundle";
export declare const PROTOTYPE_ASSET_BUNDLE_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_ASSET_CANONICALIZATION:
  "matrix-oasis.canonical-json/1";
export declare const PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE:
  "kenney-prototype-room-v1";
export declare const PROTOTYPE_ASSET_NORMALIZATION_PROFILE:
  "matrix-oasis.glb-normalization/1";
export declare const PROTOTYPE_ASSET_LIMITS: Readonly<{
  documentDepth: 256;
  manifestBytes: 262144;
  assetBriefs: 16;
  materializations: 16;
  files: 16;
  assetBytes: 33554432;
  totalAssetBytes: 134217728;
  visualTriangles: 100000;
  colliderTriangles: 10000;
  textureDimension: 2048;
  boundsMillimeters: 1000000;
}>;
export declare const PROTOTYPE_ASSET_BUNDLE_SCHEMA: Readonly<
  Record<string, unknown>
>;

export declare class PrototypeAssetContractOperationalError extends Error {
  readonly code: "PROTOTYPE_ASSET_CONTRACT_INTERNAL_ERROR";
}

export declare function validatePrototypeAssetBundleJson(
  text: string,
): PrototypeAssetValidationReport;
