import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { skillProjects } from "../data/skillProjects";
import type { SkillNeedProjectTarget } from "../data/skillNeedMatcher";
import SkillBrowserPage from "./SkillBrowserPage";

const mocks = vi.hoisted(() => ({
  findSkillsForNeed: vi.fn(),
  loadSkillNeedCandidates: vi.fn(),
  loadSkillTrustSummaryIndex: vi.fn(),
  saveSkillRerankFeedback: vi.fn(),
  searchSkills: vi.fn(),
  useSkillCreatorStatus: vi.fn(),
}));

vi.mock("../data/skillNeedCandidates", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillNeedCandidates")>()),
  loadSkillNeedCandidates: mocks.loadSkillNeedCandidates,
}));
vi.mock("../data/skillNeedMatcher", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillNeedMatcher")>()),
  findSkillsForNeed: mocks.findSkillsForNeed,
}));
vi.mock("../data/skillTrustIndex", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillTrustIndex")>()),
  loadSkillTrustSummaryIndex: mocks.loadSkillTrustSummaryIndex,
}));
vi.mock("../utils/skillRerankApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/skillRerankApi")>()),
  saveSkillRerankFeedback: mocks.saveSkillRerankFeedback,
  searchSkills: mocks.searchSkills,
}));
vi.mock("../hooks/useSkillCreatorStatus", () => ({
  useSkillCreatorStatus: mocks.useSkillCreatorStatus,
}));

const project = skillProjects.find(
  (item) => item.installMode === "direct" && item.installStatus === "ready",
)!;
const target: SkillNeedProjectTarget = {
  targetType: "project",
  project,
  id: project.id,
  name: project.name,
  category: project.category,
  kind: project.kind,
  description: project.description,
  tags: project.tags,
  installStatus: project.installStatus,
};
const candidateId = `catalog:project:${project.id}`;
const receipt = {
  queryHash: "a".repeat(64),
  candidateSetFingerprint: "b".repeat(64),
  candidateFingerprints: [
    { candidateId, candidateFingerprint: "c".repeat(64) },
  ],
  lexicalRanks: [candidateId],
  semanticRanks: [candidateId],
  proposedRanks: [candidateId],
  finalRanks: [candidateId],
  rankChanges: [],
  provider: "api",
  model: "reranker",
  strategyVersion: "skill-semantic-rrf-v1",
  durationMs: 12,
  fallbackReason: null,
};

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("SkillBrowser semantic reranking", () => {
  it("requires page-session disclosure and saves feedback only after a click", async () => {
    mocks.useSkillCreatorStatus.mockReturnValue({ status: { enabled: false } });
    mocks.loadSkillTrustSummaryIndex.mockResolvedValue(null);
    mocks.loadSkillNeedCandidates.mockResolvedValue([target]);
    mocks.searchSkills.mockResolvedValue({
      lexicalResults: [],
      finalResults: [
        {
          candidateId,
          candidateFingerprint: "c".repeat(64),
          name: project.name,
          summary: project.description,
          category: project.category,
          kind: project.kind,
          installStatus: "ready",
          score: 20,
          reasons: [
            { type: "name", label: "名称", origin: "direct", matchedTerms: ["pdf"] },
          ],
          lexicalRank: 3,
          semanticRank: 1,
          rankDelta: 2,
        },
      ],
      status: "semantic",
      warnings: [],
      receipt,
      governanceRevision: 7,
    });
    mocks.saveSkillRerankFeedback.mockResolvedValue({
      feedback: { feedbackId: "feedback_1" },
      governanceRevision: 8,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return new Response(
          JSON.stringify(url.endsWith("/installed") ? { skills: [] } : { skills: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const user = userEvent.setup();
    const rendered = render(
      <MemoryRouter>
        <SkillBrowserPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "开启" }));
    expect(screen.getByText(/最多 24 个公共目录候选/)).toBeInTheDocument();
    expect(mocks.searchSkills).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "开启语义重排" }));

    const query = screen.getByRole("textbox", { name: /描述你要完成的事/ });
    await user.type(query, "分析 PDF");
    await user.click(screen.getByRole("button", { name: "寻找合适的 Skill" }));
    expect(await screen.findByText("语义第 1 名")).toBeInTheDocument();
    expect(mocks.searchSkills).toHaveBeenCalledWith("分析 PDF", true);
    expect(mocks.saveSkillRerankFeedback).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "相关" }));
    await waitFor(() =>
      expect(mocks.saveSkillRerankFeedback).toHaveBeenCalledWith(
        expect.objectContaining({
          expectedRevision: 7,
          query: "分析 PDF",
          candidateId,
          judgment: "relevant",
        }),
      ),
    );
    expect(await screen.findByRole("button", { name: "已记为相关" })).toBeDisabled();

    rendered.unmount();
    render(
      <MemoryRouter>
        <SkillBrowserPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "开启" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  }, 10_000);
});
