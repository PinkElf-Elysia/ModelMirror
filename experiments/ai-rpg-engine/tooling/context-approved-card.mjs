import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import { CARD_PACKAGE_SCHEMA, validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { CONTEXT_PROFILE_SCHEMA, validateContextProfile } from "../context/index.mjs";
import { HOST_TEMPLATE } from "../context/host-template.mjs";
import { canonicalJson } from "../runtime/index.mjs";
import { loadRpg04ContextCardFixture } from "./context-card.mjs";
import { parseStrictJson } from "./source-input.mjs";

export const RPG04_APPROVED_CONTENT_SHA256 = "0bb3e4d3eef38094a8a7894e7530d3b7f02b31f64f24079d81b8a8a4bd81cc39";
export const RPG04_APPROVED_BASE_CARD_SHA256 = "3395cf7a0d5264748ca75a2e634470ca82bc41eafeaf0854ca3b4468a2a5e453";
export const RPG04_APPROVED_BASE_PLAYER_SHA256 = "474d5a36e3ba062f60c032b72f73b761ac52ce00dd7c58baae7541a64e56bc72";
const REFERENCE = "fixtures/rpg04/approved-content.json";
const strict = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });
const strings = { type: "array", items: { type: "string", minLength: 1 }, uniqueItems: true };
const openingBinding = strict(["baseRef", "worldbookRefs", "informationModuleRefs"], { baseRef: { type: "string", minLength: 1 }, styleRef: { type: "string", minLength: 1 }, worldbookRefs: strings, informationModuleRefs: strings });
const approvedSchema = strict(["format", "formatVersion", "resources", "openingBindings", "profileMapping", "stateFields", "approvalMapping"], {
  format: { const: "modelmirror.ai-rpg.rpg04-approved-content" }, formatVersion: { const: "0.1.0" },
  resources: strict(["styles", "worldbookEntries", "informationModules"], {
    styles: CARD_PACKAGE_SCHEMA.properties.resources.properties.styles,
    worldbookEntries: CARD_PACKAGE_SCHEMA.properties.resources.properties.worldbookEntries,
    informationModules: CARD_PACKAGE_SCHEMA.properties.resources.properties.informationModules,
  }),
  openingBindings: { type: "object", additionalProperties: openingBinding, minProperties: 1 },
  profileMapping: strict(["aliases", "loreRules"], { aliases: CONTEXT_PROFILE_SCHEMA.properties.aliases, loreRules: CONTEXT_PROFILE_SCHEMA.properties.loreRules }),
  stateFields: CARD_PACKAGE_SCHEMA.properties.stateFields,
  approvalMapping: strict(["appliedItemIds", "sourceDocument", "sourceDocumentSha256", "approvalDocument", "approvalDocumentSha256", "limitations"], {
    appliedItemIds: strings, sourceDocument: { type: "string", minLength: 1 }, sourceDocumentSha256: { type: "string", pattern: "^[a-f0-9]{64}$" }, approvalDocument: { type: "string", minLength: 1 }, approvalDocumentSha256: { type: "string", pattern: "^[a-f0-9]{64}$" }, limitations: { type: "array", items: { type: "string", minLength: 1 }, minItems: 1 },
  }),
});
const validateApprovedEnvelope = new Ajv2020({ strict: true, allErrors: true }).compile(approvedSchema);
const hash = (text) => createHash("sha256").update(text).digest("hex");
const canonicalHash = (value) => {
  const report = canonicalJson(value);
  return report.valid ? hash(Buffer.from(report.value, "utf8")) : null;
};
const fail = (code, path = "") => Object.freeze({ valid: false, diagnostics: Object.freeze([Object.freeze({ phase: "provenance", severity: "error", code, path })]) });
const ok = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: structuredClone(value) });

function build(base, text, expectedHash) {
  if (!base?.valid || typeof text !== "string") return fail("RPG04_APPROVED_INPUT_INVALID");
  const fixtureSha256 = hash(Buffer.from(text, "utf8"));
  if (fixtureSha256 !== expectedHash) return fail("RPG04_APPROVED_CONTENT_HASH_MISMATCH", "/approvedText");
  const parsed = parseStrictJson(text);
  if (!parsed.valid) return fail("RPG04_APPROVED_CONTENT_JSON_INVALID", "/approvedText");
  const fixture = parsed.value;
  if (!validateApprovedEnvelope(fixture)) return fail("RPG04_APPROVED_CONTENT_ENVELOPE_INVALID", "/approvedText");

  const previous = base.value;
  if (!validateCardPackage(previous?.cardPackage).valid || !validatePlayerSetup(previous?.playerSetup, previous?.cardPackage).valid) return fail("RPG04_APPROVED_BASE_INVALID", "/base");
  if (canonicalHash(previous.cardPackage) !== RPG04_APPROVED_BASE_CARD_SHA256) return fail("RPG04_APPROVED_BASE_CARD_HASH_MISMATCH", "/base/cardPackage");
  if (canonicalHash(previous.playerSetup) !== RPG04_APPROVED_BASE_PLAYER_SHA256) return fail("RPG04_APPROVED_BASE_PLAYER_HASH_MISMATCH", "/base/playerSetup");
  const cardPackage = structuredClone(previous.cardPackage);
  const playerSetup = structuredClone(previous.playerSetup);
  cardPackage.package = { ...cardPackage.package, id: "card.rpg04-approved", version: "0.2.0", displayName: "RPG04 已批准内容卡包" };
  cardPackage.provenance.rights.push({ id: "rights.rpg04-approved-content", kind: "authorization", name: "RPG04 approved content application", reference: "docs/RPG04_OPTIMIZATION_APPROVAL.json", documentSha256: fixture.approvalMapping.approvalDocumentSha256 });
  cardPackage.provenance.sources.push(
    { id: "source.rpg04-approved-authored", kind: "authored", reference: REFERENCE + "#approved-authored", sha256: fixtureSha256, rightsRefs: ["rights.rpg04-approved-content"] },
    { id: "source.rpg04-approved-derived", kind: "derived", reference: REFERENCE + "#approved-derived", sha256: fixtureSha256, rightsRefs: ["rights.rpg04-approved-content"] },
  );
  cardPackage.resources.styles.push(...structuredClone(fixture.resources.styles));
  cardPackage.resources.worldbookEntries.push(...structuredClone(fixture.resources.worldbookEntries));
  cardPackage.resources.informationModules.push(...structuredClone(fixture.resources.informationModules));
  cardPackage.stateFields.push(...structuredClone(fixture.stateFields));

  for (const [id, binding] of Object.entries(fixture.openingBindings)) {
    const baseOpening = cardPackage.resources.openings.find((opening) => opening.id === binding.baseRef);
    if (!baseOpening) return fail("RPG04_APPROVED_OPENING_BASE_MISSING", "/openingBindings");
    const opening = structuredClone(baseOpening);
    opening.id = id;
    opening.displayName += "（已批准内容）";
    opening.sourceRefs = [...new Set([...opening.sourceRefs, "source.rpg04-approved-derived"])];
    if (binding.styleRef) opening.styleRefs = [binding.styleRef];
    opening.worldbookRefs.push(...binding.worldbookRefs);
    opening.informationModuleRefs.push(...binding.informationModuleRefs);
    cardPackage.resources.openings.push(opening);
  }
  playerSetup.cardPackageRef = { id: cardPackage.package.id, version: cardPackage.package.version };
  playerSetup.opening.openingRef = "opening.rpg04-approved.gu";

  if (!validateCardPackage(cardPackage).valid) return fail("RPG04_APPROVED_CARD_INVALID", "/cardPackage");
  if (!validatePlayerSetup(playerSetup, cardPackage).valid) return fail("RPG04_APPROVED_PLAYER_INVALID", "/playerSetup");
  const cardPackageSha256 = canonicalHash(cardPackage);
  const hostTemplateSha256 = canonicalHash(HOST_TEMPLATE);
  if (!cardPackageSha256 || !hostTemplateSha256) return fail("RPG04_APPROVED_CANONICAL_INVALID");
  const contextProfile = {
    format: "modelmirror.ai-rpg.context-profile", formatVersion: "0.1.0",
    profile: { id: "profile.rpg04-approved", version: "0.1.0" },
    cardPackage: { id: cardPackage.package.id, version: cardPackage.package.version, sha256: cardPackageSha256 },
    hostTemplate: { id: HOST_TEMPLATE.id, version: HOST_TEMPLATE.version, sha256: hostTemplateSha256 },
    budget: { inputLimit: 65536, loreLimit: 16384, historyLimit: 32768, outputLimit: 2048 },
    scenes: [
      { id: "scene.rpg04.gu", worldRef: "world.gu", openingRef: "opening.rpg04-approved.gu", requiredResourceRefs: ["worldbook.rpg04.shared.original-gameplay", "worldbook.rpg04.shared.approved-card-guidance", "worldbook.rpg04.shared.memory-and-shop-verbatim", "worldbook.rpg04.gu.courtyard", "worldbook.rpg04.gu.steward", "worldbook.rpg04.gu.basics"] },
      { id: "scene.rpg04.minecraft", worldRef: "world.minecraft", openingRef: "opening.rpg04-approved.minecraft", requiredResourceRefs: ["worldbook.rpg04.shared.original-gameplay", "worldbook.rpg04.shared.approved-card-guidance", "worldbook.rpg04.shared.memory-and-shop-verbatim", "worldbook.rpg04.minecraft.shelter", "worldbook.rpg04.minecraft.villager", "worldbook.rpg04.minecraft.basics"] },
    ],
    aliases: structuredClone(fixture.profileMapping.aliases),
    loreRules: structuredClone(fixture.profileMapping.loreRules),
  };
  if (!validateContextProfile(contextProfile, cardPackage).valid) return fail("RPG04_APPROVED_PROFILE_INVALID", "/contextProfile");

  return ok({ cardPackage, playerSetup, contextProfile, provenanceReceipt: {
    format: "modelmirror.ai-rpg.rpg04-approved-content-receipt", formatVersion: "0.1.0",
    cardPackageRef: structuredClone(playerSetup.cardPackageRef), fixture: { reference: REFERENCE, sha256: fixtureSha256, hashConvention: "exact file bytes" },
    cardPackageSha256, contextProfileSha256: canonicalHash(contextProfile), hostTemplateSha256,
    approvalMapping: structuredClone(fixture.approvalMapping),
    sourceKinds: { "source.rpg04-approved-authored": "authored", "source.rpg04-approved-derived": "derived" },
    contextProfileUse: "comparison_only_not_dispatch_ready; host template binding is unchanged and does not claim approved host application",
    schemaDifference: "informationModules只能使用text、number、boolean或list；嵌套记录已展开为稳定字段。只有经既有接受流程提交的三个shortText memory state字段成为下一轮当前事实；超出4096字符由现有合同拒绝且不截断。",
  }});
}

export function buildRpg04ApprovedContextCard(base, approvedText) {
  try { return build(base, approvedText, RPG04_APPROVED_CONTENT_SHA256); }
  catch { return fail("RPG04_APPROVED_BUILD_FAILED"); }
}

export function validateRpg04ApprovedContent(base, approvedText) {
  try {
    const report = build(base, approvedText, hash(Buffer.from(approvedText, "utf8")));
    return Object.freeze({ valid: report.valid, diagnostics: report.diagnostics });
  }
  catch { return fail("RPG04_APPROVED_VALIDATION_FAILED"); }
}

export function loadRpg04ApprovedContextCardFixture() {
  try {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const text = fs.readFileSync(path.join(root, REFERENCE), "utf8");
    return build(loadRpg04ContextCardFixture(), text, RPG04_APPROVED_CONTENT_SHA256);
  } catch { return fail("RPG04_APPROVED_FIXTURE_READ_FAILED"); }
}
