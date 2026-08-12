import { afterEach, describe, expect, it, vi } from "vitest";
import {
  saveSkillRerankFeedback,
  searchSkills,
  SkillRerankApiError,
} from "./skillRerankApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("skillRerankApi", () => {
  it("sends only the bounded search contract and preserves the receipt", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          lexicalResults: [],
          finalResults: [],
          status: "semantic",
          warnings: [],
          receipt: { queryHash: "a".repeat(64) },
          governanceRevision: 3,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await searchSkills("分析 PDF", true);
    expect(result.status).toBe("semantic");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/skills/search",
      expect.objectContaining({
        body: JSON.stringify({ query: "分析 PDF", limit: 6, semantic: true }),
      }),
    );
  });

  it("writes the raw query only after an explicit feedback action", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ feedback: { feedbackId: "f1" }, governanceRevision: 5 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await saveSkillRerankFeedback({
      expectedRevision: 4,
      query: "分析 PDF",
      candidateId: "catalog:project:pdf",
      candidateFingerprint: "b".repeat(64),
      judgment: "relevant",
      receipt: {
        queryHash: "a".repeat(64),
        candidateSetFingerprint: "c".repeat(64),
        candidateFingerprints: [],
        lexicalRanks: [],
        semanticRanks: [],
        proposedRanks: [],
        finalRanks: [],
        rankChanges: [],
        provider: "api",
        model: "reranker",
        strategyVersion: "v1",
        durationMs: 12,
        fallbackReason: null,
      },
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.query).toBe("分析 PDF");
    expect(body.judgment).toBe("relevant");
    expect(body.expected_revision).toBe(4);
  });

  it("keeps structured backend errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: { code: "skill_rerank_revision_conflict", message: "版本已变化" },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(searchSkills("分析 PDF", true)).rejects.toMatchObject({
      code: "skill_rerank_revision_conflict",
      status: 409,
    } satisfies Partial<SkillRerankApiError>);
  });
});
