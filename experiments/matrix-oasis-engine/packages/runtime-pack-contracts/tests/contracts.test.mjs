import assert from "node:assert/strict";
import test from "node:test";
import * as contracts from "../src/index.mjs";

const EXPECTED_EXPORTS = [
  "CANONICAL_JSON_PROFILE",
  "CanonicalJsonOperationalError",
  "CanonicalJsonValueError",
  "GAME_PACK_COMPILER_ID",
  "GAME_PACK_COMPILER_VERSION",
  "RUNTIME_GAME_PACK_FORMAT",
  "RUNTIME_GAME_PACK_FORMAT_VERSION",
  "RUNTIME_GAME_PACK_RECEIPT_FORMAT",
  "RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION",
  "RUNTIME_GAME_PACK_RECEIPT_SCHEMA",
  "RUNTIME_GAME_PACK_RECEIPT_SCHEMA_ID",
  "RUNTIME_GAME_PACK_SCHEMA",
  "RUNTIME_GAME_PACK_SCHEMA_ID",
  "canonicalizeJsonValue",
];

function walk(value, visit) {
  if (!value || typeof value !== "object") {
    return;
  }
  visit(value);
  for (const child of Object.values(value)) {
    walk(child, visit);
  }
}

test("exports exactly the frozen R3 contract surface", () => {
  assert.deepEqual(Object.keys(contracts).sort(), EXPECTED_EXPORTS.sort());
  assert.equal(contracts.RUNTIME_GAME_PACK_FORMAT, "matrix-oasis.runtime-game-pack");
  assert.equal(contracts.RUNTIME_GAME_PACK_FORMAT_VERSION, "0.1.0");
  assert.equal(
    contracts.RUNTIME_GAME_PACK_SCHEMA_ID,
    "urn:matrix-oasis:runtime-game-pack:0.1.0",
  );
  assert.equal(
    contracts.RUNTIME_GAME_PACK_RECEIPT_FORMAT,
    "matrix-oasis.runtime-game-pack-receipt",
  );
  assert.equal(contracts.RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION, "0.1.0");
  assert.equal(
    contracts.RUNTIME_GAME_PACK_RECEIPT_SCHEMA_ID,
    "urn:matrix-oasis:runtime-game-pack-receipt:0.1.0",
  );
  assert.equal(
    contracts.CANONICAL_JSON_PROFILE,
    "matrix-oasis.canonical-json/1",
  );
  assert.equal(
    contracts.GAME_PACK_COMPILER_ID,
    "@matrix-oasis/game-pack-compiler",
  );
  assert.equal(contracts.GAME_PACK_COMPILER_VERSION, "0.1.0-r3");
});

test("deep-freezes both authoritative schemas", () => {
  for (const schema of [
    contracts.RUNTIME_GAME_PACK_SCHEMA,
    contracts.RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
  ]) {
    walk(schema, (value) => assert.equal(Object.isFrozen(value), true));
  }
});

test("closes every schema object and uses only local references", () => {
  for (const schema of [
    contracts.RUNTIME_GAME_PACK_SCHEMA,
    contracts.RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
  ]) {
    const references = [];
    walk(schema, (value) => {
      if (typeof value.$ref === "string") {
        references.push(value.$ref);
      }
      if (value.type === "object") {
        assert.equal(value.additionalProperties, false);
      }
    });
    assert.ok(references.length > 0);
    assert.equal(
      references.every((reference) => reference.startsWith("#/$defs/")),
      true,
    );
  }
});

test("requires normalized optionals and zero-based index references", () => {
  const schema = contracts.RUNTIME_GAME_PACK_SCHEMA;
  assert.ok(schema.required.includes("summary"));
  assert.ok(schema.$defs.entity.required.includes("description"));
  assert.ok(schema.$defs.node.required.includes("text"));
  assert.ok(schema.$defs.action.required.includes("when"));
  assert.ok(schema.$defs.action.required.includes("entityIndexes"));
  assert.equal(schema.$defs.index.minimum, 0);
  assert.equal(
    schema.$defs.nodeTarget.properties.index.$ref,
    "#/$defs/index",
  );
  assert.equal(
    schema.$defs.emitCueEffect.properties.cueIndex.$ref,
    "#/$defs/index",
  );
  assert.deepEqual(schema.$defs.booleanVariable.required, ["id", "type", "initial"]);
  assert.deepEqual(schema.$defs.integerVariable.required, ["id", "type", "initial"]);
  assert.equal(schema.$defs.booleanVariable.properties.allowedValues, undefined);
  assert.equal(schema.$defs.integerVariable.properties.allowedValues, undefined);
});

test("locks every observable reference to a required runtime index", () => {
  const schema = contracts.RUNTIME_GAME_PACK_SCHEMA;
  const indexFields = [
    [schema, "entryNodeIndex", "#/$defs/index"],
    [schema.$defs.node, "entityIndexes", "#/$defs/indexList"],
    [schema.$defs.action, "entityIndexes", "#/$defs/indexList"],
    [schema.$defs.node, "entryCueIndexes", "#/$defs/indexList"],
    [schema.$defs.ending, "cueIndexes", "#/$defs/indexList"],
    [schema.$defs.nodeTarget, "index", "#/$defs/index"],
    [schema.$defs.endingTarget, "index", "#/$defs/index"],
    [schema.$defs.emitCueEffect, "cueIndex", "#/$defs/index"],
  ];
  for (const [definition, property, reference] of indexFields) {
    assert.ok(definition.required.includes(property));
    assert.equal(definition.properties[property].$ref, reference);
  }

  const variableIndexDefinitions = [
    "eqCondition",
    "neCondition",
    "ltCondition",
    "lteCondition",
    "gtCondition",
    "gteCondition",
    "setEffect",
    "addEffect",
  ];
  for (const definitionName of variableIndexDefinitions) {
    const definition = schema.$defs[definitionName];
    assert.ok(definition.required.includes("variableIndex"));
    assert.equal(definition.properties.variableIndex.$ref, "#/$defs/index");
  }

  assert.ok(schema.$defs.ending.required.includes("text"));
  assert.equal(
    schema.$defs.ending.properties.text.$ref,
    "#/$defs/nullableProse",
  );
});

test("excludes Authoring ID-reference fields from the runtime schema", () => {
  const schema = contracts.RUNTIME_GAME_PACK_SCHEMA;
  const declaredPropertyNames = [];
  walk(schema, (value) => {
    if (value.properties && typeof value.properties === "object") {
      declaredPropertyNames.push(...Object.keys(value.properties));
    }
  });

  for (const authoringField of [
    "entryNodeId",
    "entityIds",
    "cueIds",
    "variableId",
    "cueId",
  ]) {
    assert.equal(declaredPropertyNames.includes(authoringField), false);
  }
  assert.equal(schema.$defs.nodeTarget.properties.id, undefined);
  assert.equal(schema.$defs.endingTarget.properties.id, undefined);
});

test("keeps receipt identity independent from its artifact digest", () => {
  const schema = contracts.RUNTIME_GAME_PACK_RECEIPT_SCHEMA;
  assert.deepEqual(schema.required, [
    "format",
    "formatVersion",
    "canonicalization",
    "compiler",
    "artifact",
  ]);
  assert.deepEqual(schema.$defs.artifact.required, [
    "format",
    "formatVersion",
    "sha256",
    "byteLength",
  ]);
  assert.equal(schema.$defs.compiler.properties.id.const, contracts.GAME_PACK_COMPILER_ID);
  assert.equal(
    schema.$defs.compiler.properties.version.const,
    contracts.GAME_PACK_COMPILER_VERSION,
  );
  assert.equal(schema.properties.receipt, undefined);
  assert.equal(schema.$defs.artifact.properties.byteLength.minimum, 1);
});

test("uses static canonical JSON error identities", () => {
  const valueError = new contracts.CanonicalJsonValueError();
  assert.equal(valueError.name, "CanonicalJsonValueError");
  assert.equal(valueError.message, "CANONICAL_JSON_VALUE_INVALID");
  assert.equal(valueError.code, "CANONICAL_JSON_VALUE_INVALID");

  const operationalError = new contracts.CanonicalJsonOperationalError();
  assert.equal(operationalError.name, "CanonicalJsonOperationalError");
  assert.equal(operationalError.message, "CANONICAL_JSON_INTERNAL_ERROR");
  assert.equal(operationalError.code, "CANONICAL_JSON_INTERNAL_ERROR");
});
