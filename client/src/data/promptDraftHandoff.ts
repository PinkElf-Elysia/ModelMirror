export const PROMPT_DRAFT_HANDOFF_VERSION = 1 as const;
export const PROMPT_DRAFT_TTL_MS = 10 * 60 * 1000;

const STORAGE_PREFIX = "modelmirror.prompt-draft.v1.";

export interface PromptDraftHandoffV1 {
  version: typeof PROMPT_DRAFT_HANDOFF_VERSION;
  nonce: string;
  templateId: string;
  targetModelId: string;
  content: string;
  createdAt: number;
  expiresAt: number;
}

function randomNonce() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function storageKey(nonce: string) {
  return `${STORAGE_PREFIX}${nonce}`;
}

export function createPromptDraftHandoff(
  storage: Storage,
  input: { templateId: string; targetModelId: string; content: string },
  now = Date.now(),
) {
  const handoff: PromptDraftHandoffV1 = {
    version: PROMPT_DRAFT_HANDOFF_VERSION,
    nonce: randomNonce(),
    templateId: input.templateId.slice(0, 200),
    targetModelId: input.targetModelId.slice(0, 300),
    content: input.content.slice(0, 100_000),
    createdAt: now,
    expiresAt: now + PROMPT_DRAFT_TTL_MS,
  };
  storage.setItem(storageKey(handoff.nonce), JSON.stringify(handoff));
  return handoff;
}

export function consumePromptDraftHandoff(
  storage: Storage,
  nonce: string,
  targetModelId: string,
  now = Date.now(),
) {
  if (!/^[A-Za-z0-9-]{16,80}$/.test(nonce)) return null;
  const key = storageKey(nonce);
  const raw = storage.getItem(key);
  storage.removeItem(key);
  if (!raw) return null;

  try {
    const value = JSON.parse(raw) as Partial<PromptDraftHandoffV1>;
    if (
      value.version !== PROMPT_DRAFT_HANDOFF_VERSION ||
      value.nonce !== nonce ||
      value.targetModelId !== targetModelId ||
      typeof value.content !== "string" ||
      value.content.length === 0 ||
      value.content.length > 100_000 ||
      typeof value.expiresAt !== "number" ||
      value.expiresAt < now
    ) {
      return null;
    }
    return value as PromptDraftHandoffV1;
  } catch {
    return null;
  }
}
