import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedCatalog, PLUGIN_ID, MODEL_SELECTOR_ID, HISTORY_WINDOW_ID, ROLLING_SUMMARY_ID, MEMORY_PALACE_ID, sha, canonical} from '../plugins/catalog.mjs';
import {fail} from './contracts.mjs';
import {RUNTIME_CONTRACT, PLUGIN_CONTRACT_KEY, requirePublishedRuntime, runtimeReference} from './runtime-binding.mjs';

// A separate host compatibility release, not a mutation of Earth/God packages.
// All five executable artifacts/permissions remain exactly those verified by the
// existing reviewed catalog. The release admits a protocol, never arbitrary code.
export async function loadAuthoringPluginPackages() {
  const supported = [PLUGIN_ID, MODEL_SELECTOR_ID, HISTORY_WINDOW_ID, ROLLING_SUMMARY_ID, MEMORY_PALACE_ID];
  return Promise.all(supported.map(async id => {
    const original = await loadReviewedCatalog(id);
    const manifest = {...original.manifest, version: '2.0.0', compatibleCards: [PLUGIN_CONTRACT_KEY],
      description: original.manifest.description + ' 创作宿主当前仅接入授权与提案校验，尚未接入任务执行。',
      source: original.manifest.source + ' | ' + RUNTIME_CONTRACT + ' (proposal bridge)'};
    return {...original, manifest, manifestSha256: sha(canonical(manifest))};
  }));
}

export async function createAuthoringPluginBridge({directory, lookupSession, resolveRuntime}) {
  if (typeof lookupSession !== 'function' || typeof resolveRuntime !== 'function') fail('AUTHORING_RUNTIME_HOST_REQUIRED');
  const pluginService = await createPluginService({
    directory,
    loadCatalog: loadAuthoringPluginPackages,
    lookupSession: async id => {
      const session = await lookupSession(id);
      if (!session || session.id !== id) fail('AUTHORING_RUNTIME_SESSION_INVALID');
      const reference = runtimeReference({workId: session.binding?.workId, versionId: session.binding?.versionId});
      const runtime = requirePublishedRuntime(await resolveRuntime(reference));
      return runtime.projectSession(session);
    },
  });
  // No transport, task coordinator, budget allocation or executable restoration.
  // Callers must supply host-owned persistence and dispatch in a later B6 batch.
  return Object.freeze({contract: RUNTIME_CONTRACT, stage: 'proposals-only', runtimeReady: false, pluginService});
}
