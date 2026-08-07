import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  AUTHORING_GAME_PACK_FORMAT,
  AUTHORING_GAME_PACK_SCHEMA,
  AUTHORING_GAME_PACK_SCHEMA_ID,
  AUTHORING_GAME_PACK_VERSION,
} from "../src/index.mjs";

const packageRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function walkSchema(value, visit) {
  if (!value || typeof value !== "object") {
    return;
  }
  visit(value);
  for (const child of Object.values(value)) {
    walkSchema(child, visit);
  }
}

function taggedValues(definitionNames, property = "op") {
  return definitionNames.map(
    (name) => AUTHORING_GAME_PACK_SCHEMA.$defs[name].properties[property].const,
  );
}

test("exports the frozen authoritative contract identity", () => {
  assert.equal(AUTHORING_GAME_PACK_FORMAT, "matrix-oasis.authoring-game-pack");
  assert.equal(AUTHORING_GAME_PACK_VERSION, "0.1.0");
  assert.equal(
    AUTHORING_GAME_PACK_SCHEMA_ID,
    "urn:matrix-oasis:authoring-game-pack:0.1.0",
  );
  assert.equal(Object.isFrozen(AUTHORING_GAME_PACK_SCHEMA), true);
  assert.equal(Object.isFrozen(AUTHORING_GAME_PACK_SCHEMA.$defs), true);
});

test("uses only local references and closes every data object", () => {
  const references = [];
  walkSchema(AUTHORING_GAME_PACK_SCHEMA, (schema) => {
    if (typeof schema.$ref === "string") {
      references.push(schema.$ref);
    }
    if (schema.type === "object") {
      assert.equal(schema.additionalProperties, false);
    }
  });
  assert.ok(references.length > 0);
  assert.equal(
    references.every((reference) => reference.startsWith("#/$defs/")),
    true,
  );
});

test("freezes the R1 variable, condition, effect, and target vocabulary", () => {
  assert.deepEqual(
    taggedValues(["booleanVariable", "integerVariable", "enumVariable"], "type"),
    ["boolean", "integer", "enum"],
  );
  assert.deepEqual(
    taggedValues([
      "allCondition",
      "anyCondition",
      "notCondition",
      "eqCondition",
      "neCondition",
      "ltCondition",
      "lteCondition",
      "gtCondition",
      "gteCondition",
    ]),
    ["all", "any", "not", "eq", "ne", "lt", "lte", "gt", "gte"],
  );
  assert.deepEqual(
    taggedValues(["setEffect", "addEffect", "emitCueEffect"]),
    ["set", "add", "emitCue"],
  );
  assert.deepEqual(
    taggedValues(["nodeTarget", "endingTarget"], "kind"),
    ["node", "ending"],
  );
});

test("retains the approved structural limits and identifier alphabet", () => {
  const { $defs, properties } = AUTHORING_GAME_PACK_SCHEMA;
  assert.equal($defs.id.maxLength, 96);
  assert.equal($defs.prose.maxLength, 4096);
  assert.equal(properties.nodes.maxItems, 4096);
  assert.equal($defs.node.properties.actions.maxItems, 64);
  assert.equal($defs.action.properties.effects.maxItems, 32);
  const idPattern = new RegExp($defs.id.pattern);
  assert.equal(idPattern.test("mechanic-node-1"), true);
  assert.equal(idPattern.test("Mechanic_Node"), false);
});

test("keeps sample-specific concepts out of the public schema", async () => {
  const schemaText = await readFile(
    path.join(packageRoot, "schemas", "0.1.0", "authoring-game-pack.schema.json"),
    "utf8",
  );
  const forbiddenKeys = [
    "facts",
    "evidence",
    "npcBeliefs",
    "assetPath",
    "godotScene",
    "transform",
  ];
  for (const key of forbiddenKeys) {
    assert.equal(schemaText.includes(`\"${key}\"`), false);
  }
});
