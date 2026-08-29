import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "../App";

const projectId = "rp_0123456789abcdef0123456789abcdef";
const project = {
  schemaVersion: 1,
  projectId,
  title: "Agent 评测综述",
  researchQuestion: "如何提高 Agent 评测的可复现性？",
  domain: "ai_agent",
  currentStage: "literature",
  stages: { literature: "active", hypothesis_protocol: "not_available" },
  literaturePhase: "terminal",
  literatureOutcome: "completed",
  activeRunId: null,
  completedRunId: "lr_01",
  collectionId: null,
  profileId: "v0.1-literature-default",
  modelId: "fixed-model",
  attempts: [],
  createdAt: "2026-08-25T00:00:00Z",
  updatedAt: "2026-08-25T00:01:00Z",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("sources page separates cited sources from eligible local collections", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    let body: unknown = project;
    if (path.endsWith("/sources")) body = { projectId, literatureRunId: "lr_01", integrityStatus: "verified", sources: [{ index: 1, title: "A reproducibility paper", url: "https://openalex.org/W1" }] };
    else if (path.endsWith("/literature/session")) body = { status: "ready", username: "researcher" };
    else if (path.endsWith("/library/collections")) body = { collections: [{ id: "collection-1", name: "Zotero Pilot", is_public: true, agent_enabled: true, document_count: 4, indexed_document_count: 4 }] };
    else if (path.endsWith("/zotero/status")) body = { config: { configured: true, has_api_key: true }, status: { success: true } };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));

  render(<MemoryRouter initialEntries={[`/projects/${projectId}/sources`]}><App /></MemoryRouter>);

  const source = await screen.findByRole(
    "link",
    { name: /A reproducibility paper/ },
    { timeout: 3_000 },
  );
  expect(source).toHaveAttribute("href", "https://openalex.org/W1");
  expect(source).toHaveAttribute("rel", "noopener noreferrer");
  expect(await screen.findByText("可用于综述")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "用于下一次研究" })).toHaveAttribute("href", `/projects/${projectId}?collectionId=collection-1`);
});

test("review renders verified markdown without raw images or non-HTTPS links", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.endsWith("/review")
      ? { projectId, literatureRunId: "lr_01", integrityStatus: "verified", markdown: "## 研究缺口\n[可信来源](https://example.org/paper) [不安全来源](http://127.0.0.1/private) ![远程像素](https://example.org/pixel.png)" }
      : project;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));

  const { container } = render(<MemoryRouter initialEntries={[`/projects/${projectId}/review`]}><App /></MemoryRouter>);

  const safeLink = await screen.findByRole("link", { name: /可信来源/ });
  expect(safeLink).toHaveAttribute("href", "https://example.org/paper");
  expect(screen.queryByRole("link", { name: "不安全来源" })).not.toBeInTheDocument();
  await waitFor(() => expect(container.querySelector(".markdown-body img")).toBeNull());
  expect(screen.getByText(/上游报告包含研究缺口章节/)).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: /Markdown 综述|Quarto 文稿|BibTeX 引用|RIS 引用|来源清单|完整性 manifest|研究 receipt|上游 Quarto ZIP/ })).toHaveLength(8);
});

test("project detail keeps an upstream completion retryable when artifacts failed integrity", async () => {
  const failedProject = {
    ...project,
    completedRunId: null,
    attempts: [{
      runId: "lr_failed",
      ldrResearchId: "ldr_failed",
      phase: "terminal",
      outcome: "completed",
      rawStatus: "completed",
      integrityStatus: "failed",
      errorType: "artifact_sync_failed",
      errorMessage: "LDR report did not provide sources",
      createdAt: "2026-08-25T00:00:00Z",
      startedAt: "2026-08-25T00:00:01Z",
      progress: 100,
      latestLog: null,
    }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.endsWith("/literature/session")
      ? { status: "ready", username: "researcher" }
      : path.endsWith("/system")
        ? { status: "ready", checks: [], checkedAt: "2026-08-25T00:00:00Z", literatureCapability: { status: "ready", serviceStatus: "ready", sessionStatus: "ready", profileStatus: "ready", modelBridgeStatus: "ready", username: "researcher", scientificClaim: "none" } }
      : path.endsWith("/module")
        ? { links: { localDeepResearch: "http://127.0.0.1:8792" } }
        : failedProject;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));

  render(<MemoryRouter initialEntries={[`/projects/${projectId}`]}><App /></MemoryRouter>);

  expect(await screen.findByText("成果不完整")).toBeInTheDocument();
  expect(screen.queryByText("综述已完成")).not.toBeInTheDocument();
  expect(screen.getByText(/报告、来源或引用成果包未通过完整性校验/)).toBeInTheDocument();
  expect(screen.getByText("查看技术原因")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试文献研究" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "重新同步成果" })).toBeEnabled();
});

test("project detail blocks retry when fixed model execution is not ready", async () => {
  const failedProject = {
    ...project,
    completedRunId: null,
    attempts: [{
      runId: "lr_failed",
      ldrResearchId: "ldr_failed",
      phase: "terminal",
      outcome: "completed",
      rawStatus: "completed",
      integrityStatus: "failed",
      errorType: "artifact_sync_failed",
      errorMessage: "citation package is incomplete",
      createdAt: "2026-08-25T00:00:00Z",
      progress: 100,
      latestLog: null,
    }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path.endsWith("/literature/session")
      ? { status: "ready", username: "researcher" }
      : path.endsWith("/system")
        ? { status: "ready", checks: [], checkedAt: "2026-08-25T00:00:00Z", literatureCapability: { status: "not_ready", serviceStatus: "ready", sessionStatus: "ready", profileStatus: "not_ready", modelBridgeStatus: "not_ready", username: "researcher", scientificClaim: "none" } }
        : path.endsWith("/module")
          ? { links: { localDeepResearch: "http://127.0.0.1:8792" } }
          : failedProject;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));

  render(<MemoryRouter initialEntries={[`/projects/${projectId}`]}><App /></MemoryRouter>);

  expect(await screen.findByText(/固定模型执行资格未就绪/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试文献研究" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "重新同步成果" })).toBeEnabled();
});

test("project list reports integrity failure instead of a stale completed outcome", async () => {
  const staleCompletedProject = {
    ...project,
    completedRunId: "lr_failed",
    attempts: [{
      runId: "lr_failed",
      ldrResearchId: "ldr_failed",
      phase: "terminal",
      outcome: "completed",
      rawStatus: "completed",
      integrityStatus: "failed",
      errorType: "artifact_sync_failed",
      errorMessage: "citation package is incomplete",
      createdAt: "2026-08-25T00:00:00Z",
      progress: 100,
      latestLog: null,
    }],
  };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ items: [staleCompletedProject], nextCursor: null }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));

  render(<MemoryRouter initialEntries={["/projects"]}><App /></MemoryRouter>);

  expect(await screen.findByText("成果不完整")).toBeInTheDocument();
  expect(screen.queryByText("completed")).not.toBeInTheDocument();
  expect(screen.getByRole("option", { name: "综述已完成" })).toBeInTheDocument();
});
