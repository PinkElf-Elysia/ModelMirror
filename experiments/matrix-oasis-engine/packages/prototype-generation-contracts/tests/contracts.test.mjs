import assert from "node:assert/strict";
import test from "node:test";
import { AUTHORING_GAME_PACK_SCHEMA } from "@matrix-oasis/game-pack-contracts";
import {
  GENERATION_PROPOSAL_FORMAT,
  GENERATION_PROPOSAL_FORMAT_VERSION,
  GENERATION_PROPOSAL_SCHEMA,
  PROTOTYPE_GENERATION_LIMITS,
  SCENE_BLUEPRINT_FORMAT,
  SCENE_BLUEPRINT_FORMAT_VERSION,
  SCENE_BLUEPRINT_SCHEMA,
  prepareGenerationProposalJson,
  validateGenerationProposalJson,
} from "../src/index.mjs";

function proposalFixture() {
  return {
    format: GENERATION_PROPOSAL_FORMAT,
    formatVersion: GENERATION_PROPOSAL_FORMAT_VERSION,
    authoringGamePack: {
      format: "matrix-oasis.authoring-game-pack",
      formatVersion: "0.1.0",
      id: "neutral-prototype",
      contentVersion: "1.0.0",
      language: "zh-CN",
      title: "中性原型",
      entryNodeId: "node-start",
      entities: [{ id: "object-console", label: "控制台" }],
      variables: [],
      cues: [],
      nodes: [
        {
          id: "node-start",
          title: "起点",
          entityIds: ["object-console"],
          entryCueIds: [],
          actions: [
            {
              id: "action-continue",
              label: "继续",
              effects: [],
              target: { kind: "node", id: "node-next" },
            },
          ],
        },
        {
          id: "node-next",
          title: "终点前",
          entityIds: ["object-console"],
          entryCueIds: [],
          actions: [
            {
              id: "action-finish",
              label: "完成",
              effects: [],
              target: { kind: "ending", id: "ending-complete" },
            },
          ],
        },
      ],
      endings: [{ id: "ending-complete", title: "完成", cueIds: [] }],
    },
    sceneBlueprint: {
      format: SCENE_BLUEPRINT_FORMAT,
      formatVersion: SCENE_BLUEPRINT_FORMAT_VERSION,
      scene: {
        id: "neutral-prototype",
        contentVersion: "1.0.0",
        title: "中性原型场景",
        environmentPrompt: "一个封闭、清晰、可漫游的中性测试空间",
        visualStylePrompt: "低复杂度几何体，克制的工业设计语言",
      },
      zones: [
        { id: "zone-main", label: "主空间", description: "主要交互空间" },
      ],
      assetBriefs: [
        {
          id: "asset-environment",
          kind: "environment",
          prompt: "封闭房间、地面和墙体",
          entityId: null,
          roles: ["visual", "collider"],
        },
        {
          id: "asset-console",
          kind: "prop",
          prompt: "简洁的独立控制台道具",
          entityId: "object-console",
          roles: ["visual"],
        },
      ],
      placements: [
        {
          id: "placement-environment",
          assetBriefId: "asset-environment",
          zoneId: "zone-main",
          entityId: null,
        },
        {
          id: "placement-console",
          assetBriefId: "asset-console",
          zoneId: "zone-main",
          entityId: "object-console",
        },
      ],
      nodeBindings: [
        {
          nodeId: "node-start",
          zoneId: "zone-main",
          visiblePlacementIds: ["placement-environment", "placement-console"],
        },
        {
          nodeId: "node-next",
          zoneId: "zone-main",
          visiblePlacementIds: ["placement-environment", "placement-console"],
        },
      ],
    },
  };
}

function validate(value) {
  return validateGenerationProposalJson(JSON.stringify(value));
}

function diagnosticPairs(value) {
  return validate(value).diagnostics.map((item) => [item.code, item.path]);
}

function assertHas(value, code, path) {
  assert.ok(
    diagnosticPairs(value).some(
      ([actualCode, actualPath]) => actualCode === code && actualPath === path,
    ),
    `${code} ${path}`,
  );
}

test("schemas are closed, frozen, and embed the frozen Authoring contract", () => {
  assert.equal(GENERATION_PROPOSAL_SCHEMA.additionalProperties, false);
  assert.equal(SCENE_BLUEPRINT_SCHEMA.additionalProperties, false);
  assert.equal(Object.isFrozen(GENERATION_PROPOSAL_SCHEMA), true);
  assert.equal(Object.isFrozen(SCENE_BLUEPRINT_SCHEMA.$defs.assetBrief), true);
  assert.deepEqual(
    GENERATION_PROPOSAL_SCHEMA.$defs.authoringGamePack.required,
    AUTHORING_GAME_PACK_SCHEMA.required,
  );
  assert.equal(
    Object.keys(GENERATION_PROPOSAL_SCHEMA.$defs.authoringGamePack.$defs).length,
    Object.keys(AUTHORING_GAME_PACK_SCHEMA.$defs).length,
  );
  assert.deepEqual(PROTOTYPE_GENERATION_LIMITS, {
    documentDepth: 256,
    zones: 16,
    assetBriefs: 16,
    placements: 128,
    nodeBindings: 4096,
    environmentPromptCharacters: 4096,
    visualStylePromptCharacters: 2048,
    briefPromptCharacters: 2048,
  });
});

test("golden proposal prepares three deterministic canonical documents", () => {
  const input = proposalFixture();
  const before = JSON.stringify(input);
  const results = Array.from({ length: 20 }, () =>
    prepareGenerationProposalJson(JSON.stringify(input)),
  );
  for (const result of results) {
    assert.equal(result.ok, true);
    assert.equal(result.validationReport.valid, true);
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.value.sceneBlueprint.assetBriefs), true);
    assert.deepEqual(JSON.parse(result.canonicalProposalJson), input);
    assert.deepEqual(JSON.parse(result.canonicalAuthoringJson), input.authoringGamePack);
    assert.deepEqual(
      JSON.parse(result.canonicalSceneBlueprintJson),
      input.sceneBlueprint,
    );
  }
  assert.equal(new Set(results.map((item) => item.canonicalProposalJson)).size, 1);
  assert.equal(JSON.stringify(input), before);
});

test("parse gate rejects type, syntax, comments, trailing comma, duplicate keys, and depth", () => {
  assert.deepEqual(validateGenerationProposalJson(null).diagnostics[0], {
    phase: "parse",
    severity: "error",
    code: "PROTOTYPE_PROPOSAL_JSON_INPUT_TYPE",
    path: "",
    message: "PROTOTYPE_PROPOSAL_JSON_INPUT_TYPE",
  });
  for (const text of ["", "{", "{/*x*/}", '{"format":1,}']) {
    assert.equal(validateGenerationProposalJson(text).diagnostics[0].phase, "parse");
  }
  const duplicate = validateGenerationProposalJson(
    '{"format":"a","format":"b"}',
  );
  assert.equal(duplicate.diagnostics[0].code, "PROTOTYPE_PROPOSAL_JSON_DUPLICATE_KEY");
  assert.equal(duplicate.diagnostics[0].path, "");
  const deep = `${"[".repeat(257)}0${"]".repeat(257)}`;
  assert.equal(
    validateGenerationProposalJson(deep).diagnostics[0].code,
    "PROTOTYPE_PROPOSAL_JSON_DEPTH_EXCEEDED",
  );
});

test("schema gate rejects missing, unknown, over-budget, and numeric substitutions", () => {
  const missing = proposalFixture();
  delete missing.sceneBlueprint.scene.title;
  assertHas(
    missing,
    "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED",
    "/sceneBlueprint/scene/title",
  );

  const secret = ["private", "material"].join("-");
  const unknown = proposalFixture();
  unknown.sceneBlueprint.scene[secret] = "do-not-copy";
  const unknownReport = validate(unknown);
  assert.equal(unknownReport.diagnostics[0].path, "/sceneBlueprint/scene");
  assert.equal(JSON.stringify(unknownReport).includes(secret), false);

  const tooManyZones = proposalFixture();
  tooManyZones.sceneBlueprint.zones = Array.from({ length: 17 }, (_, index) => ({
    id: `zone-${index}`,
    label: "区域",
    description: "区域描述",
  }));
  assertHas(
    tooManyZones,
    "PROTOTYPE_PROPOSAL_SCHEMA_MAX_ITEMS",
    "/sceneBlueprint/zones",
  );

  const overBudgetCollections = [
    ["assetBriefs", 17, () => ({
      id: "asset-extra",
      kind: "prop",
      prompt: "额外道具",
      entityId: "object-console",
      roles: ["visual"],
    })],
    ["placements", 129, () => ({
      id: "placement-extra",
      assetBriefId: "asset-console",
      zoneId: "zone-main",
      entityId: "object-console",
    })],
    ["nodeBindings", 4097, () => ({
      nodeId: "node-start",
      zoneId: "zone-main",
      visiblePlacementIds: ["placement-environment"],
    })],
  ];
  for (const [property, count, makeItem] of overBudgetCollections) {
    const value = proposalFixture();
    value.sceneBlueprint[property] = Array.from({ length: count }, (_, index) => ({
      ...makeItem(),
      ...(property === "assetBriefs" || property === "placements"
        ? { id: `${property === "assetBriefs" ? "asset" : "placement"}-${index}` }
        : {}),
    }));
    assertHas(
      value,
      "PROTOTYPE_PROPOSAL_SCHEMA_MAX_ITEMS",
      `/sceneBlueprint/${property}`,
    );
  }

  const longPrompt = proposalFixture();
  longPrompt.sceneBlueprint.scene.environmentPrompt = "x".repeat(4097);
  assertHas(
    longPrompt,
    "PROTOTYPE_PROPOSAL_SCHEMA_STRING_CONSTRAINT",
    "/sceneBlueprint/scene/environmentPrompt",
  );

  const numeric = proposalFixture();
  numeric.sceneBlueprint.scene.title = 1.5;
  assertHas(
    numeric,
    "PROTOTYPE_PROPOSAL_SCHEMA_TYPE",
    "/sceneBlueprint/scene/title",
  );
});

test("schema-valid isolated surrogate text is rejected without replacement", () => {
  const value = proposalFixture();
  value.sceneBlueprint.scene.environmentPrompt = String.fromCharCode(0xd800);
  assertHas(
    value,
    "PROTOTYPE_PROPOSAL_TEXT_UNPAIRED_SURROGATE",
    "/sceneBlueprint/scene/environmentPrompt",
  );
});

test("frozen Authoring validator gates cross-contract semantics", () => {
  const value = proposalFixture();
  value.authoringGamePack.nodes[0].entityIds = ["missing-entity"];
  const result = validate(value);
  assert.equal(result.valid, false);
  assert.equal(result.diagnostics.every((item) => item.path.startsWith("/authoringGamePack")), true);
  assert.equal(result.diagnostics.some((item) => item.code === "PACK_ENTITY_REFERENCE_UNKNOWN"), true);
});

test("scene identity must match Authoring identity", () => {
  const id = proposalFixture();
  id.sceneBlueprint.scene.id = "other-prototype";
  assertHas(id, "SCENE_BLUEPRINT_SCENE_ID_MISMATCH", "/sceneBlueprint/scene/id");
  const version = proposalFixture();
  version.sceneBlueprint.scene.contentVersion = "2.0.0";
  assertHas(
    version,
    "SCENE_BLUEPRINT_CONTENT_VERSION_MISMATCH",
    "/sceneBlueprint/scene/contentVersion",
  );
});

test("blueprint declaration IDs are unique across declaration collections", () => {
  const duplicate = proposalFixture();
  duplicate.sceneBlueprint.assetBriefs[0].id = "zone-main";
  assertHas(
    duplicate,
    "SCENE_BLUEPRINT_ASSET_BRIEF_ID_DUPLICATE",
    "/sceneBlueprint/assetBriefs/0/id",
  );
});

test("exactly one valid environment brief and placement are required", () => {
  const duplicateBrief = proposalFixture();
  duplicateBrief.sceneBlueprint.assetBriefs.push({
    id: "asset-environment-two",
    kind: "environment",
    prompt: "第二个环境",
    entityId: null,
    roles: ["visual", "collider"],
  });
  assertHas(
    duplicateBrief,
    "SCENE_BLUEPRINT_ENVIRONMENT_BRIEF_COUNT",
    "/sceneBlueprint/assetBriefs",
  );

  const roles = proposalFixture();
  roles.sceneBlueprint.assetBriefs[0].roles = ["visual"];
  assertHas(
    roles,
    "SCENE_BLUEPRINT_ENVIRONMENT_ROLES_INVALID",
    "/sceneBlueprint/assetBriefs/0/roles",
  );

  const duplicatePlacement = proposalFixture();
  duplicatePlacement.sceneBlueprint.placements.push({
    id: "placement-environment-two",
    assetBriefId: "asset-environment",
    zoneId: "zone-main",
    entityId: null,
  });
  assertHas(
    duplicatePlacement,
    "SCENE_BLUEPRINT_ENVIRONMENT_PLACEMENT_COUNT",
    "/sceneBlueprint/placements",
  );
});

test("asset and placement entity references are typed and exact", () => {
  const asset = proposalFixture();
  asset.sceneBlueprint.assetBriefs[1].entityId = "missing-entity";
  assertHas(
    asset,
    "SCENE_BLUEPRINT_ASSET_ENTITY_REFERENCE_INVALID",
    "/sceneBlueprint/assetBriefs/1/entityId",
  );

  const placement = proposalFixture();
  placement.sceneBlueprint.placements[1].entityId = null;
  assertHas(
    placement,
    "SCENE_BLUEPRINT_PLACEMENT_ENTITY_MISMATCH",
    "/sceneBlueprint/placements/1/entityId",
  );
});

test("placement references resolve to existing assets and zones", () => {
  const asset = proposalFixture();
  asset.sceneBlueprint.placements[1].assetBriefId = "missing-asset";
  assertHas(
    asset,
    "SCENE_BLUEPRINT_ASSET_REFERENCE_NOT_FOUND",
    "/sceneBlueprint/placements/1/assetBriefId",
  );

  const zone = proposalFixture();
  zone.sceneBlueprint.placements[1].zoneId = "missing-zone";
  assertHas(
    zone,
    "SCENE_BLUEPRINT_ZONE_REFERENCE_NOT_FOUND",
    "/sceneBlueprint/placements/1/zoneId",
  );
});

test("every Authoring node has exactly one valid binding", () => {
  const missing = proposalFixture();
  missing.sceneBlueprint.nodeBindings.pop();
  assertHas(
    missing,
    "SCENE_BLUEPRINT_NODE_BINDING_MISSING",
    "/authoringGamePack/nodes/1/id",
  );

  const duplicate = proposalFixture();
  duplicate.sceneBlueprint.nodeBindings.push(
    structuredClone(duplicate.sceneBlueprint.nodeBindings[0]),
  );
  assertHas(
    duplicate,
    "SCENE_BLUEPRINT_NODE_BINDING_DUPLICATE",
    "/sceneBlueprint/nodeBindings/2/nodeId",
  );

  const extra = proposalFixture();
  extra.sceneBlueprint.nodeBindings.push({
    nodeId: "node-unknown",
    zoneId: "zone-main",
    visiblePlacementIds: ["placement-environment"],
  });
  assertHas(
    extra,
    "SCENE_BLUEPRINT_NODE_REFERENCE_NOT_FOUND",
    "/sceneBlueprint/nodeBindings/2/nodeId",
  );
});

test("node bindings use existing zones and visible placements", () => {
  const zone = proposalFixture();
  zone.sceneBlueprint.nodeBindings[0].zoneId = "missing-zone";
  assertHas(
    zone,
    "SCENE_BLUEPRINT_ZONE_REFERENCE_NOT_FOUND",
    "/sceneBlueprint/nodeBindings/0/zoneId",
  );

  const placement = proposalFixture();
  placement.sceneBlueprint.nodeBindings[0].visiblePlacementIds.push("missing-placement");
  assertHas(
    placement,
    "SCENE_BLUEPRINT_PLACEMENT_REFERENCE_NOT_FOUND",
    "/sceneBlueprint/nodeBindings/0/visiblePlacementIds/2",
  );

  const environment = proposalFixture();
  environment.sceneBlueprint.nodeBindings[0].visiblePlacementIds = ["placement-console"];
  assertHas(
    environment,
    "SCENE_BLUEPRINT_ENVIRONMENT_NOT_VISIBLE",
    "/sceneBlueprint/nodeBindings/0/visiblePlacementIds",
  );
});

test("validation reports are stable, static, and deeply frozen", () => {
  const invalid = proposalFixture();
  invalid.sceneBlueprint.scene.id = "wrong-id";
  const reports = Array.from({ length: 20 }, () => validate(invalid));
  assert.equal(new Set(reports.map((item) => JSON.stringify(item))).size, 1);
  assert.equal(Object.isFrozen(reports[0]), true);
  assert.equal(Object.isFrozen(reports[0].diagnostics), true);
  assert.equal(Object.isFrozen(reports[0].diagnostics[0]), true);
  assert.equal(reports[0].diagnostics[0].message, reports[0].diagnostics[0].code);
  assert.equal(JSON.stringify(reports[0]).includes("wrong-id"), false);
});
