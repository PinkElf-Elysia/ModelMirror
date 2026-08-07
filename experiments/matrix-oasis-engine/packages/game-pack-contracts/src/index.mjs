import schema from "../schemas/0.1.0/authoring-game-pack.schema.json" with {
  type: "json",
};

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

export const AUTHORING_GAME_PACK_SCHEMA = deepFreeze(schema);
export const AUTHORING_GAME_PACK_FORMAT =
  AUTHORING_GAME_PACK_SCHEMA.properties.format.const;
export const AUTHORING_GAME_PACK_VERSION =
  AUTHORING_GAME_PACK_SCHEMA.properties.formatVersion.const;
export const AUTHORING_GAME_PACK_SCHEMA_ID = AUTHORING_GAME_PACK_SCHEMA.$id;
