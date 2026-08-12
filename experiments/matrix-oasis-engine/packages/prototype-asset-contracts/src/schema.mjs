export const PROTOTYPE_ASSET_BUNDLE_FORMAT =
  "matrix-oasis.prototype-asset-bundle";
export const PROTOTYPE_ASSET_BUNDLE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_ASSET_CANONICALIZATION =
  "matrix-oasis.canonical-json/1";
export const PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE =
  "kenney-prototype-room-v1";
export const PROTOTYPE_ASSET_NORMALIZATION_PROFILE =
  "matrix-oasis.glb-normalization/1";

export const PROTOTYPE_ASSET_LIMITS = Object.freeze({
  documentDepth: 256,
  manifestBytes: 256 * 1024,
  assetBriefs: 16,
  materializations: 16,
  files: 16,
  assetBytes: 32 * 1024 * 1024,
  totalAssetBytes: 128 * 1024 * 1024,
  visualTriangles: 100_000,
  colliderTriangles: 10_000,
  textureDimension: 2048,
  boundsMillimeters: 1_000_000,
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

const id = {
  type: "string",
  minLength: 1,
  maxLength: 96,
  pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
};
const hash = {
  type: "string",
  pattern: "^sha256:[0-9a-f]{64}$",
};
const roles = {
  type: "array",
  minItems: 1,
  maxItems: 2,
  uniqueItems: true,
  items: { type: "string", enum: ["visual", "collider"] },
};
const axis = {
  type: "integer",
  minimum: -PROTOTYPE_ASSET_LIMITS.boundsMillimeters,
  maximum: PROTOTYPE_ASSET_LIMITS.boundsMillimeters,
};

export const PROTOTYPE_ASSET_BUNDLE_SCHEMA = deepFreeze({
  $id: "urn:matrix-oasis:prototype-asset-bundle:0.1.0",
  title: "Matrix Oasis Prototype Asset Bundle 0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format",
    "formatVersion",
    "canonicalization",
    "scene",
    "blueprint",
    "runtimeIdentity",
    "environmentTemplate",
    "materializations",
  ],
  properties: {
    format: { type: "string", const: PROTOTYPE_ASSET_BUNDLE_FORMAT },
    formatVersion: {
      type: "string",
      const: PROTOTYPE_ASSET_BUNDLE_FORMAT_VERSION,
    },
    canonicalization: {
      type: "string",
      const: PROTOTYPE_ASSET_CANONICALIZATION,
    },
    scene: { $ref: "#/$defs/scene" },
    blueprint: { $ref: "#/$defs/blueprint" },
    runtimeIdentity: { $ref: "#/$defs/runtimeIdentity" },
    environmentTemplate: {
      type: "string",
      const: PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
    },
    materializations: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_ASSET_LIMITS.materializations,
      items: { $ref: "#/$defs/materialization" },
    },
  },
  $defs: {
    id,
    hash,
    contentVersion: {
      type: "string",
      minLength: 1,
      maxLength: 64,
      pattern: "\\S",
    },
    prose: {
      type: "string",
      minLength: 1,
      maxLength: 4096,
      pattern: "\\S",
    },
    roles,
    nullableId: {
      anyOf: [{ $ref: "#/$defs/id" }, { type: "null" }],
    },
    scene: {
      type: "object",
      additionalProperties: false,
      required: ["id", "contentVersion", "title"],
      properties: {
        id: { $ref: "#/$defs/id" },
        contentVersion: { $ref: "#/$defs/contentVersion" },
        title: { $ref: "#/$defs/prose" },
      },
    },
    blueprint: {
      type: "object",
      additionalProperties: false,
      required: [
        "format",
        "formatVersion",
        "canonicalSha256",
        "assetBriefs",
      ],
      properties: {
        format: { type: "string", const: "matrix-oasis.scene-blueprint" },
        formatVersion: { type: "string", const: "0.1.0" },
        canonicalSha256: { $ref: "#/$defs/hash" },
        assetBriefs: {
          type: "array",
          minItems: 1,
          maxItems: PROTOTYPE_ASSET_LIMITS.assetBriefs,
          items: { $ref: "#/$defs/assetBrief" },
        },
      },
    },
    assetBrief: {
      type: "object",
      additionalProperties: false,
      required: ["id", "kind", "entityId", "roles"],
      properties: {
        id: { $ref: "#/$defs/id" },
        kind: {
          type: "string",
          enum: ["environment", "prop", "character-placeholder"],
        },
        entityId: { $ref: "#/$defs/nullableId" },
        roles: { $ref: "#/$defs/roles" },
      },
    },
    runtimeIdentity: {
      type: "object",
      additionalProperties: false,
      required: [
        "format",
        "formatVersion",
        "id",
        "contentVersion",
        "authoringCanonicalSha256",
        "artifactSha256",
      ],
      properties: {
        format: { type: "string", const: "matrix-oasis.runtime-game-pack" },
        formatVersion: { type: "string", const: "0.1.0" },
        id: { $ref: "#/$defs/id" },
        contentVersion: { $ref: "#/$defs/contentVersion" },
        authoringCanonicalSha256: { $ref: "#/$defs/hash" },
        artifactSha256: { $ref: "#/$defs/hash" },
      },
    },
    builtinSource: {
      type: "object",
      additionalProperties: false,
      required: ["type", "template"],
      properties: {
        type: { type: "string", const: "builtin-template" },
        template: {
          type: "string",
          const: PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
        },
      },
    },
    meshySource: {
      type: "object",
      additionalProperties: false,
      required: ["type", "provider", "model"],
      properties: {
        type: { type: "string", const: "meshy-text-to-3d" },
        provider: { type: "string", const: "meshy" },
        model: { type: "string", const: "meshy-6" },
      },
    },
    source: {
      oneOf: [
        { $ref: "#/$defs/builtinSource" },
        { $ref: "#/$defs/meshySource" },
      ],
    },
    materialization: {
      type: "object",
      additionalProperties: false,
      required: ["assetBriefId", "source", "assets"],
      properties: {
        assetBriefId: { $ref: "#/$defs/id" },
        source: { $ref: "#/$defs/source" },
        assets: {
          type: "array",
          minItems: 1,
          maxItems: PROTOTYPE_ASSET_LIMITS.files,
          items: { $ref: "#/$defs/asset" },
        },
      },
    },
    asset: {
      type: "object",
      additionalProperties: false,
      required: [
        "id",
        "path",
        "format",
        "roles",
        "normalizationProfile",
        "byteLength",
        "sha256",
        "metrics",
      ],
      properties: {
        id: { $ref: "#/$defs/id" },
        path: {
          type: "string",
          minLength: 12,
          maxLength: 180,
          pattern: "^assets/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\\.glb$",
        },
        format: { type: "string", const: "glb" },
        roles: { $ref: "#/$defs/roles" },
        normalizationProfile: {
          type: "string",
          enum: [
            PROTOTYPE_ASSET_NORMALIZATION_PROFILE,
            PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
          ],
        },
        byteLength: {
          type: "integer",
          minimum: 1,
          maximum: PROTOTYPE_ASSET_LIMITS.assetBytes,
        },
        sha256: { $ref: "#/$defs/hash" },
        metrics: { $ref: "#/$defs/metrics" },
      },
    },
    metrics: {
      type: "object",
      additionalProperties: false,
      required: [
        "nodeCount",
        "meshCount",
        "surfaceCount",
        "triangleCount",
        "maxTextureWidth",
        "maxTextureHeight",
        "boundsMm",
      ],
      properties: {
        nodeCount: { type: "integer", minimum: 1, maximum: 256 },
        meshCount: { type: "integer", minimum: 1, maximum: 64 },
        surfaceCount: { type: "integer", minimum: 1, maximum: 128 },
        triangleCount: {
          type: "integer",
          minimum: 1,
          maximum: PROTOTYPE_ASSET_LIMITS.visualTriangles,
        },
        maxTextureWidth: {
          type: "integer",
          minimum: 0,
          maximum: PROTOTYPE_ASSET_LIMITS.textureDimension,
        },
        maxTextureHeight: {
          type: "integer",
          minimum: 0,
          maximum: PROTOTYPE_ASSET_LIMITS.textureDimension,
        },
        boundsMm: { $ref: "#/$defs/bounds" },
      },
    },
    vector3: {
      type: "array",
      minItems: 3,
      maxItems: 3,
      items: axis,
    },
    bounds: {
      type: "object",
      additionalProperties: false,
      required: ["min", "max"],
      properties: {
        min: { $ref: "#/$defs/vector3" },
        max: { $ref: "#/$defs/vector3" },
      },
    },
  },
});
