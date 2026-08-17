import assert from "node:assert/strict";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { prepareGenerationProposalJson } from "@matrix-oasis/prototype-generation-contracts";
import {
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {
  generatePrototype,
  PrototypeGeneratorOperationalError,
} from "../src/index.mjs";
import { generatePrototypeWithServices } from "../src/generator.mjs";
import {
  evaluateAcceptanceProfile,
  normalizeAcceptanceOptions,
} from "../src/acceptance-profile.mjs";

function proposalFixture() {
  return {
    format: "matrix-oasis.prototype-generation-proposal",
    formatVersion: "0.1.0",
    authoringGamePack: {
      format: "matrix-oasis.authoring-game-pack",
      formatVersion: "0.1.0",
      id: "generated-neutral-prototype",
      contentVersion: "1.0.0",
      language: "zh-CN",
      title: "生成的中性空间",
      entryNodeId: "node-start",
      entities: [{ id: "object-terminal", label: "交互终端" }],
      variables: [],
      cues: [],
      nodes: [
        {
          id: "node-start",
          title: "入口",
          entityIds: ["object-terminal"],
          entryCueIds: [],
          actions: [
            {
              id: "action-finish",
              label: "完成验证",
              effects: [],
              target: { kind: "ending", id: "ending-complete" },
            },
          ],
        },
      ],
      endings: [{ id: "ending-complete", title: "完成", cueIds: [] }],
    },
    sceneBlueprint: {
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      scene: {
        id: "generated-neutral-prototype",
        contentVersion: "1.0.0",
        title: "生成的中性空间",
        environmentPrompt: "一个封闭、可漫游、具有明确地面和墙体的中性空间",
        visualStylePrompt: "克制的低复杂度工业几何风格",
      },
      zones: [
        { id: "zone-main", label: "主空间", description: "入口和终端所在空间" },
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
          id: "asset-terminal",
          kind: "prop",
          prompt: "一台简洁的静态交互终端",
          entityId: "object-terminal",
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
          id: "placement-terminal",
          assetBriefId: "asset-terminal",
          zoneId: "zone-main",
          entityId: "object-terminal",
        },
      ],
      nodeBindings: [
        {
          nodeId: "node-start",
          zoneId: "zone-main",
          visiblePlacementIds: ["placement-environment", "placement-terminal"],
        },
      ],
    },
  };
}

function acceptanceFixture({ cycle = true } = {}) {
  const proposal = proposalFixture();
  proposal.authoringGamePack.entities.push({ id: "person-placeholder", label: "Person" });
  proposal.authoringGamePack.nodes[0].actions = [
    {
      id: "action-enter",
      label: "Enter",
      effects: [],
      target: { kind: "node", id: "node-second" },
    },
    {
      id: "action-end-one",
      label: "Finish one",
      effects: [],
      target: { kind: "ending", id: "ending-complete" },
    },
  ];
  proposal.authoringGamePack.nodes.push({
    id: "node-second",
    title: "Second",
    entityIds: ["person-placeholder"],
    entryCueIds: [],
    actions: [
      ...(cycle
        ? [
            {
              id: "action-return",
              label: "Return",
              effects: [],
              target: { kind: "node", id: "node-start" },
            },
          ]
        : []),
      {
        id: "action-end-two",
        label: "Finish two",
        effects: [],
        target: { kind: "ending", id: "ending-second" },
      },
    ],
  });
  proposal.authoringGamePack.endings.push({
    id: "ending-second",
    title: "Second ending",
    cueIds: [],
  });
  proposal.sceneBlueprint.zones.push({
    id: "zone-second",
    label: "Second zone",
    description: "A connected neutral zone",
  });
  proposal.sceneBlueprint.assetBriefs.push({
    id: "asset-person",
    kind: "character-placeholder",
    prompt: "A static neutral person placeholder",
    entityId: "person-placeholder",
    roles: ["visual"],
  });
  proposal.sceneBlueprint.placements.push({
    id: "placement-person",
    assetBriefId: "asset-person",
    zoneId: "zone-second",
    entityId: "person-placeholder",
  });
  proposal.sceneBlueprint.nodeBindings.push({
    nodeId: "node-second",
    zoneId: "zone-second",
    visiblePlacementIds: ["placement-environment", "placement-person"],
  });
  return proposal;
}

function acceptanceProfile(overrides = {}) {
  return {
    format: "matrix-oasis.prototype-acceptance-profile",
    formatVersion: "0.1.0",
    nodes: { min: 2, max: 2 },
    endings: { min: 2, max: 2 },
    actions: { min: 3, max: 4 },
    zones: { min: 2, max: 2 },
    props: { min: 1, max: 1 },
    characterPlaceholders: { min: 1, max: 1 },
    requireReachableCycle: true,
    requireAllEndingsReachable: true,
    requireAllNonEnvironmentBriefsBound: true,
    ...overrides,
  };
}

function response(candidate, requestIndex) {
  return Object.freeze({
    candidateText: candidate,
    model: "fake-neutral-model",
    usage: Object.freeze({
      promptTokens: 10 + requestIndex,
      completionTokens: 20 + requestIndex,
      totalTokens: 30 + requestIndex * 2,
    }),
  });
}

function fakeProvider(candidates) {
  const requests = [];
  const provider = Object.freeze({
    kind: "fake",
    model: "fake-neutral-model",
    async requestProposal(request) {
      const index = requests.length;
      requests.push(structuredClone(request));
      const candidate = candidates[Math.min(index, candidates.length - 1)];
      return response(candidate, index);
    },
  });
  return { provider, requests };
}

function services(overrides = {}) {
  return {
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    digest: (algorithm, bytes) => globalThis.crypto.subtle.digest(algorithm, bytes),
    ...overrides,
  };
}

function assertOperational(error, sentinel) {
  assert.equal(error instanceof PrototypeGeneratorOperationalError, true);
  assert.equal(error.code, "PROTOTYPE_GENERATOR_INTERNAL_ERROR");
  assert.equal(error.message, "PROTOTYPE_GENERATOR_INTERNAL_ERROR");
  assert.equal("cause" in error, false);
  if (sentinel) {
    assert.equal(String(error).includes(sentinel), false);
    assert.equal(JSON.stringify(error).includes(sentinel), false);
  }
  return true;
}

test("one valid candidate compiles and creates five canonical artifacts", async () => {
  const proposal = proposalFixture();
  const { provider, requests } = fakeProvider([JSON.stringify(proposal)]);
  const result = await generatePrototype(
    { prompt: "生成一个中性房间和一个可完成的基础交互" },
    provider,
  );
  assert.equal(result.ok, true);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {
    kind: "initial",
    prompt: "生成一个中性房间和一个可完成的基础交互",
  });
  assert.deepEqual(Reflect.ownKeys(result.artifacts), [
    "authoringGamePackJson",
    "sceneBlueprintJson",
    "runtimeGamePackJson",
    "runtimeReceiptJson",
    "generationReportJson",
  ]);
  for (const text of Object.values(result.artifacts)) {
    assert.equal(typeof text, "string");
    assert.doesNotThrow(() => JSON.parse(text));
    assert.equal(text.endsWith("\n"), false);
  }
  const report = JSON.parse(result.artifacts.generationReportJson);
  assert.equal(report.model, "fake-neutral-model");
  assert.equal(report.requestCount, 1);
  assert.deepEqual(report.usage, {
    completionTokens: 20,
    promptTokens: 10,
    totalTokens: 30,
  });
  assert.equal(report.artifacts.length, 4);
  assert.equal(report.runtimeCheck.status, "ready");
  assert.equal(report.runtimeCheck.declaredActionCount, 1);
  assert.equal(JSON.stringify(report).includes("生成一个中性房间"), false);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.artifacts), true);
});

test("one and two directed repairs succeed without resending the prompt", async () => {
  const valid = JSON.stringify(proposalFixture());
  for (const invalidCount of [1, 2]) {
    const { provider, requests } = fakeProvider([
      ...Array.from({ length: invalidCount }, () => "{}"),
      valid,
    ]);
    const result = await generatePrototype({ prompt: "neutral request" }, provider);
    assert.equal(result.ok, true);
    assert.equal(requests.length, invalidCount + 1);
    for (const request of requests.slice(1)) {
      assert.deepEqual(Reflect.ownKeys(request), [
        "kind",
        "previousCandidate",
        "diagnostics",
      ]);
      assert.equal(request.kind, "repair");
      assert.equal("prompt" in request, false);
      assert.equal(request.diagnostics.length > 0, true);
      assert.equal(
        request.diagnostics.every(
          (item) =>
            Reflect.ownKeys(item).join(",") === "code,path" &&
            item.code === item.code.toUpperCase(),
        ),
        true,
      );
    }
    assert.equal(JSON.parse(result.artifacts.generationReportJson).requestCount, invalidCount + 1);
  }
});

test("acceptance profile admits a generic graph without changing the public result shape", async () => {
  const profile = acceptanceProfile();
  const profileBefore = structuredClone(profile);
  const { provider, requests } = fakeProvider([JSON.stringify(acceptanceFixture())]);
  const result = await generatePrototype(
    { prompt: "Create a connected neutral prototype" },
    provider,
    { acceptanceProfile: profile },
  );
  assert.equal(result.ok, true);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {
    kind: "initial",
    prompt: "Create a connected neutral prototype",
    acceptanceProfile: profile,
  });
  assert.deepEqual(profile, profileBefore);
  assert.deepEqual(Reflect.ownKeys(result), ["ok", "artifacts"]);
  assert.equal(JSON.parse(result.artifacts.generationReportJson).requestCount, 1);
});

test("a structurally valid candidate that misses the profile enters a directed repair", async () => {
  const firstCandidate = JSON.stringify(acceptanceFixture({ cycle: false }));
  const acceptedCandidate = JSON.stringify(acceptanceFixture());
  const { provider, requests } = fakeProvider([firstCandidate, acceptedCandidate]);
  const result = await generatePrototype(
    { prompt: "Create a reusable connected prototype" },
    provider,
    { acceptanceProfile: acceptanceProfile() },
  );
  assert.equal(result.ok, true);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1], {
    kind: "repair",
    previousCandidate: firstCandidate,
    diagnostics: [
      {
        code: "PROTOTYPE_ACCEPTANCE_REACHABLE_CYCLE_REQUIRED",
        path: "/authoringGamePack/nodes",
      },
    ],
    acceptanceProfile: acceptanceProfile(),
  });
  assert.equal("prompt" in requests[1], false);
});

test("acceptance repairs downstream environment prompt budgets before returning artifacts", async () => {
  const oversized = acceptanceFixture();
  oversized.sceneBlueprint.scene.environmentPrompt = "e".repeat(321);
  oversized.sceneBlueprint.scene.visualStylePrompt = "v".repeat(121);
  const accepted = acceptanceFixture();
  const firstCandidate = JSON.stringify(oversized);
  const { provider, requests } = fakeProvider([firstCandidate, JSON.stringify(accepted)]);
  const result = await generatePrototype(
    { prompt: "Create a reusable connected prototype" },
    provider,
    { acceptanceProfile: acceptanceProfile() },
  );
  assert.equal(result.ok, true);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].diagnostics, [
    {
      code: "PROTOTYPE_ACCEPTANCE_ENVIRONMENT_PROMPT_LENGTH",
      path: "/sceneBlueprint/scene/environmentPrompt",
    },
    {
      code: "PROTOTYPE_ACCEPTANCE_VISUAL_STYLE_PROMPT_LENGTH",
      path: "/sceneBlueprint/scene/visualStylePrompt",
    },
  ]);
  assert.equal("prompt" in requests[1], false);
});

test("profile repair remains capped at three total requests", async () => {
  const rejectedCandidate = JSON.stringify(acceptanceFixture({ cycle: false }));
  const { provider, requests } = fakeProvider([rejectedCandidate]);
  const result = await generatePrototype(
    { prompt: "Create a connected prototype" },
    provider,
    { acceptanceProfile: acceptanceProfile() },
  );
  assert.equal(result.ok, false);
  assert.equal(requests.length, 3);
  assert.deepEqual(result.diagnostics.map(({ code, path }) => ({ code, path })), [
    {
      code: "PROTOTYPE_ACCEPTANCE_REACHABLE_CYCLE_REQUIRED",
      path: "/authoringGamePack/nodes",
    },
  ]);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.diagnostics), true);
});

test("acceptance graph and binding diagnostics are generic and deterministic", () => {
  const unreachable = acceptanceFixture();
  unreachable.authoringGamePack.nodes[1].actions[1].target.id = "ending-complete";
  const preparedUnreachable = prepareGenerationProposalJson(JSON.stringify(unreachable));
  assert.equal(preparedUnreachable.ok, true);
  const unreachableDiagnostics = evaluateAcceptanceProfile(
    preparedUnreachable,
    normalizeAcceptanceOptions({
      acceptanceProfile: acceptanceProfile({ requireReachableCycle: false }),
    }).profile,
  );
  assert.deepEqual(unreachableDiagnostics.map((item) => item.code), [
    "PROTOTYPE_ACCEPTANCE_ENDING_UNREACHABLE",
  ]);

  const unbound = acceptanceFixture();
  unbound.sceneBlueprint.placements = unbound.sceneBlueprint.placements.filter(
    (placement) => placement.id !== "placement-person",
  );
  unbound.sceneBlueprint.nodeBindings[1].visiblePlacementIds = ["placement-environment"];
  const preparedUnbound = prepareGenerationProposalJson(JSON.stringify(unbound));
  assert.equal(preparedUnbound.ok, true);
  const bindingDiagnostics = evaluateAcceptanceProfile(
    preparedUnbound,
    normalizeAcceptanceOptions({ acceptanceProfile: acceptanceProfile() }).profile,
  );
  assert.deepEqual(bindingDiagnostics.map((item) => item.code), [
    "PROTOTYPE_ACCEPTANCE_ASSET_BINDING_REQUIRED",
    "PROTOTYPE_ACCEPTANCE_ASSET_VISIBILITY_REQUIRED",
  ]);
  assert.equal(Object.isFrozen(bindingDiagnostics), true);
  assert.equal(Object.isFrozen(bindingDiagnostics[0]), true);

  const hidden = acceptanceFixture();
  hidden.sceneBlueprint.nodeBindings[1].visiblePlacementIds = ["placement-environment"];
  const preparedHidden = prepareGenerationProposalJson(JSON.stringify(hidden));
  assert.equal(preparedHidden.ok, true);
  assert.deepEqual(
    evaluateAcceptanceProfile(
      preparedHidden,
      normalizeAcceptanceOptions({ acceptanceProfile: acceptanceProfile() }).profile,
    ).map(({ code, path }) => ({ code, path })),
    [{
      code: "PROTOTYPE_ACCEPTANCE_ASSET_VISIBILITY_REQUIRED",
      path: "/sceneBlueprint/nodeBindings",
    }],
  );
});

test("acceptance executes conditions and effects instead of trusting declared target edges", () => {
  const proposal = acceptanceFixture();
  proposal.authoringGamePack.variables = [
    { id: "route-state", type: "integer", initial: 0 },
  ];
  proposal.authoringGamePack.nodes[0].actions[0].effects = [
    { op: "set", variableId: "route-state", value: 1 },
  ];
  proposal.authoringGamePack.nodes[1].actions = [
    {
      id: "action-declared-cycle",
      label: "Declared cycle",
      when: { op: "eq", variableId: "route-state", value: 0 },
      effects: [],
      target: { kind: "node", id: "node-start" },
    },
    {
      id: "action-declared-ending",
      label: "Declared ending",
      when: { op: "eq", variableId: "route-state", value: 0 },
      effects: [],
      target: { kind: "ending", id: "ending-second" },
    },
  ];
  const prepared = prepareGenerationProposalJson(JSON.stringify(proposal));
  assert.equal(prepared.ok, true);
  const diagnostics = evaluateAcceptanceProfile(
    prepared,
    normalizeAcceptanceOptions({ acceptanceProfile: acceptanceProfile() }).profile,
  );
  assert.deepEqual(
    diagnostics.map(({ code, path }) => ({ code, path })),
    [
      {
        code: "PROTOTYPE_ACCEPTANCE_REACHABLE_CYCLE_REQUIRED",
        path: "/authoringGamePack/nodes",
      },
      {
        code: "PROTOTYPE_ACCEPTANCE_ENDING_UNREACHABLE",
        path: "/authoringGamePack/endings",
      },
      {
        code: "PROTOTYPE_ACCEPTANCE_ACTIVE_DEADLOCK",
        path: "/authoringGamePack/nodes",
      },
    ],
  );
});

test("acceptance count ranges produce the six stable count diagnostics", () => {
  const prepared = prepareGenerationProposalJson(JSON.stringify(acceptanceFixture()));
  assert.equal(prepared.ok, true);
  const profile = normalizeAcceptanceOptions({
    acceptanceProfile: acceptanceProfile({
      nodes: { min: 3, max: 3 },
      endings: { min: 3, max: 3 },
      actions: { min: 5, max: 5 },
      zones: { min: 3, max: 3 },
      props: { min: 2, max: 2 },
      characterPlaceholders: { min: 2, max: 2 },
    }),
  }).profile;
  const first = evaluateAcceptanceProfile(prepared, profile);
  const second = evaluateAcceptanceProfile(prepared, profile);
  assert.deepEqual(first.map(({ code, path }) => ({ code, path })), [
    { code: "PROTOTYPE_ACCEPTANCE_NODE_COUNT", path: "/authoringGamePack/nodes" },
    { code: "PROTOTYPE_ACCEPTANCE_ENDING_COUNT", path: "/authoringGamePack/endings" },
    { code: "PROTOTYPE_ACCEPTANCE_ACTION_COUNT", path: "/authoringGamePack/nodes" },
    { code: "PROTOTYPE_ACCEPTANCE_ZONE_COUNT", path: "/sceneBlueprint/zones" },
    { code: "PROTOTYPE_ACCEPTANCE_PROP_COUNT", path: "/sceneBlueprint/assetBriefs" },
    {
      code: "PROTOTYPE_ACCEPTANCE_CHARACTER_COUNT",
      path: "/sceneBlueprint/assetBriefs",
    },
  ]);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
});

test("invalid acceptance profiles fail before Provider access", async () => {
  for (const options of [
    {},
    { acceptanceProfile: { ...acceptanceProfile(), unexpected: true } },
    {
      acceptanceProfile: {
        ...acceptanceProfile(),
        nodes: { min: 3, max: 2 },
      },
    },
  ]) {
    const { provider, requests } = fakeProvider([JSON.stringify(acceptanceFixture())]);
    const result = await generatePrototype({ prompt: "neutral" }, provider, options);
    assert.deepEqual(result, {
      ok: false,
      diagnostics: [
        {
          phase: "semantic",
          severity: "error",
          code: "PROTOTYPE_ACCEPTANCE_PROFILE_INVALID",
          path: "/acceptanceProfile",
          message: "PROTOTYPE_ACCEPTANCE_PROFILE_INVALID",
        },
      ],
    });
    assert.equal(requests.length, 0);
  }
});

test("three invalid candidates exhaust repairs and publish no artifacts", async () => {
  const { provider, requests } = fakeProvider(["{}", "{}", "{}", JSON.stringify(proposalFixture())]);
  const result = await generatePrototype({ prompt: "neutral request" }, provider);
  assert.equal(result.ok, false);
  assert.equal(requests.length, 3);
  assert.deepEqual(Reflect.ownKeys(result), ["ok", "diagnostics"]);
  assert.equal(result.diagnostics.length > 0, true);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(JSON.stringify(result).includes("neutral request"), false);
});

test("invalid request is static and does not call the provider", async () => {
  const { provider, requests } = fakeProvider([JSON.stringify(proposalFixture())]);
  const result = await generatePrototype({ prompt: "" }, provider);
  assert.deepEqual(result, {
    ok: false,
    diagnostics: [
      {
        phase: "semantic",
        severity: "error",
        code: "PROTOTYPE_REQUEST_TEXT_INVALID",
        path: "/prompt",
        message: "PROTOTYPE_REQUEST_TEXT_INVALID",
      },
    ],
  });
  assert.equal(requests.length, 0);
});

test("provider, compiler, Runtime prepare, Runtime create, and crypto faults are operational", async () => {
  const valid = JSON.stringify(proposalFixture());
  const sentinel = ["dynamic", "fault", Date.now()].join("-");
  const faultProvider = Object.freeze({
    kind: "fake",
    model: "fake-neutral-model",
    async requestProposal() {
      throw new Error(sentinel);
    },
  });
  await assert.rejects(
    generatePrototype({ prompt: "neutral" }, faultProvider),
    (error) => assertOperational(error, sentinel),
  );

  const faults = [
    { compileAuthoringGamePackJson: async () => { throw new Error(sentinel); } },
    { prepareRuntimeGamePackJson: async () => ({ ok: false, validationReport: {} }) },
    { createRuntimeGameSession: () => ({ ok: false, diagnostics: [] }) },
    { digest: async () => { throw new Error(sentinel); } },
  ];
  for (const override of faults) {
    const { provider } = fakeProvider([valid]);
    await assert.rejects(
      generatePrototypeWithServices(
        { prompt: "neutral" },
        provider,
        services(override),
      ),
      (error) => assertOperational(error, sentinel),
    );
  }
});

test("same fake Provider result is byte-identical twenty times", async () => {
  const valid = JSON.stringify(proposalFixture());
  const results = await Promise.all(
    Array.from({ length: 20 }, async () => {
      const { provider } = fakeProvider([valid]);
      return generatePrototype({ prompt: "deterministic neutral prototype" }, provider);
    }),
  );
  assert.equal(results.every((item) => item.ok), true);
  assert.equal(new Set(results.map((item) => JSON.stringify(item))).size, 1);
});

test("generation does not mutate request, candidate, or Provider output", async () => {
  const request = { prompt: "neutral immutable request" };
  const proposal = proposalFixture();
  const candidate = JSON.stringify(proposal);
  const { provider } = fakeProvider([candidate]);
  const beforeRequest = structuredClone(request);
  const result = await generatePrototype(request, provider);
  assert.equal(result.ok, true);
  assert.deepEqual(request, beforeRequest);
  assert.equal(candidate, JSON.stringify(proposal));
});

test("generator source imports only public package roots and contains no topic branch", async () => {
  const source = await import("node:fs/promises").then(async ({ readFile }) =>
    [
      await readFile(new URL("../src/generator.mjs", import.meta.url), "utf8"),
      await readFile(new URL("../src/acceptance-profile.mjs", import.meta.url), "utf8"),
    ].join("\n"),
  );
  assert.equal(source.includes("examples/"), false);
  assert.equal(source.includes("last-train"), false);
  assert.equal(source.includes("mechanics-conformance"), false);
  assert.equal(/from\s+["'][^"']+\/src\//.test(source), false);
  assert.equal(source.includes(["process", "env"].join(".")), false);
});

test("package root exposes only the frozen R8 generator surface", async () => {
  const publicApi = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(publicApi).sort(), [
    "PrototypeGeneratorOperationalError",
    "createOpenAICompatibleProvider",
    "generatePrototype",
  ]);
  assert.equal("generatePrototypeWithServices" in publicApi, false);
});
