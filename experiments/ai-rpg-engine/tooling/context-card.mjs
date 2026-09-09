import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import { CARD_PACKAGE_SCHEMA, validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { canonicalJson } from "../runtime/index.mjs";
import { HOST_TEMPLATE } from "../context/host-template.mjs";
import { compileVerifiedContent, parseStrictJson } from "./source-input.mjs";

export const RPG04_AUTHORED_SOURCE_SHA256 = "b395447f2cb1ce80763fb470b49b5801df110cfc8462d16e09678f9b8d4f8692";
export const RPG04_HOST_TEMPLATE_SHA256 = "07a4b6987abde92afec3f43ec13fe18d2cf4d7a58dc4d819b0c73918641752bb";
export const RPG02_BASELINE_CARD_SHA256 = "2d3886a4f3f14bd059eefa471612c86937b93e8b74a6e114207ba1b942ff9dc9";
export const RPG02_BASELINE_PLAYER_SHA256 = "21f0c5633575a934d061805b90c56adfced32ab222e8b5fcbb7395774351d9d0";

const RESOURCE_KEYS = ["styles", "openings", "worldbookEntries", "informationModules"];
const authoredSchema = {
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "resources", "defaults"],
  properties: {
    format: { const: "modelmirror.ai-rpg.rpg04-authored-resources" },
    formatVersion: { const: "0.1.0" },
    resources: {
      type: "object",
      additionalProperties: false,
      required: RESOURCE_KEYS,
      properties: Object.fromEntries(RESOURCE_KEYS.map((key) => [key, CARD_PACKAGE_SCHEMA.properties.resources.properties[key]])),
    },
    defaults: CARD_PACKAGE_SCHEMA.properties.defaults,
  },
};
const validateAuthored = new Ajv2020({ strict: true, allErrors: true }).compile(authoredSchema);
const digest = (value) => createHash("sha256").update(value).digest("hex");
const diagnostic = (code, path = "") => Object.freeze({ phase: "provenance", severity: "error", code, path });
const fail = (code, path = "") => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code, path)]) });
const succeed = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: structuredClone(value) });

function canonicalHash(value, failureCode) {
  const report = canonicalJson(value);
  return report.valid ? { valid: true, value: digest(Buffer.from(report.value, "utf8")) } : { valid: false, code: failureCode };
}

function build(baselineCompiled, authoredText, expectedSourceSha256) {
  if (!baselineCompiled || typeof baselineCompiled !== "object") return fail("RPG04_BASELINE_COMPILED_INVALID", "/baselineCompiled");
  if (typeof authoredText !== "string") return fail("RPG04_AUTHORED_SOURCE_TEXT_INVALID", "/authoredText");
  const authoredSourceSha256 = digest(Buffer.from(authoredText, "utf8"));
  if (authoredSourceSha256 !== expectedSourceSha256) return fail("RPG04_AUTHORED_SOURCE_HASH_MISMATCH", "/authoredText");
  const parsed = parseStrictJson(authoredText);
  if (!parsed.valid) return fail("RPG04_AUTHORED_SOURCE_JSON_INVALID", "/authoredText");
  if (!validateAuthored(parsed.value)) return fail("RPG04_AUTHORED_ENVELOPE_INVALID", "/authoredText");

  const baselineCardReport = validateCardPackage(baselineCompiled.cardPackage);
  if (!baselineCardReport.valid) return fail("RPG04_BASELINE_CARD_INVALID", "/baselineCompiled/cardPackage");
  const baselinePlayerReport = validatePlayerSetup(baselineCompiled.playerSetup, baselineCompiled.cardPackage);
  if (!baselinePlayerReport.valid) return fail("RPG04_BASELINE_PLAYER_INVALID", "/baselineCompiled/playerSetup");
  const baselineCardHash = canonicalHash(baselineCompiled.cardPackage, "RPG04_BASELINE_CARD_CANONICAL_INVALID");
  if (!baselineCardHash.valid || baselineCardHash.value !== RPG02_BASELINE_CARD_SHA256) return fail("RPG04_BASELINE_CARD_HASH_MISMATCH", "/baselineCompiled/cardPackage");
  const baselinePlayerHash = canonicalHash(baselineCompiled.playerSetup, "RPG04_BASELINE_PLAYER_CANONICAL_INVALID");
  if (!baselinePlayerHash.valid || baselinePlayerHash.value !== RPG02_BASELINE_PLAYER_SHA256) return fail("RPG04_BASELINE_PLAYER_HASH_MISMATCH", "/baselineCompiled/playerSetup");

  const cardPackage = structuredClone(baselineCompiled.cardPackage);
  cardPackage.package = { ...cardPackage.package, id: "card.rpg04-representative", version: "0.1.0" };
  cardPackage.provenance.rights.push({
    id: "rights.authored-rpg04",
    kind: "authorization",
    name: "RPG-04 authorized authored content",
    reference: "docs/ai-rpg-experiment/RPG04_PLAN.md",
  });
  cardPackage.provenance.sources.push({
    id: "source.authored-rpg04",
    kind: "authored",
    reference: "fixtures/rpg04/authored-resources.json",
    sha256: authoredSourceSha256,
    rightsRefs: ["rights.authored-rpg04"],
  });
  for (const key of RESOURCE_KEYS) cardPackage.resources[key].push(...structuredClone(parsed.value.resources[key]));
  cardPackage.defaults = structuredClone(parsed.value.defaults);

  const playerSetup = structuredClone(baselineCompiled.playerSetup);
  playerSetup.cardPackageRef = { id: cardPackage.package.id, version: cardPackage.package.version };
  playerSetup.opening.openingRef = cardPackage.defaults.openingRef;
  const cardReport = validateCardPackage(cardPackage);
  if (!cardReport.valid) return fail("RPG04_MERGED_CARD_INVALID", "/cardPackage");
  const playerReport = validatePlayerSetup(playerSetup, cardPackage);
  if (!playerReport.valid) return fail("RPG04_MERGED_PLAYER_INVALID", "/playerSetup");

  const hostHash = canonicalHash(HOST_TEMPLATE, "RPG04_HOST_TEMPLATE_CANONICAL_INVALID");
  if (!hostHash.valid || hostHash.value !== RPG04_HOST_TEMPLATE_SHA256) return fail(hostHash.code ?? "RPG04_HOST_TEMPLATE_HASH_MISMATCH", "/hostTemplate");
  const cardHash = canonicalHash(cardPackage, "RPG04_CARD_CANONICAL_INVALID");
  if (!cardHash.valid) return fail(cardHash.code, "/cardPackage");
  return succeed({
    cardPackage,
    playerSetup,
    provenanceReceipt: {
      format: "modelmirror.ai-rpg.rpg04-context-card-receipt",
      formatVersion: "0.1.0",
      cardPackageRef: structuredClone(playerSetup.cardPackageRef),
      authoredSource: { reference: "fixtures/rpg04/authored-resources.json", sha256: authoredSourceSha256, hashConvention: "exact file bytes" },
      cardPackageSha256: cardHash.value,
      cardHashConvention: "SHA-256 UTF-8 canonicalJson(cardPackage)",
      hostTemplateRef: { id: HOST_TEMPLATE.id, version: HOST_TEMPLATE.version },
      hostTemplateSha256: hostHash.value,
      hostHashConvention: "SHA-256 UTF-8 canonicalJson(HOST_TEMPLATE)",
    },
  });
}

export function buildRpg04ContextCard(baselineCompiled, authoredText) {
  try { return build(baselineCompiled, authoredText, RPG04_AUTHORED_SOURCE_SHA256); }
  catch { return fail("RPG04_CONTEXT_CARD_BUILD_FAILED"); }
}

export function validateRpg04ContextCardSource(baselineCompiled, authoredText) {
  try {
    if (typeof authoredText !== "string") return fail("RPG04_AUTHORED_SOURCE_TEXT_INVALID", "/authoredText");
    const report = build(baselineCompiled, authoredText, digest(Buffer.from(authoredText, "utf8")));
    return Object.freeze({ valid: report.valid, diagnostics: report.diagnostics });
  } catch {
    return fail("RPG04_CONTEXT_CARD_SOURCE_VALIDATION_FAILED");
  }
}

export function loadRpg04ContextCardFixture() {
  try {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
    const inputReport = parseStrictJson(read("fixtures/rpg02/compile-input.json"));
    const playerConfigReport = parseStrictJson(read("fixtures/rpg02/player-config.json"));
    if (!inputReport.valid || !playerConfigReport.valid) return fail("RPG04_FIXED_BASELINE_JSON_INVALID");
    const input = structuredClone(inputReport.value);
    input.player = { text: read("fixtures/rpg02/player-text.txt"), ...structuredClone(playerConfigReport.value) };
    const baseline = compileVerifiedContent(input, {
      htmlText: read("fixtures/rpg02/selected-source.txt"),
      selectionText: read("fixtures/rpg02/source-selection.json"),
      captureText: read("fixtures/rpg02/source-capture.json"),
    });
    if (!baseline.valid) return fail("RPG04_FIXED_BASELINE_VERIFICATION_FAILED");
    return buildRpg04ContextCard(baseline.value.compiled, read("fixtures/rpg04/authored-resources.json"));
  } catch {
    return fail("RPG04_FIXED_FIXTURE_READ_FAILED");
  }
}
