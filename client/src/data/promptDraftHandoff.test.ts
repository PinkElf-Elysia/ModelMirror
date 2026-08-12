import { describe, expect, it } from "vitest";
import {
  PROMPT_DRAFT_TTL_MS,
  consumePromptDraftHandoff,
  createPromptDraftHandoff,
} from "./promptDraftHandoff";

describe("PromptDraftHandoffV1", () => {
  it("is consumed once for the exact target model", () => {
    const draft = createPromptDraftHandoff(sessionStorage, {
      templateId: "template-1",
      targetModelId: "openai/test",
      content: "只填入，不发送",
    }, 100);
    expect(consumePromptDraftHandoff(sessionStorage, draft.nonce, "openai/test", 200)?.content).toBe("只填入，不发送");
    expect(consumePromptDraftHandoff(sessionStorage, draft.nonce, "openai/test", 200)).toBeNull();
  });

  it("fails closed for wrong targets and expired drafts", () => {
    const wrongTarget = createPromptDraftHandoff(sessionStorage, { templateId: "a", targetModelId: "model-a", content: "a" }, 0);
    expect(consumePromptDraftHandoff(sessionStorage, wrongTarget.nonce, "model-b", 1)).toBeNull();
    const expired = createPromptDraftHandoff(sessionStorage, { templateId: "b", targetModelId: "model-a", content: "b" }, 0);
    expect(consumePromptDraftHandoff(sessionStorage, expired.nonce, "model-a", PROMPT_DRAFT_TTL_MS + 1)).toBeNull();
  });
});
