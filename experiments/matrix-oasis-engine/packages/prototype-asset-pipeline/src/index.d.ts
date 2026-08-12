export type MeshyProviderDiagnosticCode =
  | "MESHY_PROVIDER_REQUEST_INVALID"
  | "MESHY_PROVIDER_NETWORK_ERROR"
  | "MESHY_PROVIDER_TIMEOUT"
  | "MESHY_PROVIDER_REDIRECT"
  | "MESHY_PROVIDER_RATE_LIMITED"
  | "MESHY_PROVIDER_HTTP_ERROR"
  | "MESHY_PROVIDER_RESPONSE_TOO_LARGE"
  | "MESHY_PROVIDER_RESPONSE_INVALID"
  | "MESHY_PROVIDER_DOWNLOAD_URL_INVALID"
  | "MESHY_PROVIDER_DOWNLOAD_TOO_LARGE";

export interface MeshyProviderDiagnostic {
  readonly phase: "provider";
  readonly severity: "error";
  readonly code: MeshyProviderDiagnosticCode;
  readonly path: "";
  readonly message: MeshyProviderDiagnosticCode;
}

export interface MeshyProviderFailure {
  readonly ok: false;
  readonly diagnostics: readonly MeshyProviderDiagnostic[];
}

export interface MeshyTaskCreated {
  readonly ok: true;
  readonly taskId: string;
}

export interface MeshyTaskStatus {
  readonly ok: true;
  readonly task: Readonly<{
    status: "pending" | "succeeded" | "failed";
    progress: number;
    glbUrl: string | null;
    consumedCredits: number | null;
  }>;
}

export interface MeshyGlbDownload {
  readonly ok: true;
  readonly bytes: Uint8Array;
}

export interface MeshyTextTo3DProvider {
  readonly provider: "meshy";
  readonly model: "meshy-6";
  readonly createPreview: (
    request: Readonly<{ prompt: string }>,
  ) => Promise<MeshyTaskCreated | MeshyProviderFailure>;
  readonly createRefine: (
    request: Readonly<{ previewTaskId: string }>,
  ) => Promise<MeshyTaskCreated | MeshyProviderFailure>;
  readonly getTask: (
    request: Readonly<{ taskId: string }>,
  ) => Promise<MeshyTaskStatus | MeshyProviderFailure>;
  readonly downloadGlb: (
    request: Readonly<{ url: string }>,
  ) => Promise<MeshyGlbDownload | MeshyProviderFailure>;
}

export interface MeshyTextTo3DProviderConfig {
  readonly endpoint: string;
  readonly apiKey: string;
  readonly timeoutMs?: number;
}

export declare const MESHY_PROVIDER_ENDPOINT: string;
export declare const MESHY_PROVIDER_MODEL: "meshy-6";
export declare const MESHY_PROVIDER_LIMITS: Readonly<{
  timeoutMs: 120000;
  responseBytes: 1048576;
  rawGlbBytes: 134217728;
  promptCharacters: 600;
  taskIdCharacters: 128;
}>;

export declare class PrototypeAssetPipelineOperationalError extends Error {
  readonly code: "PROTOTYPE_ASSET_PIPELINE_INTERNAL_ERROR";
}

export declare function createMeshyTextTo3DProvider(
  config: MeshyTextTo3DProviderConfig,
): MeshyTextTo3DProvider;
