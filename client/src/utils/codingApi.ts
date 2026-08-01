import type {
  CodingCancelResponse,
  CodingApplyResult,
  CodingCapabilities,
  CodingCommitResult,
  CodingDraftChanges,
  CodingEvent,
  CodingPatchDownload,
  CodingRecoveryResumeResponse,
  CodingRecoveryStatus,
  CodingSessionResponse,
  CodingTurnResponse,
  CodingVerification,
  CodingVerificationCancelResponse,
} from "../types/coding";

export class CodingApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, status: number, code = "request_failed") {
    super(message);
    this.name = "CodingApiError";
    this.status = status;
    this.code = code;
  }
}

async function requestJson<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  const payload = (await response.json().catch(() => null)) as
    | { detail?: { code?: string } }
    | null;
  if (!response.ok) {
    throw new CodingApiError(
      "Coding Agent 请求失败",
      response.status,
      payload?.detail?.code,
    );
  }
  return payload as T;
}

async function requestText(url: string): Promise<string> {
  const response = await fetch(url, {
    headers: { Accept: "text/x-diff" },
  });
  if (!response.ok) {
    await throwResponseError(response);
  }
  return response.text();
}

async function throwResponseError(response: Response): Promise<never> {
  const payload = (await response.json().catch(() => null)) as
    | { detail?: { code?: string } }
    | null;
  throw new CodingApiError(
    "代码助手请求失败",
    response.status,
    payload?.detail?.code,
  );
}

export function getCodingCapabilities() {
  return requestJson<CodingCapabilities>("/api/coding/capabilities");
}

export function createCodingSession() {
  return requestJson<CodingSessionResponse>("/api/coding/sessions", {
    method: "POST",
  });
}

export function getCodingRecovery() {
  return requestJson<CodingRecoveryStatus>("/api/coding/recovery");
}

export function resumeCodingRecovery() {
  return requestJson<CodingRecoveryResumeResponse>(
    "/api/coding/recovery/resume",
    { method: "POST" },
  );
}

export function discardCodingRecovery() {
  return requestJson<{ discarded: true }>(
    "/api/coding/recovery/discard",
    { method: "POST" },
  );
}

export async function getCodingRecoveryPatch(): Promise<CodingPatchDownload> {
  const response = await fetch("/api/coding/recovery/patch", {
    headers: { Accept: "text/x-diff" },
  });
  if (!response.ok) {
    await throwResponseError(response);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename =
    disposition.match(/filename="([^"]+)"/)?.[1] ??
    "modelmirror-recovered.patch";
  return { blob: await response.blob(), filename };
}

export function getCodingSessionStatus(sessionId: string) {
  return requestJson<{ state: string }>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export function startCodingTurn(sessionId: string, prompt: string) {
  return requestJson<CodingTurnResponse>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/turns`,
    {
      method: "POST",
      body: JSON.stringify({ prompt }),
    },
  );
}

export function cancelCodingTurn(sessionId: string) {
  return requestJson<CodingCancelResponse>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST" },
  );
}

export function getCodingChanges(sessionId: string) {
  return requestJson<CodingDraftChanges>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/changes`,
  );
}

export function getCodingDiff(
  sessionId: string,
  path: string,
  revision: number,
) {
  const query = new URLSearchParams({
    path,
    revision: String(revision),
  });
  return requestText(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/diff?${query}`,
  );
}

export function validateCodingChanges(sessionId: string) {
  return requestJson<CodingDraftChanges>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/validate`,
    { method: "POST" },
  );
}

export function discardCodingChanges(sessionId: string) {
  return requestJson<CodingDraftChanges>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/discard`,
    { method: "POST" },
  );
}

export async function getCodingPatch(
  sessionId: string,
  revision: number,
): Promise<CodingPatchDownload> {
  const response = await fetch(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/patch?revision=${revision}`,
    { headers: { Accept: "text/x-diff" } },
  );
  if (!response.ok) {
    await throwResponseError(response);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename =
    disposition.match(/filename="([^"]+)"/)?.[1] ??
    `modelmirror-changes-r${revision}.patch`;
  return { blob: await response.blob(), filename };
}

export function startCodingVerification(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingVerification>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/verification`,
    {
      method: "POST",
      body: JSON.stringify({ revision }),
    },
  );
}

export function getCodingVerification(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingVerification>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/verification?revision=${revision}`,
  );
}

export function cancelCodingVerification(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingVerificationCancelResponse>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/verification/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ revision }),
    },
  );
}

export function applyCodingChanges(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingApplyResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/apply`,
    {
      method: "POST",
      body: JSON.stringify({ revision }),
    },
  );
}

export function getCodingApplyStatus(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingApplyResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/apply?revision=${revision}`,
  );
}

export function revertCodingApply(
  sessionId: string,
  revision: number,
  applyId: string,
) {
  return requestJson<CodingApplyResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/apply/revert`,
    {
      method: "POST",
      body: JSON.stringify({ revision, apply_id: applyId }),
    },
  );
}

export function commitCodingChanges(
  sessionId: string,
  revision: number,
  applyId: string,
  message: string,
) {
  return requestJson<CodingCommitResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/commit`,
    {
      method: "POST",
      body: JSON.stringify({ revision, apply_id: applyId, message }),
    },
  );
}

export function getCodingCommitStatus(
  sessionId: string,
  revision: number,
) {
  return requestJson<CodingCommitResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/commit?revision=${revision}`,
  );
}

export function undoCodingCommit(
  sessionId: string,
  revision: number,
  applyId: string,
  commitId: string,
) {
  return requestJson<CodingCommitResult>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/commit/undo`,
    {
      method: "POST",
      body: JSON.stringify({
        revision,
        apply_id: applyId,
        commit_id: commitId,
      }),
    },
  );
}

export function closeCodingSession(sessionId: string) {
  return requestJson<{ closed: true }>(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/close`,
    { method: "POST" },
  );
}

interface CodingEventHandlers {
  onEvent: (event: CodingEvent) => void;
  onTransportError: () => void;
}

const terminalTypes = new Set(["turn_completed", "failed", "cancelled"]);

export function connectCodingEvents(
  sessionId: string,
  after: number,
  handlers: CodingEventHandlers,
) {
  const source = new EventSource(
    `/api/coding/sessions/${encodeURIComponent(sessionId)}/events?after=${after}`,
  );
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as CodingEvent;
      if (
        !Number.isFinite(event.seq) ||
        typeof event.type !== "string" ||
        typeof event.data !== "object"
      ) {
        throw new Error("Invalid CodingEvent");
      }
      handlers.onEvent(event);
      if (terminalTypes.has(event.type)) {
        source.close();
      }
    } catch {
      source.close();
      handlers.onTransportError();
    }
  };
  source.onerror = () => {
    handlers.onTransportError();
  };
  return () => source.close();
}
