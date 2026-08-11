export interface ScenePackTransform { positionMm: readonly [number, number, number]; rotationMilliDegrees: readonly [number, number, number]; scalePermille: readonly [number, number, number]; }
export interface ScenePackAnchor { positionMm: readonly [number, number, number]; yawMilliDegrees: number; }
export interface ScenePackAsset { id: string; roles: readonly ("visual" | "collider")[]; path: string; format: "glb"; byteLength: number; sha256: string; }
export interface ScenePackPlacement { id: string; visualAssetId: string; colliderAssetId: string | null; entityId: string | null; transform: ScenePackTransform; }
export interface ScenePackNodeBinding { nodeId: string; playerSpawn: ScenePackAnchor; actionAnchor: ScenePackAnchor; visiblePlacementIds: readonly string[]; }
export interface ScenePack { format: "matrix-oasis.scene-pack"; formatVersion: "0.1.0"; canonicalization: "matrix-oasis.canonical-json/1"; scene: {id: string; contentVersion: string; title: string}; runtimeIdentity: {runtimeFormat: "matrix-oasis.runtime-game-pack"; runtimeFormatVersion: "0.1.0"; packId: string; packContentVersion: string; sourceCanonicalSha256: string; artifactSha256: string}; assets: readonly ScenePackAsset[]; placements: readonly ScenePackPlacement[]; nodeBindings: readonly ScenePackNodeBinding[]; }
export const SCENE_PACK_SCHEMA: Readonly<Record<string, unknown>>;
export const SCENE_PACK_SCHEMA_ID: string;
export const SCENE_PACK_FORMAT: "matrix-oasis.scene-pack";
export const SCENE_PACK_FORMAT_VERSION: "0.1.0";
export const SCENE_PACK_CANONICALIZATION: "matrix-oasis.canonical-json/1";
export const SCENE_PACK_LIMITS: Readonly<{manifestBytes: number; assetBytes: number; totalAssetBytes: number; assets: number; placements: number; nodeBindings: number}>;
export { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
