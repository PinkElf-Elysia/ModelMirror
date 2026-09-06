import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { compileVerifiedContent as compileVerifiedSource } from "../tooling/source-input.mjs";
import { RUNTIME_FORMATS, RUNTIME_FORMAT_VERSION, canonicalJson } from "../runtime/index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const readJson = (relative) => JSON.parse(fs.readFileSync(path.join(root, relative), "utf8"));
export const sha256 = (value) => createHash("sha256").update(value).digest("hex");

export function compileVerifiedContent() {
  const input = readJson("fixtures/rpg02/compile-input.json"), before = structuredClone(input);
  const source = fs.readFileSync(path.join(root, "fixtures", "rpg02", "selected-source.txt"), "utf8");
  const selectionText = fs.readFileSync(path.join(root, "fixtures", "rpg02", "source-selection.json"), "utf8");
  const captureText = fs.readFileSync(path.join(root, "fixtures", "rpg02", "source-capture.json"), "utf8");
  const playerText = fs.readFileSync(path.join(root, "fixtures", "rpg02", "player-text.txt"), "utf8");
  const playerConfig = readJson("fixtures/rpg02/player-config.json");
  input.player = { text: playerText, ...playerConfig };
  const compiledInput = structuredClone(input);
  const result = compileVerifiedSource(input, { htmlText: source, selectionText, captureText });
  const compiled = result.value?.compiled;
  if (!result.valid || !compiled || !validateCardPackage(compiled.cardPackage).valid || !compiled.playerSetup || !validatePlayerSetup(compiled.playerSetup, compiled.cardPackage).valid) throw new Error("RUNTIME_FIXTURE_COMPILE");
  if (JSON.stringify(input) !== JSON.stringify(compiledInput) || Object.hasOwn(before, "player")) throw new Error("RUNTIME_FIXTURE_MUTATION");
  return structuredClone(compiled);
}

export function baseRuntimeFixture() {
  const cardPackage = readJson("fixtures/zero-plugin.card-package.json"), playerSetup = readJson("fixtures/bai-yu-ling-yin.player-setup.json");
  const cardCanonical = canonicalJson(cardPackage), playerCanonical = canonicalJson(playerSetup);
  if (!cardCanonical.valid || !playerCanonical.valid) throw new Error("RUNTIME_FIXTURE_CANONICAL");
  const resources = { cardPackage: { id: cardPackage.package.id, version: cardPackage.package.version, sha256: sha256(cardCanonical.value) }, playerSetup: { setupId: playerSetup.setupId, sha256: sha256(playerCanonical.value) } };
  const session = { format: RUNTIME_FORMATS.session, formatVersion: RUNTIME_FORMAT_VERSION, sessionId: "session.fixture", resources, revision: 0, state: cardPackage.stateFields.map((field) => ({ fieldRef: field.id, value: field.initialValue })), turns: [], generations: [], pending: null, pluginAuthorizations: [] };
  return { cardPackage, playerSetup, session };
}
