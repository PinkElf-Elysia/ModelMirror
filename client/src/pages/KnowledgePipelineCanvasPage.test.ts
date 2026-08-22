import { describe, expect, it } from "vitest";
import {
  fullChainDiagnosticPayload,
  safeEvidenceProbeReceipt,
} from "./KnowledgePipelineCanvasPage";

describe("Knowledge pipeline evidence probe receipt", () => {
  it("exposes only bounded timing and request-envelope fields", () => {
    expect(
      safeEvidenceProbeReceipt({
        retrieval_elapsed_ms: 1480.4567,
        embedding_elapsed_ms: 0,
        rerank_elapsed_ms: 1375.1254,
        rerank_input_count: 5,
        rerank_input_char_count: 4096,
        rerank_max_output_tokens: 300,
        rerank_timeout_budget_ms: 5000,
        question: "private query",
        matched_text: "private source text",
        api_key: "sk-secret",
      }),
    ).toEqual({
      retrievalElapsedMs: 1480.457,
      embeddingElapsedMs: 0,
      verifierElapsedMs: 1375.125,
      verifierInputCount: 5,
      verifierInputChars: 4096,
      verifierMaxOutputTokens: 300,
      verifierTimeoutBudgetMs: 5000,
    });
  });

  it("builds a full-chain request without retrieval overrides or answers", () => {
    expect(fullChainDiagnosticPayload("  Is MM-2042 supported?  ")).toEqual({
      question: "Is MM-2042 supported?",
      generate_answer: false,
      probe_mode: "full_chain_diagnostic",
    });
  });
});
