import { AUTHORING_GAME_PACK_SCHEMA } from "@matrix-oasis/game-pack-contracts";

export const GENERATION_PROPOSAL_FORMAT =
  "matrix-oasis.prototype-generation-proposal";
export const GENERATION_PROPOSAL_FORMAT_VERSION = "0.1.0";
export const SCENE_BLUEPRINT_FORMAT = "matrix-oasis.scene-blueprint";
export const SCENE_BLUEPRINT_FORMAT_VERSION = "0.1.0";

export const PROTOTYPE_GENERATION_LIMITS = Object.freeze({
  documentDepth: 256,
  zones: 16,
  assetBriefs: 16,
  placements: 128,
  nodeBindings: 4096,
  environmentPromptCharacters: 4096,
  visualStylePromptCharacters: 2048,
  briefPromptCharacters: 2048,
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

function rewriteLocalRefs(value, prefix) {
  if (Array.isArray(value)) {
    return value.map((item) => rewriteLocalRefs(item, prefix));
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const output = {};
  for (const [key, child] of Object.entries(value)) {
    if (key === "$schema" || key === "$id") {
      continue;
    }
    output[key] =
      key === "$ref" && typeof child === "string" && child.startsWith("#/$defs/")
        ? `#/$defs/${prefix}/$defs/${child.slice(8)}`
        : rewriteLocalRefs(child, prefix);
  }
  return output;
}

const id = {
  type: "string",
  minLength: 1,
  maxLength: 96,
  pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
};

const prompt = (maxLength) => ({
  type: "string",
  minLength: 1,
  maxLength,
  pattern: "\\S",
});

const sceneBlueprintSchema = {
  $id: "urn:matrix-oasis:scene-blueprint:0.1.0",
  title: "Matrix Oasis Scene Blueprint 0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format",
    "formatVersion",
    "scene",
    "zones",
    "assetBriefs",
    "placements",
    "nodeBindings",
  ],
  properties: {
    format: { type: "string", const: SCENE_BLUEPRINT_FORMAT },
    formatVersion: {
      type: "string",
      const: SCENE_BLUEPRINT_FORMAT_VERSION,
    },
    scene: { $ref: "#/$defs/scene" },
    zones: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_GENERATION_LIMITS.zones,
      items: { $ref: "#/$defs/zone" },
    },
    assetBriefs: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_GENERATION_LIMITS.assetBriefs,
      items: { $ref: "#/$defs/assetBrief" },
    },
    placements: {
      type: "array",
      maxItems: PROTOTYPE_GENERATION_LIMITS.placements,
      items: { $ref: "#/$defs/placement" },
    },
    nodeBindings: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_GENERATION_LIMITS.nodeBindings,
      items: { $ref: "#/$defs/nodeBinding" },
    },
  },
  $defs: {
    id,
    contentVersion: {
      type: "string",
      minLength: 1,
      maxLength: 64,
      pattern: "\\S",
    },
    prose: prompt(4096),
    scene: {
      type: "object",
      additionalProperties: false,
      required: [
        "id",
        "contentVersion",
        "title",
        "environmentPrompt",
        "visualStylePrompt",
      ],
      properties: {
        id: { $ref: "#/$defs/id" },
        contentVersion: { $ref: "#/$defs/contentVersion" },
        title: { $ref: "#/$defs/prose" },
        environmentPrompt: prompt(
          PROTOTYPE_GENERATION_LIMITS.environmentPromptCharacters,
        ),
        visualStylePrompt: prompt(
          PROTOTYPE_GENERATION_LIMITS.visualStylePromptCharacters,
        ),
      },
    },
    zone: {
      type: "object",
      additionalProperties: false,
      required: ["id", "label", "description"],
      properties: {
        id: { $ref: "#/$defs/id" },
        label: { $ref: "#/$defs/prose" },
        description: { $ref: "#/$defs/prose" },
      },
    },
    roles: {
      type: "array",
      minItems: 1,
      maxItems: 2,
      uniqueItems: true,
      items: { type: "string", enum: ["visual", "collider"] },
    },
    assetBrief: {
      type: "object",
      additionalProperties: false,
      required: ["id", "kind", "prompt", "entityId", "roles"],
      properties: {
        id: { $ref: "#/$defs/id" },
        kind: {
          type: "string",
          enum: ["environment", "prop", "character-placeholder"],
        },
        prompt: prompt(PROTOTYPE_GENERATION_LIMITS.briefPromptCharacters),
        entityId: {
          anyOf: [{ $ref: "#/$defs/id" }, { type: "null" }],
        },
        roles: { $ref: "#/$defs/roles" },
      },
    },
    placement: {
      type: "object",
      additionalProperties: false,
      required: ["id", "assetBriefId", "zoneId", "entityId"],
      properties: {
        id: { $ref: "#/$defs/id" },
        assetBriefId: { $ref: "#/$defs/id" },
        zoneId: { $ref: "#/$defs/id" },
        entityId: {
          anyOf: [{ $ref: "#/$defs/id" }, { type: "null" }],
        },
      },
    },
    nodeBinding: {
      type: "object",
      additionalProperties: false,
      required: ["nodeId", "zoneId", "visiblePlacementIds"],
      properties: {
        nodeId: { $ref: "#/$defs/id" },
        zoneId: { $ref: "#/$defs/id" },
        visiblePlacementIds: {
          type: "array",
          uniqueItems: true,
          items: { $ref: "#/$defs/id" },
        },
      },
    },
  },
};

export const SCENE_BLUEPRINT_SCHEMA = deepFreeze(sceneBlueprintSchema);

const embeddedAuthoringSchema = rewriteLocalRefs(
  AUTHORING_GAME_PACK_SCHEMA,
  "authoringGamePack",
);
const embeddedBlueprintSchema = rewriteLocalRefs(
  SCENE_BLUEPRINT_SCHEMA,
  "sceneBlueprint",
);

export const GENERATION_PROPOSAL_SCHEMA = deepFreeze({
  $id: "urn:matrix-oasis:prototype-generation-proposal:0.1.0",
  title: "Matrix Oasis Prototype Generation Proposal 0.1.0",
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "authoringGamePack", "sceneBlueprint"],
  properties: {
    format: { type: "string", const: GENERATION_PROPOSAL_FORMAT },
    formatVersion: {
      type: "string",
      const: GENERATION_PROPOSAL_FORMAT_VERSION,
    },
    authoringGamePack: { $ref: "#/$defs/authoringGamePack" },
    sceneBlueprint: { $ref: "#/$defs/sceneBlueprint" },
  },
  $defs: {
    authoringGamePack: embeddedAuthoringSchema,
    sceneBlueprint: embeddedBlueprintSchema,
  },
});
