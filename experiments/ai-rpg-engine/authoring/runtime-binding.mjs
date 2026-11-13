import {readFile} from 'node:fs/promises';
import {AuthoringStore} from './store.mjs';
import {canonical, fail, keys, sha256, validateConfiguration, publicationIssues} from './contracts.mjs';

export const RUNTIME_CONTRACT = 'modelmirror.rpg.authored-runtime/1';
export const BINDING_FORMAT = 'modelmirror.rpg.authored-session-binding/1';
// An internal protocol key for the legacy plugin registry, never an author/card ID.
export const PLUGIN_CONTRACT_KEY = 'authored-work-v1';
const WORK = /^work-[a-f0-9]{64}$/, VERSION = /^version-[a-f0-9]{64}$/;
const sessionId = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(value);
const revision = value => Number.isSafeInteger(value) && value >= 0;
const digest = value => sha256(canonical(value));
const root = new URL('../', import.meta.url);
const sourcePaths = [
  'package-lock.json',
  'authoring/runtime-binding.mjs', 'authoring/runtime-plugins.mjs', 'authoring/store.mjs',
  'authoring/generation-settings.mjs', 'authoring/model-control.mjs', 'authoring/model-plugin.mjs', 'authoring/session-storage.mjs',
  'authoring/variable-panel.mjs', 'authoring/variable-template-syntax.mjs', 'authoring/contracts.mjs', 'authoring/metadata.mjs', 'authoring/gallery.mjs', 'authoring/dialogue.mjs',
  'plugins/catalog.mjs', 'plugins/host.mjs', 'plugins/owner-lock.mjs', 'plugins/transcript.mjs', 'plugins/message-tools.mjs', 'plugins/illustrations.mjs',
  ...['branch-save', 'model-selector', 'history-window', 'rolling-summary', 'memory-palace', 'generation-parameters', 'model-comparison']
    .flatMap(name => ['plugins/' + name + '.mjs', 'plugins/' + name + '.manifest.json']),
];
async function artifactHashes() {
  return Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, sha256(await readFile(new URL(path, root)))])));
}
// These are binding/proposal artifacts, not a claim that the story runtime is wired.
const loadedArtifacts = await artifactHashes();
const implementationHash = digest(loadedArtifacts);
const runtimes = new WeakSet();
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
export function requirePublishedRuntime(runtime) {
  if (!runtimes.has(runtime)) fail('AUTHORING_RUNTIME_HOST_REQUIRED');
  return runtime;
}
export function runtimeReference(value) {
  canonical(value); keys(value, ['workId', 'versionId']);
  if (typeof value.workId !== 'string' || !WORK.test(value.workId)) fail('AUTHORING_WORK_ID_INVALID');
  if (typeof value.versionId !== 'string' || !VERSION.test(value.versionId)) fail('AUTHORING_VERSION_ID_INVALID');
  return structuredClone(value);
}

// No draft/latest fallback and no factory/provider supplied by a work package.
// This module is host-only: its hash checks detect inconsistency, not malicious
// writers with access to the host's session storage.
export async function openPublishedRuntime(store, reference) {
  if (!(store instanceof AuthoringStore)) fail('AUTHORING_RUNTIME_STORE_REQUIRED');
  const ref = runtimeReference(reference);
  const config = freeze(validateConfiguration(await store.exportConfiguration(ref.workId, ref.versionId)));
  const issues = publicationIssues(config.document, config.assets);
  if (issues.length) fail('AUTHORING_PUBLICATION_INVALID', '', {issues});
  const descriptor = freeze({
    format: RUNTIME_CONTRACT, stage: 'binding-and-proposals', ...ref,
    configurationHash: digest(config), assetsHash: digest(config.assets),
    implementationHash, artifactHashes: {...loadedArtifacts}, runtimeReady: false,
  });
  const runtimeHash = digest(descriptor);
  // Exact pre-upgrade bindings on deployed 425ff762 and merged c9db3034.
  // Rebuild against this work's immutable publication; retain its resource identity.
  const previousHashes = [
    {'plugins/host.mjs':'f5ecc40c20c4482a564b72d1836dfac3bb1f4e2555169e92862132f9b92cad27','plugins/catalog.mjs':'a6f955f96c1417efe9f3e2c334d045f0b4376b53bc7eaa04d773818c13072486'},
    {'plugins/host.mjs':'bd43d839c5023c8f06fe068fc434452897ed798558bb1333798cf331c6e5dabd'},
  ].map(before=>{
    const artifacts={...loadedArtifacts,...before,'authoring/runtime-binding.mjs':'10301c3fe357df77823ff08c86604d4907c554cbb620d482090ceaed24fb872c'};
    return digest({...descriptor,artifactHashes:artifacts,implementationHash:digest(artifacts)});
  });
  async function verify() {
    if (digest(await artifactHashes()) !== implementationHash) fail('AUTHORING_RUNTIME_FILES_CHANGED_RESTART_REQUIRED');
    const current = await store.verifyConfiguration(ref.workId, ref.versionId);
    if (digest(current) !== descriptor.configurationHash) fail('AUTHORING_RUNTIME_VERSION_CHANGED');
    return true;
  }
  function bind({sessionId: id, openingText}) {
    if (!sessionId(id)) fail('AUTHORING_RUNTIME_SESSION_ID_INVALID');
    if (typeof openingText !== 'string' || !openingText.isWellFormed() || !openingText.trim() || [...openingText].length > 200000) fail('AUTHORING_RUNTIME_OPENING_INVALID');
    const identity = {...ref, sessionId: id, runtimeHash, openingHash: sha256(openingText)};
    return freeze({format: BINDING_FORMAT, ...identity, resourceHash: digest(identity), openingText});
  }
  function requireBinding(value) {
    canonical(value);
    keys(value, ['format', 'workId', 'versionId', 'sessionId', 'runtimeHash', 'openingHash', 'resourceHash', 'openingText']);
    const expected = bind({sessionId: value.sessionId, openingText: value.openingText});
    if (previousHashes.includes(value.runtimeHash)) {
      const previousHash = value.runtimeHash;
      const identity = {...ref, sessionId:value.sessionId, runtimeHash:previousHash, openingHash:expected.openingHash};
      const previous = {...expected, runtimeHash:previousHash, resourceHash:digest(identity)};
      if (canonical(value) === canonical(previous)) return structuredClone(previous);
    }
    if (canonical(value) !== canonical(expected)) fail('AUTHORING_RUNTIME_BINDING_MISMATCH');
    return structuredClone(expected);
  }
  const runtime = Object.freeze({
    descriptor, hash: runtimeHash,
    readiness: freeze({runtimeReady: false, bindingReady: true, pluginProposalProtocol: true,
      required: config.capabilities.required.slice(),
      remaining: ['session-persistence', 'controlled-model-parameters', 'story-dispatch', 'background-tasks', 'branch-materialization', 'play-ui']}),
    configuration: () => structuredClone(config),
    verify,
    bindSession(input) {
      canonical(input); keys(input, ['sessionId', 'openingText']);
      // Only a binding value; this does not create a session, grant or quota.
      return bind(input);
    },
    requireBinding,
    async asset(alias) {
      // Asset reads always use the factory's exact version.
      await verify();
      const value = await store.getAsset(ref.workId, alias, ref.versionId);
      return {asset: structuredClone(value.asset), bytes: Buffer.from(value.bytes)};
    },
    async projectSession(session) {
      // The future host's persisted session/lock state, NOT a browser payload.
      canonical(session); keys(session, ['id', 'revision', 'binding', 'completedTurns', 'busy', 'pending']);
      session = structuredClone(session); // Freeze one read before asynchronous integrity checks.
      const binding = requireBinding(session.binding);
      if (session.id !== binding.sessionId || !revision(session.revision) || !revision(session.completedTurns) ||
          typeof session.busy !== 'boolean' || typeof session.pending !== 'boolean') fail('AUTHORING_RUNTIME_SESSION_INVALID');
      await verify();
      return {id: session.id, revision: session.revision, cardId: PLUGIN_CONTRACT_KEY,
        completedTurns: session.completedTurns, resourceHash: binding.resourceHash, busy: session.busy, pending: session.pending,
        // These flags admit proposal validation only. This bridge has no dispatcher.
        historyWindowCompatible: true, rollingSummaryCompatible: true, memoryPalaceCompatible: true};
    },
  });
  await verify(); runtimes.add(runtime); return runtime;
}
