const HASH = /^sha256:[0-9a-f]{64}$/u;
const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;

export class R12HostOperationalError extends Error {
  constructor() {
    super("R12_HOST_INTERNAL_ERROR");
    this.name = "R12HostOperationalError";
    this.code = "R12_HOST_INTERNAL_ERROR";
  }
}

function exactFunctions(value, names) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === names.length &&
    names.every((name) => typeof value[name] === "function");
}

function rejected(code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([Object.freeze({ code, path: "" })]) });
}

export function createR12PrototypeOperations(steps) {
  const names = [
    "findCache", "generate", "describeAssets", "acquireEnvironment", "acquireAssets",
    "normalizeAssets", "spatializeEnvironment", "publishPrototype", "publishSpatial",
    "persistPending", "recoverPending", "discardPending",
    "launch", "recover", "stopLaunch",
  ];
  if (!exactFunctions(steps, names)) throw new R12HostOperationalError();
  return Object.freeze({
    async findCache(input) { return steps.findCache(input); },
    async generate(input) { return steps.generate(input); },
    async describeAssets(input) { return steps.describeAssets(input); },
    async acquire({ artifacts, approval, onStage }) {
      try {
        const environment = await steps.acquireEnvironment({ artifacts, approval });
        if (!environment?.ok) return environment ?? rejected("R12_HOST_ENVIRONMENT_FAILED");
        const assets = await steps.acquireAssets({ artifacts, approval });
        if (!assets?.ok) return assets ?? rejected("R12_HOST_ASSETS_FAILED");
        onStage("normalizing");
        const normalized = await steps.normalizeAssets({ artifacts, assets });
        if (!normalized?.ok) return normalized ?? rejected("R12_HOST_NORMALIZATION_FAILED");
        onStage("spatializing");
        const spatial = await steps.spatializeEnvironment({ artifacts, environment });
        if (!spatial?.ok) return spatial ?? rejected("R12_HOST_SPATIALIZATION_FAILED");
        return Object.freeze({ ok: true, environment, normalized, spatial });
      } catch { return rejected("R12_HOST_ACQUISITION_FAILED"); }
    },
    async publish({ prompt, promptSha256, model, artifacts, acquisition }) {
      try {
        const prototype = await steps.publishPrototype({ prompt, promptSha256, model, artifacts, acquisition });
        if (!prototype?.ok || typeof prototype.runId !== "string" || !RUN_ID.test(prototype.runId)) {
          return rejected("R12_HOST_ASSEMBLY_FAILED");
        }
        const spatial = await steps.publishSpatial({ runId: prototype.runId, artifacts, acquisition });
        if (!spatial?.ok || spatial.runId !== prototype.runId) return rejected("R12_HOST_SPATIAL_ASSEMBLY_FAILED");
        return Object.freeze({ ok: true, runId: prototype.runId });
      } catch { return rejected("R12_HOST_ASSEMBLY_FAILED"); }
    },
    async persistPending(input) { return steps.persistPending(input); },
    async recoverPending() { return steps.recoverPending(); },
    async discardPending(input) { return steps.discardPending(input); },
    async launch(input) { return steps.launch(input); },
    async recover() { return steps.recover(); },
    async stopLaunch() { return steps.stopLaunch(); },
  });
}

export function validateR12AssetApprovalSummary(value) {
  try {
    return value !== null && typeof value === "object" && HASH.test(value.blueprintSha256) &&
      value.marble?.model === "marble-1.1" && typeof value.marble.recovered === "boolean" &&
      (value.marble.recovered
        ? value.marble.maxCreates === 0 && value.marble.maxPolls === 0 && value.marble.maxDownloads === 0 &&
          value.marble.creditLimit === 0 && value.marble.usdLimitCents === 0
        : value.marble.maxCreates === 1 && value.marble.maxPolls === 180 && value.marble.maxDownloads === 3 &&
          value.marble.creditLimit === 1600 && value.marble.usdLimitCents === 150) &&
      value.meshy?.model === "meshy-6" && Array.isArray(value.meshy.briefs) &&
      value.meshy.briefs.length <= 6 && ((value.meshy.maxTasks === 0 && value.meshy.creditLimit === 0) ||
        (value.meshy.maxTasks === value.meshy.briefs.length * 2 &&
          value.meshy.creditLimit === value.meshy.briefs.length * 30));
  } catch { return false; }
}
