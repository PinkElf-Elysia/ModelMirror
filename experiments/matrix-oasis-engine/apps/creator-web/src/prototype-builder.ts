export const PROTOTYPE_BUILDER_MARKER =
  "MATRIX_OASIS_R10_PROTOTYPE_BUILDER";
export const R16_PROTOTYPE_BUILDER_MARKER =
  "MATRIX_OASIS_R16_CREATOR_MVP_READY";

export const PROTOTYPE_RUN_STATES = Object.freeze([
  "awaiting_model_approval",
  "generating",
  "awaiting_asset_approval",
  "acquiring",
  "normalizing",
  "spatializing",
  "assembling",
  "qualifying",
  "ready",
  "failed",
] as const);

export type PrototypeRunStatus = (typeof PROTOTYPE_RUN_STATES)[number];

export interface PrototypeBuilderDiagnostic {
  readonly phase: "host";
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeModelApproval {
  readonly endpointHost: string;
  readonly model: string;
  readonly maxRequests: 3;
  readonly maxUsdCents: 100;
  readonly prompt: string;
  readonly promptSha256: string;
  readonly approvalHash: string;
  readonly approved: boolean;
}

export interface PrototypeAssetApproval {
  readonly blueprintSha256: string;
  readonly marble: Readonly<{
    model: "marble-1.1";
    environmentPrompt: string;
    recovered: boolean;
    maxCreates: 0 | 1;
    maxPolls: 0 | 180;
    maxDownloads: 0 | 2 | 3;
    creditLimit: 0 | 1600;
    usdLimitCents: 0 | 150;
  }>;
  readonly meshy: Readonly<{
    model: "meshy-6";
    briefs: readonly Readonly<{
      id: string;
      kind: "prop" | "character-placeholder";
      prompt: string;
    }>[];
    maxTasks: number;
    creditLimit: number;
  }>;
  readonly approvalHash: string;
  readonly approved: boolean;
}

export interface PrototypeRun {
  readonly id: string;
  readonly status: PrototypeRunStatus;
  readonly cacheHit: boolean;
  readonly diagnostics: readonly PrototypeBuilderDiagnostic[];
  readonly modelApproval: PrototypeModelApproval | null;
  readonly assetApproval: PrototypeAssetApproval | null;
  readonly resultRunId: string | null;
  readonly qualification?: PrototypeQualification | null;
}

export interface PrototypeQualificationEvidence {
  readonly runId: string;
  readonly attempt: 0 | 1 | 2;
  readonly replayCount: number;
  readonly screenshotCount: number;
  readonly videoCount: 1;
  readonly sampleCount: 300;
  readonly medianFrameMicros: number;
  readonly medianFpsMilli: number;
}

export interface PrototypeQualification {
  readonly profile: "matrix-oasis.creator-solved-evidence/1";
  readonly cacheLevel: "qualified" | "evidence-only" | "solved-only" | "source-only" | null;
  readonly subphase: "analyzing" | "solving" | "verifying" | "evidencing" | null;
  readonly attempt: 0 | 1 | 2;
  readonly reusedQualification: boolean;
  readonly solutionSha256: string | null;
  readonly evidence: PrototypeQualificationEvidence | null;
}

export interface PrototypeRecovery {
  readonly model: "marble-1.1";
  readonly worldIdSha256: string;
  readonly maxCreates: 0;
  readonly maxPolls: 0;
  readonly maxWorldGets: 0 | 1;
  readonly maxDownloads: 0 | 3;
  readonly creditLimit: 0;
  readonly usdLimitCents: 0;
  readonly status: "awaiting_approval" | "recovering" | "ready" | "failed";
  readonly diagnostics: readonly PrototypeBuilderDiagnostic[];
  readonly approvalHash: string;
  readonly approved: boolean;
}

export interface PrototypeWorldDiscoveryCandidate {
  readonly worldIdSha256: string;
  readonly promptSha256: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly model: "marble-1.1";
  readonly assets: Readonly<{ panorama: boolean; collider: boolean; spatialSource: boolean }>;
}

export interface PrototypeWorldDiscoveryRecovery {
  readonly worldIdSha256: string;
  readonly maxCreates: 0;
  readonly maxPolls: 0;
  readonly maxWorldGets: 1;
  readonly maxDownloads: 3;
  readonly creditLimit: 0;
  readonly usdLimitCents: 0;
  readonly status: "awaiting_approval" | "recovering" | "ready" | "failed";
  readonly diagnostics: readonly PrototypeBuilderDiagnostic[];
  readonly approvalHash: string;
  readonly approved: boolean;
}

export interface PrototypeWorldDiscovery {
  readonly provider: "world-labs-marble";
  readonly operation: "worlds:list";
  readonly model: "marble-1.1";
  readonly pageSize: 100;
  readonly status: "SUCCEEDED";
  readonly sortBy: "created_at";
  readonly maxRequests: 1;
  readonly maxCreates: 0;
  readonly maxPolls: 0;
  readonly maxWorldGets: 0;
  readonly maxDownloads: 0;
  readonly creditLimit: 0;
  readonly usdLimitCents: 0;
  readonly statusState: "awaiting_approval" | "querying" | "ready" | "failed";
  readonly diagnostics: readonly PrototypeBuilderDiagnostic[];
  readonly candidates: readonly PrototypeWorldDiscoveryCandidate[];
  readonly recovery: PrototypeWorldDiscoveryRecovery | null;
  readonly approvalHash: string;
  readonly approved: boolean;
}

export interface PrototypeBootstrap {
  readonly marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST" | "MATRIX_OASIS_R16_PROTOTYPE_HOST";
  readonly readiness: Readonly<{
    model: boolean;
    assets: boolean;
    godot: boolean;
  }>;
  readonly currentRunId: string | null;
  readonly runs: readonly PrototypeRun[];
  readonly recovery?: PrototypeRecovery | null;
  readonly worldDiscovery?: PrototypeWorldDiscovery | null;
  readonly qualificationProfile?: "matrix-oasis.creator-solved-evidence/1";
}

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const RUN_ID = /^r10-run-[1-9][0-9]*$/u;
const RESULT_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const QUALIFICATION_RUN_ID = /^[0-9a-f]{64}$/u;
const HASH = /^sha256:[0-9a-f]{64}$/u;
const CODE = /^[A-Z][A-Z0-9_]{2,127}$/u;
const POINTER = /^(?:\/(?:[^~/]|~0|~1)*)*$/u;
const API_PATH = /^\/api\/[A-Za-z0-9/-]{1,256}$/u;
const RUN_STATES = new Set<string>(PROTOTYPE_RUN_STATES);
const RESPONSE_LIMIT = 128 * 1024;

export class PrototypeBuilderClientError extends Error {
  readonly code: string;

  constructor(code = "PROTOTYPE_BUILDER_CLIENT_ERROR") {
    super(code);
    this.name = "PrototypeBuilderClientError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function exactKeys(
  value: unknown,
  keys: readonly string[],
): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function staticDiagnostic(value: unknown): PrototypeBuilderDiagnostic | null {
  if (
    !exactKeys(value, ["phase", "severity", "code", "path", "message"]) ||
    value.phase !== "host" ||
    value.severity !== "error" ||
    typeof value.code !== "string" ||
    !CODE.test(value.code) ||
    typeof value.path !== "string" ||
    value.path.length > 512 ||
    !POINTER.test(value.path) ||
    value.message !== value.code
  ) {
    return null;
  }
  return Object.freeze({
    phase: "host",
    severity: "error",
    code: value.code,
    path: value.path,
    message: value.code,
  });
}

function diagnostics(value: unknown): readonly PrototypeBuilderDiagnostic[] | null {
  if (!Array.isArray(value) || value.length > 64) {
    return null;
  }
  const rebuilt = value.map(staticDiagnostic);
  return rebuilt.some((item) => item === null)
    ? null
    : Object.freeze(rebuilt as PrototypeBuilderDiagnostic[]);
}

function safeText(value: unknown, maximum = 4096): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maximum &&
    !value.includes("\u0000") &&
    value.trim().length > 0
  );
}

function parseModelApproval(value: unknown): PrototypeModelApproval | null {
  const keys = [
    "endpointHost",
    "model",
    "maxRequests",
    "maxUsdCents",
    "prompt",
    "promptSha256",
    "approvalHash",
    "approved",
  ];
  if (
    !exactKeys(value, keys) ||
    !safeText(value.endpointHost, 256) ||
    !safeText(value.model, 128) ||
    value.maxRequests !== 3 ||
    value.maxUsdCents !== 100 ||
    typeof value.prompt !== "string" ||
    value.prompt.length < 1 ||
    new TextEncoder().encode(value.prompt).byteLength > 32_768 ||
    typeof value.promptSha256 !== "string" ||
    !HASH.test(value.promptSha256) ||
    typeof value.approvalHash !== "string" ||
    !HASH.test(value.approvalHash) ||
    typeof value.approved !== "boolean"
  ) {
    return null;
  }
  return Object.freeze({
    endpointHost: value.endpointHost,
    model: value.model,
    maxRequests: 3,
    maxUsdCents: 100,
    prompt: value.prompt,
    promptSha256: value.promptSha256,
    approvalHash: value.approvalHash,
    approved: value.approved,
  });
}

function parseAssetApproval(value: unknown): PrototypeAssetApproval | null {
  if (
    !exactKeys(value, [
      "blueprintSha256",
      "marble",
      "meshy",
      "approvalHash",
      "approved",
    ]) ||
    typeof value.blueprintSha256 !== "string" ||
    !HASH.test(value.blueprintSha256) ||
    typeof value.approvalHash !== "string" ||
    !HASH.test(value.approvalHash) ||
    typeof value.approved !== "boolean" ||
    !exactKeys(value.marble, [
      "model",
      "environmentPrompt",
      "recovered",
      "maxCreates",
      "maxPolls",
      "maxDownloads",
      "creditLimit",
      "usdLimitCents",
    ]) ||
    value.marble.model !== "marble-1.1" ||
    !safeText(value.marble.environmentPrompt) ||
    typeof value.marble.recovered !== "boolean" ||
    (value.marble.recovered
      ? value.marble.maxCreates !== 0 || value.marble.maxPolls !== 0 || value.marble.maxDownloads !== 0 ||
        value.marble.creditLimit !== 0 || value.marble.usdLimitCents !== 0
      : value.marble.maxCreates !== 1 || value.marble.maxPolls !== 180 ||
        ![2, 3].includes(Number(value.marble.maxDownloads)) || value.marble.creditLimit !== 1600 ||
        value.marble.usdLimitCents !== 150) ||
    !exactKeys(value.meshy, ["model", "briefs", "maxTasks", "creditLimit"]) ||
    value.meshy.model !== "meshy-6" ||
    !Array.isArray(value.meshy.briefs) ||
    value.meshy.briefs.length > 6 ||
    !((value.meshy.maxTasks === 0 && value.meshy.creditLimit === 0) ||
      (value.meshy.maxTasks === value.meshy.briefs.length * 2 &&
        value.meshy.creditLimit === value.meshy.briefs.length * 30))
  ) {
    return null;
  }
  const briefs = value.meshy.briefs.map((brief) => {
    if (
      !exactKeys(brief, ["id", "kind", "prompt"]) ||
      !safeText(brief.id, 96) ||
      !["prop", "character-placeholder"].includes(String(brief.kind)) ||
      !safeText(brief.prompt, 600)
    ) {
      return null;
    }
    return Object.freeze({
      id: brief.id,
      kind: brief.kind as "prop" | "character-placeholder",
      prompt: brief.prompt,
    });
  });
  if (briefs.some((brief) => brief === null)) {
    return null;
  }
  return Object.freeze({
    blueprintSha256: value.blueprintSha256,
    marble: Object.freeze({
      model: "marble-1.1",
      environmentPrompt: value.marble.environmentPrompt,
      recovered: value.marble.recovered,
      maxCreates: value.marble.maxCreates as 0 | 1,
      maxPolls: value.marble.maxPolls as 0 | 180,
      maxDownloads: value.marble.maxDownloads as 0 | 2 | 3,
      creditLimit: value.marble.creditLimit as 0 | 1600,
      usdLimitCents: value.marble.usdLimitCents as 0 | 150,
    }),
    meshy: Object.freeze({
      model: "meshy-6",
      briefs: Object.freeze(
        briefs as readonly Readonly<{
          id: string;
          kind: "prop" | "character-placeholder";
          prompt: string;
        }>[],
      ),
      maxTasks: value.meshy.maxTasks,
      creditLimit: value.meshy.creditLimit,
    }),
    approvalHash: value.approvalHash,
    approved: value.approved,
  });
}

function parseQualificationEvidence(value: unknown): PrototypeQualificationEvidence | null {
  if (!exactKeys(value, ["runId", "attempt", "replayCount", "screenshotCount", "videoCount", "sampleCount",
    "medianFrameMicros", "medianFpsMilli"]) || typeof value.runId !== "string" || !QUALIFICATION_RUN_ID.test(value.runId) ||
      typeof value.attempt !== "number" || ![0, 1, 2].includes(value.attempt) || typeof value.replayCount !== "number" ||
      !Number.isSafeInteger(value.replayCount) || value.replayCount < 1 || value.replayCount > 32 ||
      typeof value.screenshotCount !== "number" || !Number.isSafeInteger(value.screenshotCount) ||
      value.screenshotCount < value.replayCount || value.screenshotCount > 512 || value.videoCount !== 1 || value.sampleCount !== 300 ||
      typeof value.medianFrameMicros !== "number" || !Number.isSafeInteger(value.medianFrameMicros) || value.medianFrameMicros < 1 || value.medianFrameMicros > 10_000_000 ||
      typeof value.medianFpsMilli !== "number" || !Number.isSafeInteger(value.medianFpsMilli) || value.medianFpsMilli < 30_000 || value.medianFpsMilli > 1_000_000 ||
      value.medianFpsMilli !== Math.floor(1_000_000_000 / value.medianFrameMicros)) return null;
  return Object.freeze({
    runId: value.runId,
    attempt: value.attempt as 0 | 1 | 2,
    replayCount: value.replayCount,
    screenshotCount: value.screenshotCount,
    videoCount: 1,
    sampleCount: 300,
    medianFrameMicros: value.medianFrameMicros,
    medianFpsMilli: value.medianFpsMilli,
  });
}

function parseQualification(value: unknown): PrototypeQualification | null {
  if (!exactKeys(value, ["profile", "cacheLevel", "subphase", "attempt", "reusedQualification", "solutionSha256", "evidence"]) ||
      value.profile !== "matrix-oasis.creator-solved-evidence/1" ||
      ![null, "qualified", "evidence-only", "solved-only", "source-only"].includes(value.cacheLevel as string | null) ||
      ![null, "analyzing", "solving", "verifying", "evidencing"].includes(value.subphase as string | null) ||
      typeof value.attempt !== "number" || ![0, 1, 2].includes(value.attempt) || typeof value.reusedQualification !== "boolean" ||
      (value.solutionSha256 !== null && (typeof value.solutionSha256 !== "string" || !HASH.test(value.solutionSha256)))) return null;
  const evidence = value.evidence === null ? null : parseQualificationEvidence(value.evidence);
  if (value.evidence !== null && evidence === null) return null;
  return Object.freeze({ ...value, evidence }) as PrototypeQualification;
}

function parseRun(value: unknown, r16 = false): PrototypeRun | null {
  const keys = ["id", "status", "cacheHit", "diagnostics", "modelApproval", "assetApproval", "resultRunId"];
  if (r16) keys.push("qualification");
  if (
    !exactKeys(value, keys) ||
    typeof value.id !== "string" ||
    !RUN_ID.test(value.id) ||
    typeof value.status !== "string" ||
    !RUN_STATES.has(value.status) ||
    typeof value.cacheHit !== "boolean" ||
    (value.resultRunId !== null &&
      (typeof value.resultRunId !== "string" ||
        !(r16 ? QUALIFICATION_RUN_ID : RESULT_RUN_ID).test(value.resultRunId)))
  ) {
    return null;
  }
  const safeDiagnostics = diagnostics(value.diagnostics);
  const modelApproval =
    value.modelApproval === null
      ? null
      : parseModelApproval(value.modelApproval);
  const assetApproval =
    value.assetApproval === null
      ? null
      : parseAssetApproval(value.assetApproval);
  const qualification = r16
    ? (value.qualification === null ? null : parseQualification(value.qualification))
    : undefined;
  if (
    safeDiagnostics === null ||
    (value.modelApproval !== null && modelApproval === null) ||
    (value.assetApproval !== null && assetApproval === null) ||
    (r16 && qualification === null) ||
    (r16 && value.status === "ready" && (value.resultRunId === null || qualification?.solutionSha256 === null ||
      qualification?.evidence === null || qualification?.subphase !== null || qualification?.attempt !== qualification?.evidence?.attempt)) ||
    (r16 && value.status === "qualifying" && (value.resultRunId !== null || qualification?.cacheLevel === null ||
      qualification?.evidence !== null || qualification?.solutionSha256 !== null)) ||
    (r16 && qualification?.reusedQualification === true &&
      (value.status !== "ready" || qualification.cacheLevel !== "qualified" || value.cacheHit !== true))
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    status: value.status as PrototypeRunStatus,
    cacheHit: value.cacheHit,
    diagnostics: safeDiagnostics,
    modelApproval,
    assetApproval,
    resultRunId: value.resultRunId as string | null,
    ...(r16 ? { qualification } : {}),
  });
}

function parseBootstrap(value: unknown): PrototypeBootstrap | null {
  const r16 = isRecord(value) && value.marker === "MATRIX_OASIS_R16_PROTOTYPE_HOST";
  if (r16) {
    if (!exactKeys(value, ["marker", "readiness", "currentRunId", "runs", "qualificationProfile"]) ||
        value.qualificationProfile !== "matrix-oasis.creator-solved-evidence/1" ||
        !exactKeys(value.readiness, ["model", "assets", "godot"]) ||
        [value.readiness.model, value.readiness.assets, value.readiness.godot].some((ready) => typeof ready !== "boolean") ||
        (value.currentRunId !== null && (typeof value.currentRunId !== "string" || !QUALIFICATION_RUN_ID.test(value.currentRunId))) ||
        !Array.isArray(value.runs) || value.runs.length > 100) return null;
    const runs = value.runs.map((run) => parseRun(run, true));
    if (runs.some((run) => run === null)) return null;
    if (value.currentRunId !== null && !(runs as PrototypeRun[]).some((run) =>
      run.status === "ready" && run.resultRunId === value.currentRunId)) return null;
    return Object.freeze({ marker: "MATRIX_OASIS_R16_PROTOTYPE_HOST",
      readiness: Object.freeze({ model: value.readiness.model as boolean, assets: value.readiness.assets as boolean,
        godot: value.readiness.godot as boolean }), currentRunId: value.currentRunId as string | null,
      runs: Object.freeze(runs as PrototypeRun[]), qualificationProfile: "matrix-oasis.creator-solved-evidence/1" });
  }
  const bootstrapKeys = ["marker", "readiness", "currentRunId", "runs"];
  if (isRecord(value) && Object.hasOwn(value, "recovery")) bootstrapKeys.push("recovery");
  if (isRecord(value) && Object.hasOwn(value, "worldDiscovery")) bootstrapKeys.push("worldDiscovery");
  if (
    !exactKeys(value, bootstrapKeys) ||
    value.marker !== "MATRIX_OASIS_R10_PROTOTYPE_HOST" ||
    !exactKeys(value.readiness, ["model", "assets", "godot"]) ||
    [
      value.readiness.model,
      value.readiness.assets,
      value.readiness.godot,
    ].some((ready) => typeof ready !== "boolean") ||
    (value.currentRunId !== null &&
      (typeof value.currentRunId !== "string" ||
        !RESULT_RUN_ID.test(value.currentRunId))) ||
    !Array.isArray(value.runs) ||
    value.runs.length > 100
  ) {
    return null;
  }
  const runs = value.runs.map((run) => parseRun(run, false));
  if (runs.some((run) => run === null)) {
    return null;
  }
  const recovery = Object.hasOwn(value, "recovery") && value.recovery !== null
    ? parseRecovery(value.recovery)
    : null;
  if (Object.hasOwn(value, "recovery") && value.recovery !== null && recovery === null) return null;
  const worldDiscovery = Object.hasOwn(value, "worldDiscovery") && value.worldDiscovery !== null
    ? parseWorldDiscovery(value.worldDiscovery)
    : null;
  if (Object.hasOwn(value, "worldDiscovery") && value.worldDiscovery !== null && worldDiscovery === null) return null;
  return Object.freeze({
    marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
    readiness: Object.freeze({
      model: value.readiness.model as boolean,
      assets: value.readiness.assets as boolean,
      godot: value.readiness.godot as boolean,
    }),
    currentRunId: value.currentRunId as string | null,
    runs: Object.freeze(runs as PrototypeRun[]),
    ...(Object.hasOwn(value, "recovery") ? { recovery } : {}),
    ...(Object.hasOwn(value, "worldDiscovery") ? { worldDiscovery } : {}),
  });
}

function parseRecovery(value: unknown): PrototypeRecovery | null {
  const scopeValid = isRecord(value) && ((value.maxWorldGets === 1 && value.maxDownloads === 3) ||
    (value.maxWorldGets === 0 && value.maxDownloads === 0));
  if (!exactKeys(value, ["model", "worldIdSha256", "maxCreates", "maxPolls",
    "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents", "status", "diagnostics", "approvalHash", "approved"]) ||
      value.model !== "marble-1.1" || typeof value.worldIdSha256 !== "string" || !HASH.test(value.worldIdSha256) ||
      value.maxCreates !== 0 || value.maxPolls !== 0 || !scopeValid || value.creditLimit !== 0 ||
      value.usdLimitCents !== 0 || !["awaiting_approval", "recovering", "ready", "failed"].includes(String(value.status)) ||
      typeof value.approvalHash !== "string" || !HASH.test(value.approvalHash) || typeof value.approved !== "boolean") return null;
  const safeDiagnostics = diagnostics(value.diagnostics);
  if (safeDiagnostics === null) return null;
  return Object.freeze({
    model: "marble-1.1", worldIdSha256: value.worldIdSha256,
    maxCreates: 0, maxPolls: 0,
    maxWorldGets: value.maxWorldGets as 0 | 1,
    maxDownloads: value.maxDownloads as 0 | 3,
    creditLimit: 0, usdLimitCents: 0,
    status: value.status as PrototypeRecovery["status"], diagnostics: safeDiagnostics,
    approvalHash: value.approvalHash, approved: value.approved,
  });
}

function parseWorldDiscovery(value: unknown): PrototypeWorldDiscovery | null {
  if (!exactKeys(value, ["provider", "operation", "model", "pageSize", "status", "sortBy", "maxRequests",
    "maxCreates", "maxPolls", "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents", "statusState",
    "diagnostics", "candidates", "recovery", "approvalHash", "approved"]) || value.provider !== "world-labs-marble" ||
      value.operation !== "worlds:list" || value.model !== "marble-1.1" || value.pageSize !== 100 || value.status !== "SUCCEEDED" ||
      value.sortBy !== "created_at" || value.maxRequests !== 1 || value.maxCreates !== 0 || value.maxPolls !== 0 ||
      value.maxWorldGets !== 0 || value.maxDownloads !== 0 || value.creditLimit !== 0 || value.usdLimitCents !== 0 ||
      !["awaiting_approval", "querying", "ready", "failed"].includes(String(value.statusState)) ||
      typeof value.approvalHash !== "string" || !HASH.test(value.approvalHash) || typeof value.approved !== "boolean" ||
      !Array.isArray(value.candidates) || value.candidates.length > 100) return null;
  const safeDiagnostics = diagnostics(value.diagnostics);
  if (safeDiagnostics === null) return null;
  const recovery = value.recovery === null ? null : parseWorldDiscoveryRecovery(value.recovery);
  if (value.recovery !== null && recovery === null) return null;
  const candidates: PrototypeWorldDiscoveryCandidate[] = [];
  for (const candidate of value.candidates) {
    if (!exactKeys(candidate, ["worldIdSha256", "promptSha256", "createdAt", "updatedAt", "model", "assets"]) ||
        typeof candidate.worldIdSha256 !== "string" || !HASH.test(candidate.worldIdSha256) ||
        typeof candidate.promptSha256 !== "string" || !HASH.test(candidate.promptSha256) || candidate.model !== "marble-1.1" ||
        typeof candidate.createdAt !== "string" || !Number.isFinite(Date.parse(candidate.createdAt)) ||
        typeof candidate.updatedAt !== "string" || !Number.isFinite(Date.parse(candidate.updatedAt)) ||
        !exactKeys(candidate.assets, ["panorama", "collider", "spatialSource"]) ||
        [candidate.assets.panorama, candidate.assets.collider, candidate.assets.spatialSource].some((entry) => typeof entry !== "boolean")) return null;
    candidates.push(Object.freeze({ ...candidate, assets: Object.freeze({ ...candidate.assets }) }) as PrototypeWorldDiscoveryCandidate);
  }
  return Object.freeze({
    provider: "world-labs-marble", operation: "worlds:list", model: "marble-1.1", pageSize: 100, status: "SUCCEEDED",
    sortBy: "created_at", maxRequests: 1, maxCreates: 0, maxPolls: 0, maxWorldGets: 0, maxDownloads: 0,
    creditLimit: 0, usdLimitCents: 0,
    statusState: value.statusState as PrototypeWorldDiscovery["statusState"], diagnostics: safeDiagnostics,
    candidates: Object.freeze(candidates), recovery, approvalHash: value.approvalHash, approved: value.approved,
  });
}

function parseWorldDiscoveryRecovery(value: unknown): PrototypeWorldDiscoveryRecovery | null {
  if (!exactKeys(value, ["worldIdSha256", "maxCreates", "maxPolls", "maxWorldGets", "maxDownloads", "creditLimit",
    "usdLimitCents", "status", "diagnostics", "approvalHash", "approved"]) || typeof value.worldIdSha256 !== "string" ||
      !HASH.test(value.worldIdSha256) || value.maxCreates !== 0 || value.maxPolls !== 0 || value.maxWorldGets !== 1 ||
      value.maxDownloads !== 3 || value.creditLimit !== 0 || value.usdLimitCents !== 0 ||
      !["awaiting_approval", "recovering", "ready", "failed"].includes(String(value.status)) ||
      typeof value.approvalHash !== "string" || !HASH.test(value.approvalHash) || typeof value.approved !== "boolean") return null;
  const safeDiagnostics = diagnostics(value.diagnostics);
  if (safeDiagnostics === null) return null;
  return Object.freeze({ worldIdSha256: value.worldIdSha256, maxCreates: 0, maxPolls: 0, maxWorldGets: 1,
    maxDownloads: 3, creditLimit: 0, usdLimitCents: 0,
    status: value.status as PrototypeWorldDiscoveryRecovery["status"], diagnostics: safeDiagnostics,
    approvalHash: value.approvalHash, approved: value.approved });
}

async function responseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type");
  if (!contentType?.toLowerCase().startsWith("application/json")) {
    throw new PrototypeBuilderClientError();
  }
  const text = await response.text();
  if (
    text.length < 1 ||
    new TextEncoder().encode(text).byteLength > RESPONSE_LIMIT
  ) {
    throw new PrototypeBuilderClientError();
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new PrototypeBuilderClientError();
  }
}

function failureCode(value: unknown): string {
  if (!exactKeys(value, ["ok", "diagnostics"]) || value.ok !== false) {
    return "PROTOTYPE_BUILDER_CLIENT_ERROR";
  }
  const safe = diagnostics(value.diagnostics);
  return safe?.[0]?.code ?? "PROTOTYPE_BUILDER_CLIENT_ERROR";
}

export class PrototypeBuilderClient {
  readonly #fetch: FetchLike;

  constructor(fetchImplementation: FetchLike = globalThis.fetch.bind(globalThis)) {
    if (typeof fetchImplementation !== "function") {
      throw new PrototypeBuilderClientError();
    }
    this.#fetch = fetchImplementation;
  }

  async #request(path: string, method: "GET" | "POST", body?: unknown) {
    if (!API_PATH.test(path)) {
      throw new PrototypeBuilderClientError();
    }
    const response = await this.#fetch(path, {
      method,
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      ...(method === "POST"
        ? {
            headers: { "content-type": "application/json; charset=utf-8" },
            body: JSON.stringify(body),
          }
        : {}),
    });
    const value = await responseJson(response);
    if (!response.ok) {
      throw new PrototypeBuilderClientError(failureCode(value));
    }
    return value;
  }

  async bootstrap(): Promise<PrototypeBootstrap> {
    const value = parseBootstrap(
      await this.#request("/api/bootstrap", "GET"),
    );
    if (!value) {
      throw new PrototypeBuilderClientError();
    }
    return value;
  }

  async createRun(prompt: string): Promise<PrototypeRun> {
    return this.#runResponse(
      await this.#request("/api/runs", "POST", { prompt }),
    );
  }

  async getRun(id: string): Promise<PrototypeRun> {
    if (!RUN_ID.test(id)) {
      throw new PrototypeBuilderClientError();
    }
    return this.#runResponse(
      await this.#request(`/api/runs/${id}`, "GET"),
    );
  }

  async approveModel(run: PrototypeRun): Promise<PrototypeRun> {
    if (!run.modelApproval || !RUN_ID.test(run.id)) {
      throw new PrototypeBuilderClientError();
    }
    return this.#runResponse(
      await this.#request(`/api/runs/${run.id}/approve-model`, "POST", {
        approvalHash: run.modelApproval.approvalHash,
      }),
    );
  }

  async approveAssets(run: PrototypeRun): Promise<PrototypeRun> {
    if (!run.assetApproval || !RUN_ID.test(run.id)) {
      throw new PrototypeBuilderClientError();
    }
    return this.#runResponse(
      await this.#request(`/api/runs/${run.id}/approve-assets`, "POST", {
        approvalHash: run.assetApproval.approvalHash,
      }),
    );
  }

  async approveRecovery(recovery: PrototypeRecovery): Promise<PrototypeRecovery> {
    const value = await this.#request("/api/recovery/approve", "POST", { approvalHash: recovery.approvalHash });
    if (!exactKeys(value, ["ok", "recovery"]) || value.ok !== true) throw new PrototypeBuilderClientError();
    const parsed = parseRecovery(value.recovery);
    if (!parsed) throw new PrototypeBuilderClientError();
    return parsed;
  }

  async approveWorldDiscovery(discovery: PrototypeWorldDiscovery): Promise<PrototypeWorldDiscovery> {
    const value = await this.#request("/api/world-discovery/approve", "POST", { approvalHash: discovery.approvalHash });
    if (!exactKeys(value, ["ok", "worldDiscovery"]) || value.ok !== true) throw new PrototypeBuilderClientError();
    const parsed = parseWorldDiscovery(value.worldDiscovery);
    if (!parsed) throw new PrototypeBuilderClientError();
    return parsed;
  }

  async prepareWorldRecovery(candidate: PrototypeWorldDiscoveryCandidate): Promise<PrototypeWorldDiscovery> {
    const value = await this.#request("/api/world-discovery/prepare-recovery", "POST", { worldIdSha256: candidate.worldIdSha256 });
    if (!exactKeys(value, ["ok", "worldDiscovery"]) || value.ok !== true) throw new PrototypeBuilderClientError();
    const parsed = parseWorldDiscovery(value.worldDiscovery);
    if (!parsed) throw new PrototypeBuilderClientError();
    return parsed;
  }

  async approveWorldRecovery(discovery: PrototypeWorldDiscovery): Promise<PrototypeWorldDiscovery> {
    if (discovery.recovery === null) throw new PrototypeBuilderClientError();
    const value = await this.#request("/api/world-discovery/approve-recovery", "POST", { approvalHash: discovery.recovery.approvalHash });
    if (!exactKeys(value, ["ok", "worldDiscovery"]) || value.ok !== true) throw new PrototypeBuilderClientError();
    const parsed = parseWorldDiscovery(value.worldDiscovery);
    if (!parsed) throw new PrototypeBuilderClientError();
    return parsed;
  }

  async launch(run: PrototypeRun): Promise<string> {
    if (!RUN_ID.test(run.id)) {
      throw new PrototypeBuilderClientError();
    }
    const value = await this.#request(
      `/api/runs/${run.id}/launch`,
      "POST",
      {},
    );
    if (
      !exactKeys(value, ["ok", "runId"]) ||
      value.ok !== true ||
      typeof value.runId !== "string" ||
      !(run.qualification === undefined ? RESULT_RUN_ID : QUALIFICATION_RUN_ID).test(value.runId)
    ) {
      throw new PrototypeBuilderClientError();
    }
    return value.runId;
  }

  #runResponse(value: unknown): PrototypeRun {
    if (!exactKeys(value, ["ok", "run"]) || value.ok !== true) {
      throw new PrototypeBuilderClientError();
    }
    const run = parseRun(value.run, isRecord(value.run) && Object.hasOwn(value.run, "qualification"));
    if (!run) {
      throw new PrototypeBuilderClientError();
    }
    return run;
  }
}
