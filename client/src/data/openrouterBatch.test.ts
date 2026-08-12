import { describe, expect, it } from "vitest";
import { batchResultText, type OpenRouterBatchResult } from "./openrouterBatch";

function result(
  body: unknown,
  overrides: Partial<OpenRouterBatchResult> = {},
): OpenRouterBatchResult {
  return {
    id: "batch_req_1",
    custom_id: "request-1",
    response: { status_code: 200, body },
    error: null,
    ...overrides,
  };
}

describe("OpenRouter batch result presentation", () => {
  it("extracts chat completion text", () => {
    expect(
      batchResultText(
        result({ choices: [{ message: { content: "Batch finished" } }] }),
      ),
    ).toBe("Batch finished");
  });

  it("summarizes embeddings without rendering raw vectors", () => {
    expect(
      batchResultText(
        result({
          data: [
            { embedding: [0.1, 0.2, 0.3] },
            { embedding: [0.4, 0.5, 0.6] },
          ],
        }),
      ),
    ).toBe("向量生成完成，共 2 条，维度 3。");
  });

  it("surfaces per-request failures", () => {
    expect(
      batchResultText(
        result(null, {
          response: null,
          error: { message: "validation failed" },
        }),
      ),
    ).toBe("validation failed");
  });
});
