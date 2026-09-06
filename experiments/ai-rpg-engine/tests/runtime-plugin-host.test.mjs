import test from "node:test";
import assert from "node:assert/strict";
import { createPluginHost } from "../runtime/plugin-host.mjs";
import { RUNTIME_FORMATS, RUNTIME_FORMAT_VERSION } from "../runtime/contracts.mjs";
import { baseRuntimeFixture, sha256 } from "./runtime-fixtures.mjs";

const clone = (value) => structuredClone(value);
function manifest(id = "plugin.context-basic", artifact = "b".repeat(64)) {
  return {
    format: "modelmirror.ai-rpg.plugin-manifest", formatVersion: "0.1.0",
    plugin: { id, version: "1.0.0", displayName: "Plugin", description: "Fixture plugin." },
    compatibleHostContractVersions: ["0.1.0"], capabilities: ["context.enrich"],
    permissions: ["card.read", "turn.read"],
    settings: [{ key: "context.limit", label: "Limit", description: "Limit.", valueType: "integer", required: false, minimum: 1, maximum: 32 }],
    dependencies: [], dataAccess: { read: ["card", "turnInput", "sessionMetadata"], propose: ["context"] },
    network: { mode: "none" }, lifecycle: { activation: "explicit", deactivation: "supported", failurePolicy: "isolated", uninstallData: "retain" },
    provenance: { sourceReference: "fixture", sourceSha256: "a".repeat(64), licenseName: "MIT", licenseReference: "fixture", artifactSha256: artifact },
  };
}
function authorization(fixture, item, revision = 0, overrides = {}) {
  return {
    format: RUNTIME_FORMATS.pluginAuthorization, formatVersion: RUNTIME_FORMAT_VERSION,
    sessionId: fixture.session.sessionId, cardPackageSha256: fixture.session.resources.cardPackage.sha256,
    playerSetupSha256: fixture.session.resources.playerSetup.sha256, revision, evidenceKind: "mock", action: "authorize",
    pluginId: item.plugin.id, version: item.plugin.version, manifestSha256: sha256(JSON.stringify(sortJson(item))), artifactSha256: item.provenance.artifactSha256,
    permissions: [...item.permissions], read: [...item.dataAccess.read], propose: [...item.dataAccess.propose], settings: [{ key: "context.limit", value: 4 }], ...overrides,
  };
}
function sortJson(value) {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortJson(value[key])]));
  return value;
}
function register(host, item, adapter = { async invoke() { return { proposals: [] }; } }) {
  return host.register({ manifest: item, manifestSha256: sha256(JSON.stringify(sortJson(item))), artifactSha256: item.provenance.artifactSha256, adapter });
}
function setup(adapter) {
  const fixture = baseRuntimeFixture(), item = manifest(), created = createPluginHost({ hash: sha256 });
  assert.equal(created.valid, true); const host = created.value;
  assert.equal(register(host, item, adapter).valid, true);
  const grant = authorization(fixture, item); fixture.session.pluginAuthorizations.push(clone(grant));
  assert.equal(host.enable(grant).valid, true);
  return { ...fixture, item, grant, host };
}

test("constructor and registration fail closed on hashes, duplicate ids, manifests, and adapter authority", () => {
  assert.equal(createPluginHost({ hash: async () => "a".repeat(64) }).valid, false);
  const host = createPluginHost({ hash: sha256 }).value, item = manifest();
  assert.equal(host.register({ manifest: item, manifestSha256: "0".repeat(64), artifactSha256: item.provenance.artifactSha256, adapter: { invoke() {} } }).valid, false);
  assert.equal(register(host, item, { invoke() {} }).valid, true);
  assert.equal(register(host, item, { invoke() {} }).valid, false);
  const root = manifest("plugin.root"); root.permissions.push("system.root");
  assert.equal(register(createPluginHost({ hash: sha256 }).value, root, { invoke() {} }).valid, false);
  assert.equal(register(createPluginHost({ hash: sha256 }).value, manifest("plugin.no-adapter"), {}).valid, false);
});

test("coercible hashes and malformed resource envelopes return diagnostics without throwing", async () => {
  const host = createPluginHost({ hash: sha256 }).value, item = manifest();
  assert.equal(host.register({ manifest: item, manifestSha256: [sha256(JSON.stringify(sortJson(item)))], artifactSha256: [item.provenance.artifactSha256], adapter: { invoke() {} } }).valid, false);
  const fixture = baseRuntimeFixture(); fixture.session.resources.cardPackage.sha256 = sha256("null");
  assert.equal(host.readiness(null, fixture.session).ready, false);
  for (const pluginId of [null, [], { toString: "private" }]) assert.equal((await host.invoke({ pluginId, capability: "context.enrich", session: fixture.session, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })).valid, false);
});

test("authorization enforces version, subsets, permission mappings, settings, and empty revoke", () => {
  const fixture = baseRuntimeFixture(), item = manifest(), host = createPluginHost({ hash: sha256 }).value;
  register(host, item, { invoke() {} }); const base = authorization(fixture, item);
  assert.equal(host.checkAuthorization(base).valid, true);
  for (const bad of [
    { ...base, version: "2.0.0" },
    { ...base, permissions: ["model.request"] },
    { ...base, permissions: [], read: ["card"] },
    { ...base, settings: [{ key: "context.limit", value: 99 }] },
    { ...base, settings: [{ key: "unknown", value: 1 }] },
    { ...base, action: "revoke" },
  ]) assert.equal(host.checkAuthorization(bad).valid, false);
  assert.equal(host.checkAuthorization({ ...base, action: "revoke", permissions: [], read: [], propose: [], settings: [] }).valid, true);
});

test("zero-plugin, required, and all recommended fallbacks use only enabled latest grants", () => {
  const fixture = baseRuntimeFixture(), item = manifest(), host = createPluginHost({ hash: sha256 }).value;
  assert.equal(host.readiness(fixture.cardPackage, fixture.session).ready, true);
  fixture.cardPackage.requiredPlugins = [{ pluginId: item.plugin.id, version: item.plugin.version, capabilities: ["context.enrich"] }];
  fixture.session.resources.cardPackage.sha256 = sha256(JSON.stringify(sortJson(fixture.cardPackage)));
  assert.equal(host.readiness(fixture.cardPackage, fixture.session).ready, false);
  register(host, item); assert.equal(host.readiness(fixture.cardPackage, fixture.session).ready, false);
  const grant = authorization(fixture, item); fixture.session.pluginAuthorizations.push(grant); host.enable(grant);
  assert.equal(host.readiness(fixture.cardPackage, fixture.session).ready, true);
  host.disable({ sessionId: fixture.session.sessionId, pluginId: item.plugin.id });
  assert.equal(host.readiness(fixture.cardPackage, fixture.session).ready, false);
  for (const fallback of ["core", "omit", "readOnly"]) {
    const optional = baseRuntimeFixture();
    optional.cardPackage.recommendedPlugins = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: [], fallback }];
    optional.session.resources.cardPackage.sha256 = sha256(JSON.stringify(sortJson(optional.cardPackage)));
    const result = host.readiness(optional.cardPackage, optional.session); assert.equal(result.ready, true); assert.equal(result.diagnostics[0].severity, "warning");
  }
});

test("invoke projects only granted immutable data and exposes unavailable services", async () => {
  let seen;
  const ctx = setup({ async invoke(value) { seen = value; value.data.card.package.displayName = "changed"; value.settings["context.limit"] = 9; return { proposals: [{ scope: "context", content: "candidate" }] }; } });
  const before = JSON.stringify([ctx.cardPackage, ctx.playerSetup, ctx.session]);
  const result = await ctx.host.invoke({ pluginId: ctx.item.plugin.id, capability: "context.enrich", session: ctx.session, cardPackage: ctx.cardPackage, playerSetup: ctx.playerSetup, turnInput: { kind: "action", text: "look" }, turnProposal: { secret: true } });
  assert.equal(result.valid, true); assert.deepEqual(Object.keys(seen.data).sort(), ["card", "sessionMetadata", "turnInput"]);
  assert.deepEqual(seen.services, { model: { available: false }, memory: { available: false }, network: { available: false }, ui: { available: false } });
  assert.equal(Object.isFrozen(seen.services), true); assert.equal(JSON.stringify([ctx.cardPackage, ctx.playerSetup, ctx.session]), before);
  assert.equal(result.value.authorizationSha256, sha256(JSON.stringify(sortJson(ctx.grant))));
  assert.equal(ctx.host.validateResult(result.value, ctx.session).valid, true);
});

test("result binding becomes stale after revision, revoke, disable, or re-enable epoch", async () => {
  const ctx = setup({ async invoke() { return { proposals: [{ scope: "context", content: "x" }] }; } });
  const result = (await ctx.host.invoke({ pluginId: ctx.item.plugin.id, capability: "context.enrich", session: ctx.session, cardPackage: ctx.cardPackage, playerSetup: ctx.playerSetup })).value;
  const revised = clone(ctx.session); revised.revision = 1;
  assert.equal(ctx.host.validateResult(result, revised).valid, false);
  ctx.host.disable({ sessionId: ctx.session.sessionId, pluginId: ctx.item.plugin.id });
  assert.equal(ctx.host.validateResult(result, ctx.session).valid, false);
  ctx.host.enable(ctx.grant); assert.equal(ctx.host.validateResult(result, ctx.session).valid, false);
  const revoked = clone(ctx.session); revoked.revision = 1; revoked.pluginAuthorizations.push({ ...ctx.grant, revision: 1, action: "revoke", permissions: [], read: [], propose: [], settings: [] });
  assert.equal(ctx.host.validateResult(result, revoked).valid, false);
});

test("dependencies require explicit compatible enablement and disabling them invalidates dependents", async () => {
  const fixture = baseRuntimeFixture(), dep = manifest("plugin.dep"), root = manifest("plugin.root");
  root.dependencies = [{ pluginId: dep.plugin.id, version: dep.plugin.version, capabilities: ["context.enrich"] }];
  const host = createPluginHost({ hash: sha256 }).value; register(host, dep); register(host, root);
  const depGrant = authorization(fixture, dep), rootGrant = authorization(fixture, root);
  fixture.session.pluginAuthorizations.push(depGrant, rootGrant);
  assert.equal(host.enable(rootGrant).valid, false); assert.equal(host.enable(depGrant).valid, true); assert.equal(host.enable(rootGrant).valid, true);
  host.disable({ sessionId: fixture.session.sessionId, pluginId: dep.plugin.id });
  assert.equal((await host.invoke({ pluginId: root.plugin.id, capability: "context.enrich", session: fixture.session, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })).valid, false);
});

test("busy, timeout, exception, disable-late, and malicious output fail without late acceptance", async () => {
  let release; const gate = new Promise((resolve) => { release = resolve; });
  const ctx = setup({ async invoke() { await gate; return { proposals: [{ scope: "context", content: "late" }] }; } });
  const input = { pluginId: ctx.item.plugin.id, capability: "context.enrich", session: ctx.session, cardPackage: ctx.cardPackage, playerSetup: ctx.playerSetup, timeoutMs: 100 };
  const first = ctx.host.invoke(input); assert.equal((await ctx.host.invoke(input)).valid, false);
  ctx.host.disable({ sessionId: ctx.session.sessionId, pluginId: ctx.item.plugin.id }); release(); assert.equal((await first).valid, false);
  for (const adapter of [
    { async invoke() { throw new Error("private path C:/secret"); } },
    { async invoke() { return { proposals: [{ scope: "state", content: "unauthorized" }] }; } },
    { async invoke() { return { proposals: [{ scope: "context", content: "x", extra: true }] }; } },
  ]) {
    const one = setup(adapter), outcome = await one.host.invoke({ pluginId: one.item.plugin.id, capability: "context.enrich", session: one.session, cardPackage: one.cardPackage, playerSetup: one.playerSetup });
    assert.equal(outcome.valid, false); assert.equal(JSON.stringify(outcome).includes("secret"), false);
  }
  const slow = setup({ async invoke() { await new Promise(() => {}); } });
  assert.equal((await slow.host.invoke({ pluginId: slow.item.plugin.id, capability: "context.enrich", session: slow.session, cardPackage: slow.cardPackage, playerSetup: slow.playerSetup, timeoutMs: 5 })).valid, false);
});

test("input getters, cycles, sparse arrays, invalid timeout, and input mutation are rejected or isolated", async () => {
  const ctx = setup({ async invoke() { return { proposals: [] }; } }), cyclic = {}; cyclic.self = cyclic;
  assert.equal((await ctx.host.invoke(cyclic)).valid, false);
  assert.equal((await ctx.host.invoke({ pluginId: ctx.item.plugin.id, capability: "context.enrich", session: ctx.session, cardPackage: ctx.cardPackage, playerSetup: ctx.playerSetup, timeoutMs: 5001 })).valid, false);
  const sparse = []; sparse.length = 3;
  assert.equal(ctx.host.register({ manifest: sparse, manifestSha256: "a".repeat(64), artifactSha256: "b".repeat(64), adapter: { invoke() {} } }).valid, false);
  const evil = {}; Object.defineProperty(evil, "manifest", { get() { throw new Error("secret"); } });
  assert.equal(ctx.host.register(evil).valid, false);
});
