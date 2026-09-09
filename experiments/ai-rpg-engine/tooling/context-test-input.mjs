import { createHash } from "node:crypto";
import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { validateContextProfile } from "../context/index.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { loadRpg04ApprovedContextCardFixture } from "./context-approved-card.mjs";
import { loadRpg04ApprovedHost } from "./context-host.mjs";

const WORLDS = Object.freeze({
  gu: Object.freeze({
    sceneRef: "scene.rpg04.gu", openingRef: "opening.rpg04-approved.gu", worldRef: "world.gu",
    identityRef: "identity.gu.outer-disciple", backgroundRef: "background.gu.arrival", itemRef: "item.gu.outer-disciple-kit",
    talentRefs: Object.freeze(["talent.gu.sovereign-body", "talent.gu.perseverance", "talent.gu.spring-autumn-cicada", "talent.gu.venerable-aptitude", "talent.common.root"]),
  }),
  minecraft: Object.freeze({
    sceneRef: "scene.rpg04.minecraft", openingRef: "opening.rpg04-approved.minecraft", worldRef: "world.minecraft",
    identityRef: "identity.minecraft.first-night", backgroundRef: "background.minecraft.arrival", itemRef: "item.minecraft.first-night-kit",
    talentRefs: Object.freeze(["talent.minecraft.auto-fishing", "talent.minecraft.shaders", "talent.minecraft.steve-hand"]),
  }),
});
const TURNS = Object.freeze([
  Object.freeze({ kind: "action", text: "观察眼前环境并等待，不拿取物品，不改变任何状态。" }),
  Object.freeze({ kind: "speech", text: "我是刚到这里的旅人。请只告诉我你目前知道的情况。" }),
  Object.freeze({ kind: "query", text: "查询当前地点、可见对象与已经确定的事实，不触发购买、刷新或权限变更。" }),
]);
const sha256 = (value) => {
  const encoded = canonicalJson(value);
  if (!encoded.valid) throw new TypeError("RPG04_TEST_INPUT_CANONICAL_INVALID");
  return createHash("sha256").update(Buffer.from(encoded.value, "utf8")).digest("hex");
};

export function buildTestScenario({ world } = {}) {
  const selection = WORLDS[world];
  if (!selection) throw new TypeError("RPG04_TEST_WORLD_INVALID");
  const approved = loadRpg04ApprovedContextCardFixture(), host = loadRpg04ApprovedHost();
  if (!approved.valid || !host.valid || host.value.activation.ready !== true) throw new TypeError("RPG04_TEST_SOURCE_INVALID");
  const cardPackage = structuredClone(approved.value.cardPackage);
  const playerSetup = structuredClone(approved.value.playerSetup);
  playerSetup.setupId = `setup.rpg04-neutral.${world}`;
  playerSetup.character = { name: "中性测试旅人", appearance: "衣着普通、没有可推断身份的显著标记。", personality: "谨慎、礼貌、只依据可观察事实行动。", preferences: [] };
  playerSetup.opening = { mode: "固定中性测试开场", openingRef: selection.openingRef };
  playerSetup.world = { source: "package", resourceRef: selection.worldRef };
  playerSetup.currentIdentity = { source: "package", resourceRef: selection.identityRef };
  playerSetup.inherentBackgrounds = [{ source: "package", resourceRef: selection.backgroundRef }];
  playerSetup.possessions = [{ resource: { source: "package", resourceRef: selection.itemRef }, quantity: 1 }];
  playerSetup.talents = selection.talentRefs.map((resourceRef) => ({ resource: { source: "package", resourceRef }, owned: true, active: true }));
  playerSetup.characterPower = { status: "unspecified" };
  playerSetup.runtimePermissions = [];
  const profile = structuredClone(approved.value.contextProfile);
  profile.hostTemplate = structuredClone(host.value.binding);
  if (!validateCardPackage(cardPackage).valid || !validatePlayerSetup(playerSetup, cardPackage).valid || !validateContextProfile(profile, cardPackage).valid) throw new TypeError("RPG04_TEST_SCENARIO_INVALID");
  return Object.freeze({
    world, cardPackage, playerSetup, profile,
    hostTemplate: structuredClone(host.value.hostTemplate), hostBinding: structuredClone(host.value.binding), hostActivation: structuredClone(host.value.activation),
    sceneRef: selection.sceneRef,
    bindings: Object.freeze({ cardPackageSha256: sha256(cardPackage), playerSetupSha256: sha256(playerSetup), profileSha256: sha256(profile), hostTemplateSha256: host.value.binding.sha256 }),
  });
}

export function createTestContextInput({ scenario, session, turnIndex, modelId, maxTokens = 2048 } = {}) {
  if (!scenario || !session || !Number.isSafeInteger(turnIndex) || turnIndex < 0 || turnIndex >= TURNS.length || typeof modelId !== "string" || modelId.length < 1) throw new TypeError("RPG04_TEST_INPUT_ARGUMENT_INVALID");
  const ordinal = turnIndex + 1, suffix = `${scenario.world}.${ordinal}`;
  return {
    format: "modelmirror.ai-rpg.context-input", formatVersion: "0.1.0",
    cardPackage: structuredClone(scenario.cardPackage), playerSetup: structuredClone(scenario.playerSetup), session: structuredClone(session), profile: structuredClone(scenario.profile),
    sceneRef: scenario.sceneRef, resourceRefs: [], generationId: `generation.rpg04.${suffix}`, exchangeId: `exchange.rpg04.${suffix}`,
    expectedRevision: session.revision, input: structuredClone(TURNS[turnIndex]), modelId, settings: { temperature: 0, maxTokens },
  };
}
