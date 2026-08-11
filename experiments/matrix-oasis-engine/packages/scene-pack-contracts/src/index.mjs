import schema from "../schemas/0.1.0/scene-pack.schema.json" with { type: "json" };
import { CANONICAL_JSON_PROFILE, canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

function freezeTree(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freezeTree(child);
  return Object.freeze(value);
}

export const SCENE_PACK_SCHEMA = freezeTree(schema);
export const SCENE_PACK_SCHEMA_ID = SCENE_PACK_SCHEMA.$id;
export const SCENE_PACK_FORMAT = SCENE_PACK_SCHEMA.properties.format.const;
export const SCENE_PACK_FORMAT_VERSION = SCENE_PACK_SCHEMA.properties.formatVersion.const;
export const SCENE_PACK_CANONICALIZATION = CANONICAL_JSON_PROFILE;
export const SCENE_PACK_LIMITS = Object.freeze({manifestBytes: 262144, assetBytes: 33554432, totalAssetBytes: 134217728, assets: 16, placements: 128, nodeBindings: 4096});
export { canonicalizeJsonValue };
