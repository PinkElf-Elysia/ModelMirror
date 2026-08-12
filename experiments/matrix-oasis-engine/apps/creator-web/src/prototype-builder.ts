export const PROTOTYPE_BUILDER_MARKER =
  "MATRIX_OASIS_R10_PROTOTYPE_BUILDER";

export const PROTOTYPE_RUN_STATES = Object.freeze([
  "awaiting_model_approval",
  "generating",
  "awaiting_asset_approval",
  "acquiring",
  "normalizing",
  "assembling",
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
    maxCreates: 1;
    maxPolls: 180;
    maxDownloads: 2;
    creditLimit: 1600;
    usdLimitCents: 150;
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
}

export interface PrototypeBootstrap {
  readonly marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST";
  readonly readiness: Readonly<{
    model: boolean;
    assets: boolean;
    godot: boolean;
  }>;
  readonly currentRunId: string | null;
  readonly runs: readonly PrototypeRun[];
}

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const RUN_ID = /^r10-run-[1-9][0-9]*$/u;
const RESULT_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
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
      "maxCreates",
      "maxPolls",
      "maxDownloads",
      "creditLimit",
      "usdLimitCents",
    ]) ||
    value.marble.model !== "marble-1.1" ||
    !safeText(value.marble.environmentPrompt) ||
    value.marble.maxCreates !== 1 ||
    value.marble.maxPolls !== 180 ||
    value.marble.maxDownloads !== 2 ||
    value.marble.creditLimit !== 1600 ||
    value.marble.usdLimitCents !== 150 ||
    !exactKeys(value.meshy, ["model", "briefs", "maxTasks", "creditLimit"]) ||
    value.meshy.model !== "meshy-6" ||
    !Array.isArray(value.meshy.briefs) ||
    value.meshy.briefs.length > 2 ||
    value.meshy.maxTasks !== value.meshy.briefs.length * 2 ||
    value.meshy.creditLimit !== value.meshy.briefs.length * 30
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
      maxCreates: 1,
      maxPolls: 180,
      maxDownloads: 2,
      creditLimit: 1600,
      usdLimitCents: 150,
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

function parseRun(value: unknown): PrototypeRun | null {
  if (
    !exactKeys(value, [
      "id",
      "status",
      "cacheHit",
      "diagnostics",
      "modelApproval",
      "assetApproval",
      "resultRunId",
    ]) ||
    typeof value.id !== "string" ||
    !RUN_ID.test(value.id) ||
    typeof value.status !== "string" ||
    !RUN_STATES.has(value.status) ||
    typeof value.cacheHit !== "boolean" ||
    (value.resultRunId !== null &&
      (typeof value.resultRunId !== "string" ||
        !RESULT_RUN_ID.test(value.resultRunId)))
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
  if (
    safeDiagnostics === null ||
    (value.modelApproval !== null && modelApproval === null) ||
    (value.assetApproval !== null && assetApproval === null)
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
  });
}

function parseBootstrap(value: unknown): PrototypeBootstrap | null {
  if (
    !exactKeys(value, ["marker", "readiness", "currentRunId", "runs"]) ||
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
  const runs = value.runs.map(parseRun);
  if (runs.some((run) => run === null)) {
    return null;
  }
  return Object.freeze({
    marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
    readiness: Object.freeze({
      model: value.readiness.model as boolean,
      assets: value.readiness.assets as boolean,
      godot: value.readiness.godot as boolean,
    }),
    currentRunId: value.currentRunId as string | null,
    runs: Object.freeze(runs as PrototypeRun[]),
  });
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
      !RESULT_RUN_ID.test(value.runId)
    ) {
      throw new PrototypeBuilderClientError();
    }
    return value.runId;
  }

  #runResponse(value: unknown): PrototypeRun {
    if (!exactKeys(value, ["ok", "run"]) || value.ok !== true) {
      throw new PrototypeBuilderClientError();
    }
    const run = parseRun(value.run);
    if (!run) {
      throw new PrototypeBuilderClientError();
    }
    return run;
  }
}
