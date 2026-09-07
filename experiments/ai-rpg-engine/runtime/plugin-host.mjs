import Ajv2020 from "ajv/dist/2020.js";
import {
  PLUGIN_MANIFEST_SCHEMA,
  evaluatePluginReadiness,
  validateCardPackage,
} from "../src/index.mjs";
import {
  canonicalJson,
  validatePluginAuthorization,
  validateRuntimeResourceBindings,
  validateRuntimeSession,
} from "./contracts.mjs";

const ajv = new Ajv2020({ allErrors: true, strict: true });
const validateManifestSchema = ajv.compile(PLUGIN_MANIFEST_SCHEMA);
const READ_PERMISSION = Object.freeze({
  card: "card.read",
  playerSetup: "player.read",
  turnInput: "turn.read",
  turnProposal: "turn.read",
  state: "state.read",
  sessionMetadata: null,
});
const PROPOSE_PERMISSION = Object.freeze({
  context: "turn.read",
  state: "state.propose",
  informationModule: "ui.contribute",
});
const SERVICE_NAMES = Object.freeze(["model", "memory", "network", "ui"]);

function diagnostic(phase, code, path = "") {
  return Object.freeze({ phase, severity: "error", code, path });
}
function report(errors, value = undefined) {
  const diagnostics = Object.freeze(errors.slice());
  return Object.freeze({ valid: diagnostics.length === 0, diagnostics, value: diagnostics.length === 0 ? value : null });
}
function cloneJson(value) {
  const canonical = canonicalJson(value);
  return canonical.valid ? { valid: true, value: JSON.parse(canonical.value), canonical: canonical.value } : { valid: false };
}
function hashText(text, hash) {
  let value;
  try { value = hash(text); } catch { return null; }
  if (value && typeof value.then === "function") return null;
  return typeof value === "string" && /^[a-f0-9]{64}$/iu.test(value) ? value.toLowerCase() : null;
}
function manifestSemantics(manifest) {
  const errors = [];
  if (new Set(manifest.settings.map((item) => item.key)).size !== manifest.settings.length) errors.push(diagnostic("reference", "PLUGIN_HOST_MANIFEST_SETTING_DUPLICATE", "/manifest/settings"));
  if (manifest.settings.some((item) => item.valueType === "integer" && item.minimum !== undefined && item.maximum !== undefined && item.minimum > item.maximum)) errors.push(diagnostic("policy", "PLUGIN_HOST_MANIFEST_SETTING_RANGE", "/manifest/settings"));
  if (new Set(manifest.dependencies.map((item) => item.pluginId)).size !== manifest.dependencies.length) errors.push(diagnostic("reference", "PLUGIN_HOST_MANIFEST_DEPENDENCY_DUPLICATE", "/manifest/dependencies"));
  if (manifest.dependencies.some((item) => item.pluginId === manifest.plugin.id)) errors.push(diagnostic("reference", "PLUGIN_HOST_MANIFEST_SELF_DEPENDENCY", "/manifest/dependencies"));
  const networkPermission = manifest.permissions.includes("network.request");
  if ((manifest.network.mode === "modelmirror-mediated") !== networkPermission) errors.push(diagnostic("policy", "PLUGIN_HOST_MANIFEST_NETWORK_BINDING", "/manifest/network"));
  return errors;
}
function authorizationSemantics(record, registration) {
  const errors = [];
  const manifest = registration?.manifest;
  if (!manifest || record.pluginId !== manifest.plugin.id || record.version !== manifest.plugin.version || record.manifestSha256.toLowerCase() !== registration.manifestSha256 || record.artifactSha256.toLowerCase() !== registration.artifactSha256) errors.push(diagnostic("reference", "PLUGIN_HOST_AUTHORIZATION_BINDING", "/pluginId"));
  if (!manifest) return errors;
  for (const permission of record.permissions) if (!manifest.permissions.includes(permission)) errors.push(diagnostic("policy", "PLUGIN_HOST_AUTHORIZATION_PERMISSION", "/permissions"));
  for (const scope of record.read) {
    if (!manifest.dataAccess.read.includes(scope) || READ_PERMISSION[scope] && !record.permissions.includes(READ_PERMISSION[scope])) errors.push(diagnostic("policy", "PLUGIN_HOST_AUTHORIZATION_READ", "/read"));
  }
  for (const scope of record.propose) {
    if (!manifest.dataAccess.propose.includes(scope) || PROPOSE_PERMISSION[scope] && !record.permissions.includes(PROPOSE_PERMISSION[scope])) errors.push(diagnostic("policy", "PLUGIN_HOST_AUTHORIZATION_PROPOSE", "/propose"));
  }
  const settings = new Map();
  for (const entry of record.settings) {
    if (settings.has(entry.key)) { errors.push(diagnostic("reference", "PLUGIN_HOST_AUTHORIZATION_SETTING_DUPLICATE", "/settings")); continue; }
    settings.set(entry.key, entry.value);
    const declaration = manifest.settings.find((item) => item.key === entry.key);
    if (!declaration) { errors.push(diagnostic("reference", "PLUGIN_HOST_AUTHORIZATION_SETTING_UNKNOWN", "/settings")); continue; }
    const value = entry.value;
    const typeOk = declaration.valueType === "boolean" ? typeof value === "boolean" : declaration.valueType === "integer" ? Number.isSafeInteger(value) : typeof value === "string";
    if (!typeOk || declaration.valueType === "integer" && (declaration.minimum !== undefined && value < declaration.minimum || declaration.maximum !== undefined && value > declaration.maximum) || declaration.valueType === "shortText" && value.length > declaration.maxLength || declaration.valueType === "enum" && !declaration.choices.includes(value)) errors.push(diagnostic("policy", "PLUGIN_HOST_AUTHORIZATION_SETTING_VALUE", "/settings"));
  }
  if (record.action === "authorize") for (const declaration of manifest.settings) if (declaration.required && !settings.has(declaration.key)) errors.push(diagnostic("policy", "PLUGIN_HOST_AUTHORIZATION_SETTING_REQUIRED", "/settings"));
  if (record.action === "revoke" && (record.permissions.length || record.read.length || record.propose.length || record.settings.length)) errors.push(diagnostic("policy", "PLUGIN_HOST_REVOKE_NONEMPTY", ""));
  return errors;
}
function latestAuthorization(session, pluginId) {
  let latest = null;
  for (const record of session.pluginAuthorizations) if (record.pluginId === pluginId && (!latest || record.revision > latest.revision || record.revision === latest.revision)) latest = record;
  return latest;
}
function dependenciesReady(registration, binding, enabled, registrations, session = null, visiting = new Set()) {
  const pluginId = registration.manifest.plugin.id;
  if (visiting.has(pluginId)) return false;
  const next = new Set(visiting); next.add(pluginId);
  return registration.manifest.dependencies.every((dependency) => {
    const target = registrations.get(dependency.pluginId), active = enabled.get(binding.sessionId + "\0" + dependency.pluginId);
    const latest = session ? latestAuthorization(session, dependency.pluginId) : null;
    return target && active && active.evidenceKind === binding.evidenceKind && active.cardPackageSha256 === binding.cardPackageSha256 && active.playerSetupSha256 === binding.playerSetupSha256 && target.manifest.plugin.version === dependency.version && dependency.capabilities.every((item) => target.manifest.capabilities.includes(item)) && (!session || latest?.action === "authorize" && active.authorizationCanonical === cloneJson(latest).canonical) && dependenciesReady(target, latest ?? binding, enabled, registrations, session, next);
  });
}
function unavailableServices() {
  const services = {};
  for (const name of SERVICE_NAMES) services[name] = Object.freeze({ available: false });
  return Object.freeze(services);
}
function proposalsValid(proposals, allowed) {
  if (!Array.isArray(proposals) || proposals.length > 64) return false;
  let total = 0;
  return proposals.every((proposal) => proposal && typeof proposal === "object" && !Array.isArray(proposal) && Object.keys(proposal).sort().join(",") === "content,scope" && ["context", "state", "informationModule"].includes(proposal.scope) && allowed.includes(proposal.scope) && typeof proposal.content === "string" && proposal.content.length >= 1 && proposal.content.length <= 65536 && (total += proposal.content.length) <= 262144);
}

export function createPluginHost({ hash } = {}) {
  if (typeof hash !== "function" || hashText("", hash) === null) return report([diagnostic("preflight", "PLUGIN_HOST_HASH_ARGUMENT", "/hash")]);
  const registrations = new Map(), enabled = new Map(), running = new Map();
  let epoch = 0;

  const checkAuthorization = (record) => {
    const snapshot = cloneJson(record);
    if (!snapshot.valid || !validatePluginAuthorization(snapshot.value).valid) return report([diagnostic("schema", "PLUGIN_HOST_AUTHORIZATION_INVALID", "")]);
    const errors = authorizationSemantics(snapshot.value, registrations.get(snapshot.value.pluginId));
    return report(errors, errors.length ? undefined : Object.freeze(snapshot.value));
  };

  const host = {
    register(input) {
      let snapshot, adapter;
      try {
        if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).sort().join(",") !== "adapter,artifactSha256,manifest,manifestSha256") throw new Error();
        if (![Object.prototype, null].includes(Object.getPrototypeOf(input)) || Object.getOwnPropertySymbols(input).length) throw new Error();
        const descriptors = Object.getOwnPropertyDescriptors(input);
        if (Object.values(descriptors).some((item) => !Object.hasOwn(item, "value") || item.enumerable !== true)) throw new Error();
        snapshot = cloneJson({ manifest: input.manifest, manifestSha256: input.manifestSha256, artifactSha256: input.artifactSha256 });
        adapter = input.adapter;
      } catch { snapshot = { valid: false }; }
      if (!snapshot.valid) return report([diagnostic("preflight", "PLUGIN_HOST_REGISTER_INPUT", "")]);
      const { manifest, manifestSha256, artifactSha256 } = snapshot.value;
      if (typeof manifestSha256 !== "string" || typeof artifactSha256 !== "string") return report([diagnostic("schema", "PLUGIN_HOST_REGISTER_HASH_TYPE", "")]);
      if (!validateManifestSchema(manifest)) return report([diagnostic("schema", "PLUGIN_HOST_MANIFEST_INVALID", "/manifest")]);
      const semantics = manifestSemantics(manifest); if (semantics.length) return report(semantics);
      const manifestCanonical = cloneJson(manifest), actualManifestSha = manifestCanonical.valid && hashText(manifestCanonical.canonical, hash);
      if (!actualManifestSha || actualManifestSha !== String(manifestSha256).toLowerCase()) return report([diagnostic("reference", "PLUGIN_HOST_MANIFEST_HASH", "/manifestSha256")]);
      if (!/^[a-f0-9]{64}$/iu.test(artifactSha256) || artifactSha256.toLowerCase() !== manifest.provenance.artifactSha256.toLowerCase()) return report([diagnostic("reference", "PLUGIN_HOST_ARTIFACT_HASH", "/artifactSha256")]);
      if (registrations.has(manifest.plugin.id)) return report([diagnostic("policy", "PLUGIN_HOST_PLUGIN_DUPLICATE", "/manifest/plugin/id")]);
      let invokeDescriptor;
      try { invokeDescriptor = adapter && Object.getOwnPropertyDescriptor(adapter, "invoke"); } catch { invokeDescriptor = null; }
      if (!invokeDescriptor || !Object.hasOwn(invokeDescriptor, "value") || typeof invokeDescriptor.value !== "function") return report([diagnostic("preflight", "PLUGIN_HOST_ADAPTER_INVALID", "/adapter")]);
      const registration = Object.freeze({ manifest: Object.freeze(manifest), manifestSha256: actualManifestSha, artifactSha256: artifactSha256.toLowerCase(), invoke: invokeDescriptor.value });
      registrations.set(manifest.plugin.id, registration);
      return report([], Object.freeze({ pluginId: manifest.plugin.id, version: manifest.plugin.version }));
    },
    checkAuthorization,
    enable(record) {
      const checked = checkAuthorization(record); if (!checked.valid) return checked;
      const authorization = checked.value;
      if (authorization.action !== "authorize") return report([diagnostic("policy", "PLUGIN_HOST_ENABLE_REQUIRES_AUTHORIZE", "/action")]);
      const key = authorization.sessionId + "\0" + authorization.pluginId, registration = registrations.get(authorization.pluginId);
      if (!dependenciesReady(registration, authorization, enabled, registrations)) return report([diagnostic("readiness", "PLUGIN_HOST_DEPENDENCY_NOT_ENABLED", "/pluginId")]);
      const authorizationCanonical = cloneJson(authorization).canonical;
      enabled.set(key, Object.freeze({ authorizationCanonical, authorizationSha256: hashText(authorizationCanonical, hash), evidenceKind: authorization.evidenceKind, cardPackageSha256: authorization.cardPackageSha256, playerSetupSha256: authorization.playerSetupSha256, token: ++epoch }));
      return report([], Object.freeze({ pluginId: authorization.pluginId, sessionId: authorization.sessionId }));
    },
    disable(input) {
      const snapshot = cloneJson(input); if (!snapshot.valid || !snapshot.value || typeof snapshot.value.sessionId !== "string" || typeof snapshot.value.pluginId !== "string") return report([diagnostic("schema", "PLUGIN_HOST_DISABLE_INPUT", "")]);
      if (Object.keys(snapshot.value).sort().join(",") !== "pluginId,sessionId") return report([diagnostic("schema", "PLUGIN_HOST_DISABLE_INPUT", "")]);
      const key = snapshot.value.sessionId + "\0" + snapshot.value.pluginId; enabled.delete(key); epoch += 1; running.get(key)?.controller.abort();
      return report([], Object.freeze({ pluginId: snapshot.value.pluginId, sessionId: snapshot.value.sessionId }));
    },
    readiness(cardPackage, session) {
      const card = cloneJson(cardPackage), runtime = cloneJson(session);
      if (!card.valid || !runtime.valid || !validateCardPackage(card.value).valid || !validateRuntimeSession(runtime.value).valid) return Object.freeze({ ready: false, diagnostics: Object.freeze([diagnostic("preflight", "PLUGIN_HOST_READINESS_INPUT", "")]) });
      if (runtime.value.resources.cardPackage.sha256.toLowerCase() !== hashText(card.canonical, hash) || runtime.value.resources.cardPackage.id !== card.value.package.id || runtime.value.resources.cardPackage.version !== card.value.package.version) return Object.freeze({ ready: false, diagnostics: Object.freeze([diagnostic("reference", "PLUGIN_HOST_READINESS_BINDING", "/cardPackage")]) });
      const effective = [];
      for (const [pluginId, registration] of registrations) {
        const latest = latestAuthorization(runtime.value, pluginId), active = enabled.get(runtime.value.sessionId + "\0" + pluginId);
        if (latest?.action === "authorize" && active?.authorizationCanonical === cloneJson(latest).canonical && dependenciesReady(registration, latest, enabled, registrations, runtime.value)) effective.push(registration.manifest);
      }
      return evaluatePluginReadiness(card.value, effective);
    },
    async invoke(input) {
      const snapshot = cloneJson(input);
      const allowedInput = ["capability", "cardPackage", "playerSetup", "pluginId", "session", "timeoutMs", "turnInput", "turnProposal"];
      if (!snapshot.valid || !snapshot.value || typeof snapshot.value !== "object" || Array.isArray(snapshot.value) || Object.keys(snapshot.value).some((keyName) => !allowedInput.includes(keyName)) || !["pluginId", "capability", "session", "cardPackage", "playerSetup"].every((keyName) => Object.hasOwn(snapshot.value, keyName))) return report([diagnostic("preflight", "PLUGIN_HOST_INVOKE_INPUT", "")]);
      const value = snapshot.value, timeoutMs = value.timeoutMs ?? 1000;
      if (typeof value.pluginId !== "string" || typeof value.capability !== "string") return report([diagnostic("schema", "PLUGIN_HOST_INVOKE_INPUT", "")]);
      if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000) return report([diagnostic("policy", "PLUGIN_HOST_TIMEOUT", "/timeoutMs")]);
      const registration = registrations.get(value.pluginId), session = value.session, key = String(session?.sessionId) + "\0" + value.pluginId;
      if (!registration || !registration.manifest.capabilities.includes(value.capability)) return report([diagnostic("reference", "PLUGIN_HOST_CAPABILITY", "/capability")]);
      if (!validateRuntimeSession(session, value.cardPackage, value.playerSetup, hash).valid || !validateRuntimeResourceBindings(value.cardPackage, value.playerSetup, session?.resources, hash).valid) return report([diagnostic("reference", "PLUGIN_HOST_RUNTIME_BINDING", "/session")]);
      const latest = latestAuthorization(session, value.pluginId), active = enabled.get(key), checked = latest && checkAuthorization(latest);
      if (!active || !checked?.valid || latest.action !== "authorize" || active.authorizationCanonical !== cloneJson(latest).canonical || !dependenciesReady(registration, latest, enabled, registrations, session)) return report([diagnostic("policy", "PLUGIN_HOST_NOT_ENABLED", "/pluginId")]);
      if (running.has(key)) return report([diagnostic("policy", "PLUGIN_HOST_INVOCATION_BUSY", "/pluginId")]);
      const data = {};
      for (const scope of latest.read) {
        if (scope === "card") data.card = value.cardPackage;
        else if (scope === "playerSetup") data.playerSetup = value.playerSetup;
        else if (scope === "turnInput" && Object.hasOwn(value, "turnInput")) data.turnInput = value.turnInput;
        else if (scope === "turnProposal" && Object.hasOwn(value, "turnProposal")) data.turnProposal = value.turnProposal;
        else if (scope === "state") data.state = session.state;
        else if (scope === "sessionMetadata") data.sessionMetadata = { sessionId: session.sessionId, resources: session.resources, revision: session.revision };
      }
      const controller = new AbortController(), invocationEpoch = ++epoch, token = active.token;
      running.set(key, { controller, invocationEpoch });
      let timer;
      try {
        const adapterPromise = Promise.resolve().then(() => registration.invoke(Object.freeze({ pluginId: value.pluginId, capability: value.capability, data: cloneJson(data).value, settings: cloneJson(Object.fromEntries(latest.settings.map((item) => [item.key, item.value]))).value, signal: controller.signal, services: unavailableServices() })));
        const timeoutPromise = new Promise((resolve) => { timer = setTimeout(() => { controller.abort(); resolve({ timeout: true }); }, timeoutMs); });
        const outcome = await Promise.race([adapterPromise.then((result) => ({ result }), () => ({ failed: true })), timeoutPromise]);
        if (outcome.timeout) return report([diagnostic("runtime", "PLUGIN_HOST_INVOCATION_TIMEOUT", "")]);
        const current = enabled.get(key), currentLatest = latestAuthorization(session, value.pluginId);
        if (outcome.failed || current?.token !== token || running.get(key)?.invocationEpoch !== invocationEpoch || current?.authorizationCanonical !== cloneJson(currentLatest).canonical || !dependenciesReady(registration, currentLatest, enabled, registrations, session)) return report([diagnostic("runtime", "PLUGIN_HOST_INVOCATION_FAILED", "")]);
        const output = cloneJson(outcome.result);
        if (!output.valid || !output.value || Object.keys(output.value).length !== 1 || !Array.isArray(output.value.proposals) || output.value.proposals.length > 64) return report([diagnostic("schema", "PLUGIN_HOST_OUTPUT_INVALID", "")]);
        if (!proposalsValid(output.value.proposals, latest.propose)) return report([diagnostic("policy", "PLUGIN_HOST_OUTPUT_PROPOSAL", "/proposals")]);
        return report([], Object.freeze({ sessionId: session.sessionId, revision: session.revision, pluginId: value.pluginId, version: registration.manifest.plugin.version, capability: value.capability, evidenceKind: latest.evidenceKind, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, authorizationSha256: current.authorizationSha256, activationEpoch: token, proposals: Object.freeze(output.value.proposals.map((item) => Object.freeze(item))) }));
      } finally {
        clearTimeout(timer);
        if (running.get(key)?.invocationEpoch === invocationEpoch) running.delete(key);
      }
    },
    validateResult(value, session) {
      const result = cloneJson(value), runtime = cloneJson(session);
      if (!result.valid || !runtime.valid || !validateRuntimeSession(runtime.value).valid) return report([diagnostic("schema", "PLUGIN_HOST_RESULT_INVALID", "")]);
      const expectedKeys = ["activationEpoch", "authorizationSha256", "capability", "cardPackageSha256", "evidenceKind", "playerSetupSha256", "pluginId", "proposals", "revision", "sessionId", "version"];
      if (!result.value || Object.keys(result.value).sort().join(",") !== expectedKeys.sort().join(",")) return report([diagnostic("schema", "PLUGIN_HOST_RESULT_INVALID", "")]);
      const registration = registrations.get(result.value.pluginId), latest = latestAuthorization(runtime.value, result.value.pluginId), active = enabled.get(runtime.value.sessionId + "\0" + result.value.pluginId);
      if (!registration || !latest || result.value.sessionId !== runtime.value.sessionId || result.value.revision !== runtime.value.revision || result.value.version !== registration.manifest.plugin.version || result.value.evidenceKind !== latest.evidenceKind || result.value.cardPackageSha256 !== runtime.value.resources.cardPackage.sha256 || result.value.playerSetupSha256 !== runtime.value.resources.playerSetup.sha256 || result.value.authorizationSha256 !== hashText(cloneJson(latest).canonical, hash) || result.value.activationEpoch !== active?.token || !registration.manifest.capabilities.includes(result.value.capability) || active.authorizationCanonical !== cloneJson(latest).canonical || !dependenciesReady(registration, latest, enabled, registrations, runtime.value)) return report([diagnostic("reference", "PLUGIN_HOST_RESULT_STALE", "")]);
      if (!proposalsValid(result.value.proposals, latest.propose)) return report([diagnostic("policy", "PLUGIN_HOST_RESULT_PROPOSAL", "/proposals")]);
      return report([], Object.freeze(result.value));
    },
  };
  return report([], Object.freeze(host));
}
