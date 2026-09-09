import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import { CARD_PACKAGE_SCHEMA, validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { compileVerifiedContent } from "./runtime-fixtures.mjs";
import { HOST_TEMPLATE } from "../context/host-template.mjs";
const bytes = fs.readFileSync(new URL("../fixtures/rpg04/authored-resources.json", import.meta.url));
const authored = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
const sha = createHash("sha256").update(bytes).digest("hex");
const keys = ["styles", "openings", "worldbookEntries", "informationModules"];
const schema = { type: "object", additionalProperties: false, required: ["format", "formatVersion", "resources", "defaults"], properties: {
  format: { const: "modelmirror.ai-rpg.rpg04-authored-resources" }, formatVersion: { const: "0.1.0" },
  resources: { type: "object", additionalProperties: false, required: keys, properties: Object.fromEntries(keys.map(k => [k, CARD_PACKAGE_SCHEMA.properties.resources.properties[k]])) },
  defaults: CARD_PACKAGE_SCHEMA.properties.defaults
} };
const check = new Ajv2020({ strict: true }).compile(schema);
function merge(data = authored) {
  const baseline = compileVerifiedContent();
  const card = structuredClone(baseline.cardPackage);
  card.package = { ...card.package, id: "card.rpg04-representative", version: "0.1.0" };
  card.provenance.rights.push({ id: "rights.authored-rpg04", kind: "authorization", name: "RPG-04 authorized authored content", reference: "docs/ai-rpg-experiment/RPG04_PLAN.md" });
  card.provenance.sources.push({ id: "source.authored-rpg04", kind: "authored", reference: "fixtures/rpg04/authored-resources.json", sha256: sha, rightsRefs: ["rights.authored-rpg04"] });
  for (const key of keys) card.resources[key].push(...structuredClone(data.resources[key]));
  card.defaults = structuredClone(data.defaults);
  return { card, baseline };
}
test("authored supplement reuses closed resource schemas and merges into a valid zero-plugin card", () => {
  assert.equal(check(authored), true, JSON.stringify(check.errors)); const { card } = merge();
  assert.equal(validateCardPackage(card).valid, true); assert.deepEqual(card.requiredPlugins, []); assert.deepEqual(card.recommendedPlugins, []);
});
test("old selected resources and provenance remain unchanged after supplement merge", () => {
  const { card, baseline } = merge();
  for (const [key, entries] of Object.entries(baseline.cardPackage.resources)) assert.deepEqual(card.resources[key].slice(0, entries.length), entries);
  assert.deepEqual(card.provenance.sources.slice(0, baseline.cardPackage.provenance.sources.length), baseline.cardPackage.provenance.sources);
  for (const resources of Object.values(authored.resources)) for (const resource of resources) assert.deepEqual(resource.sourceRefs, ["source.authored-rpg04"]);
  assert.equal(card.provenance.sources.at(-1).sha256, sha);
});
test("both new openings use only their own world identity and talent scopes without auto-granting kits", () => {
  const { card } = merge(); assert.equal(authored.resources.openings.length, 2);
  for (const opening of authored.resources.openings) {
    assert.deepEqual(opening.itemRefs, []);
    for (const id of opening.identityRefs) assert.equal(card.resources.identities.find(x => x.id === id).worldRefs.includes(opening.worldRef), true);
    for (const id of opening.talentRefs) { const scope = card.resources.talents.find(x => x.id === id).worldRefs; assert.equal(scope.length === 0 || scope.includes(opening.worldRef), true); }
    for (const id of opening.worldbookRefs) { const lore = card.resources.worldbookEntries.find(x => x.id === id); assert.equal(lore.visibility !== "host" && lore.worldRefs.includes(opening.worldRef), true); }
  }
});
test("two private lore fixtures remain explicit and are absent from public opening text and refs", () => {
  const hidden = authored.resources.worldbookEntries.filter(x => x.visibility === "host"); assert.equal(hidden.length, 2);
  for (const opening of authored.resources.openings) for (const entry of hidden) { assert.equal(opening.worldbookRefs.includes(entry.id), false); assert.equal(opening.content.includes(entry.content), false); }
});
test("full virtual player retains five talents and empty runtime permissions with new package binding", () => {
  const { card, baseline } = merge(); const player = structuredClone(baseline.playerSetup);
  player.cardPackageRef = { id: card.package.id, version: card.package.version };
  assert.equal(validatePlayerSetup(player, card).valid, true); assert.equal(player.talents.length, 5); assert.deepEqual(player.runtimePermissions, []);
  assert.deepEqual(player.talents, baseline.playerSetup.talents); assert.deepEqual(player.persona, baseline.playerSetup.persona);
});
test("closed envelope rejects execution-shaped fields and merged validation rejects dangling or duplicate resources", () => {
  for (const key of ["script", "rawHtml", "endpoint"]) { const bad = structuredClone(authored); bad[key] = "untrusted"; assert.equal(check(bad), false); }
  const missing = structuredClone(authored); missing.resources.openings[0].worldbookRefs.push("worldbook.missing"); assert.equal(validateCardPackage(merge(missing).card).valid, false);
  const duplicate = structuredClone(authored); duplicate.resources.styles.push(structuredClone(duplicate.resources.styles[0])); assert.equal(validateCardPackage(merge(duplicate).card).valid, false);
});
test("host template is immutable authored text with explicit contract guidance, not a behavioral acceptance", () => {
  assert.equal(Object.isFrozen(HOST_TEMPLATE), true); assert.equal(HOST_TEMPLATE.version, "0.1.0");
  for (const name of ["narrative", "suggestedActions", "informationModules", "stateProposals", "uncertainties", "input.kind", "session.state"]) assert.equal(HOST_TEMPLATE.content.includes(name), true);
  assert.equal(HOST_TEMPLATE.content.includes("query时此数组必须为空"), true);
  assert.throws(() => { HOST_TEMPLATE.content = "changed"; }, TypeError);
});
test("validation and merge preserve frozen inputs and byte hash identifies the exact authored file", () => {
  const before = JSON.stringify(authored); const freeze = x => { if(x && typeof x === "object") { Object.values(x).forEach(freeze); Object.freeze(x); } return x; };
  freeze(authored); assert.equal(check(authored), true); merge(); assert.equal(JSON.stringify(authored), before);
  assert.notEqual(createHash("sha256").update(Buffer.concat([bytes, Buffer.from(" ")])).digest("hex"), sha);
});
