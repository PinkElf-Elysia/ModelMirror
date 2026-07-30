import type {
  CodingCancelResponse,
  CodingCapabilities,
  CodingEvent,
  CodingSessionResponse,
  CodingTurnResponse,
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

export function getCodingCapabilities() {
  return requestJson<CodingCapabilities>("/api/coding/capabilities");
}

export function createCodingSession() {
  return requestJson<CodingSessionResponse>("/api/coding/sessions", {
    method: "POST",
  });
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
