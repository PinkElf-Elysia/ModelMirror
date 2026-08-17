import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const workerApi = vi.hoisted(() => ({
  getCodingWorkerStatus: vi.fn(),
}));

const codingApi = vi.hoisted(() => ({
  CodingApiErrorClass: class CodingApiError extends Error {
    code: string;
    status: number;

    constructor(message: string, status: number, code: string) {
      super(message);
      this.status = status;
      this.code = code;
    }
  },
  applyCodingChanges: vi.fn(),
  cancelCodingTurn: vi.fn(),
  cancelCodingVerification: vi.fn(),
  closeCodingSession: vi.fn(),
  commitCodingChanges: vi.fn(),
  confirmCodingVerification: vi.fn(),
  connectCodingEvents: vi.fn(),
  continueCodingSession: vi.fn(),
  createCodingSession: vi.fn(),
  decideCodingCommand: vi.fn(),
  discardCodingChanges: vi.fn(),
  discardCodingRecovery: vi.fn(),
  getCodingApplyStatus: vi.fn(),
  getCodingCapabilities: vi.fn(),
  getCodingChanges: vi.fn(),
  getCodingCommitStatus: vi.fn(),
  getCodingHistory: vi.fn(),
  getCodingPatch: vi.fn(),
  getCodingPendingCommand: vi.fn(),
  getCodingProjectHost: vi.fn(),
  getCodingProjects: vi.fn(),
  getCodingPublishStatus: vi.fn(),
  getCodingRecovery: vi.fn(),
  getCodingRecoveryPatch: vi.fn(),
  getCodingSessionStatus: vi.fn(),
  getCodingVerification: vi.fn(),
  markCodingPublishReady: vi.fn(),
  publishCodingChanges: vi.fn(),
  resumeCodingRecovery: vi.fn(),
  revertCodingApply: vi.fn(),
  startCodingTurn: vi.fn(),
  startCodingVerification: vi.fn(),
  undoCodingCommit: vi.fn(),
  validateCodingChanges: vi.fn(),
}));

vi.mock("../utils/codingWorkerApi", () => workerApi);
vi.mock("../utils/codingApi", () => ({
  ...codingApi,
  CodingApiError: codingApi.CodingApiErrorClass,
  getPendingCodingCommand: codingApi.getCodingPendingCommand,
}));

vi.mock("../components/CodingWorkerConsole", () => ({
  default: ({ onCodingHandoff }: { onCodingHandoff?: (value: unknown) => void }) => (
    <button
      onClick={() => onCodingHandoff?.({
        id: "session_1234567890abcdef1234567890abcdef",
        status: "ready",
        project: { id: "hostgit_1234567890abcdef1234567890abcdef" },
        revision: 1,
        task_id: "task_1234567890abcdef1234567890abcdef",
      })}
      type="button"
    >
      创建写回确认
    </button>
  ),
}));
vi.mock("../components/CodingChangesPanel", () => ({
  default: () => <div>专用写回审阅</div>,
}));
vi.mock("../components/CodingHistoryPanel", () => ({ default: () => null }));
vi.mock("../components/CodingProjectHostPanel", () => ({ default: () => null }));
vi.mock("../components/CodingRecoveryCard", () => ({ default: () => null }));
vi.mock("../components/PageContainer", () => ({
  default: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}));

import CodingPage from "./CodingPage";

const project = {
  id: "hostgit_1234567890abcdef1234567890abcdef",
  name: "Worker 样例项目",
  kind: "host_git",
  state: "available",
  reason: null,
  branch: "main",
  head: "a".repeat(40),
  features: {
    chat: true,
    draft: true,
    diff: true,
    download: true,
    recovery: true,
    verification: true,
    apply: true,
    commit: true,
    publish: false,
  },
};

beforeEach(() => {
  window.sessionStorage.clear();
  Object.values(workerApi).forEach((mock) => mock.mockReset());
  Object.values(codingApi).forEach((mock) => {
    if ("mockReset" in mock) mock.mockReset();
  });
  workerApi.getCodingWorkerStatus.mockResolvedValue({ enabled: true, available: true });
  codingApi.getCodingRecovery.mockResolvedValue({ enabled: true, available: true, pending: false });
  codingApi.getCodingCapabilities.mockResolvedValue({
    enabled: true,
    configured: false,
    available: false,
    mode: "draft",
    recovery: { pending: false },
    limits: { max_prompt_chars: 20_000 },
    projects: { available: true },
    project_host: { enabled: true, direct_writeback: true, writeback_available: true },
    apply: { enabled: true, available: true },
    commit: { enabled: true, available: true },
    verification: { enabled: true, available: true },
    publish: { enabled: false, available: false },
    incremental: { enabled: true, available: true },
    commands: { enabled: false, available: false },
  });
  codingApi.getCodingProjects.mockResolvedValue({ projects: [project] });
  codingApi.getCodingProjectHost.mockResolvedValue({
    available: true,
    direct_writeback: true,
    writeback_available: true,
  });
  codingApi.getCodingSessionStatus.mockResolvedValue({
    id: "session_1234567890abcdef1234567890abcdef",
    status: "ready",
    project,
  });
  codingApi.getCodingChanges.mockResolvedValue({
    revision: 1,
    files: [{ path: "app.py", status: "modified", additions: 1, deletions: 1 }],
    additions: 1,
    deletions: 1,
    validation: { valid: true, checked_at: 1, issues: [] },
    can_download: true,
  });
  codingApi.getCodingApplyStatus.mockResolvedValue({ state: "not_applied", revision: 1 });
  codingApi.getCodingCommitStatus.mockResolvedValue({ state: "not_committed", revision: 1 });
  codingApi.getCodingHistory.mockResolvedValue({ cycles: [], active_cycle: 1, can_continue: false });
  codingApi.getCodingVerification.mockResolvedValue(null);
  codingApi.connectCodingEvents.mockReturnValue(() => undefined);
});

describe("CodingPage Worker writeback surface", () => {
  it("keeps a Worker handoff in a dedicated writeback review instead of the legacy workspace", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><CodingPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "创建写回确认" }));

    expect(await screen.findByRole("heading", { name: "宿主写回确认" })).toBeVisible();
    expect(screen.getByText("专用写回审阅")).toBeVisible();
    expect(screen.queryByRole("button", { name: "创建写回确认" })).not.toBeInTheDocument();
    expect(JSON.parse(window.sessionStorage.getItem("modelmirror.coding.session.v1") ?? "{}"))
      .toMatchObject({ workerTaskId: "task_1234567890abcdef1234567890abcdef" });
  });

  it("drops a stale legacy session marker and returns to the Worker surface", async () => {
    window.sessionStorage.setItem("modelmirror.coding.session.v1", JSON.stringify({
      id: "session_1234567890abcdef1234567890abcdef",
      lastSeq: 0,
      projectId: project.id,
    }));
    codingApi.getCodingSessionStatus.mockRejectedValue(
      new codingApi.CodingApiErrorClass("session_not_found", 404, "session_not_found"),
    );
    codingApi.getCodingChanges.mockRejectedValue(
      new codingApi.CodingApiErrorClass("session_not_found", 404, "session_not_found"),
    );

    render(<MemoryRouter><CodingPage /></MemoryRouter>);

    await waitFor(() => expect(window.sessionStorage.getItem("modelmirror.coding.session.v1")).toBeNull());
    expect(await screen.findByRole("button", { name: "创建写回确认" })).toBeVisible();
  });

  it("routes a recovered Host Git handoff back to the dedicated writeback surface", async () => {
    workerApi.getCodingWorkerStatus.mockResolvedValue({
      enabled: true,
      available: false,
      reason: "provider_unavailable",
    });
    codingApi.getCodingRecovery.mockResolvedValue({
      enabled: true,
      available: true,
      pending: true,
      can_resume: true,
      can_download: true,
      state: "draft",
      revision: 1,
      file_count: 4,
      reason: null,
      project,
    });

    const user = userEvent.setup();
    render(<MemoryRouter><CodingPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "宿主写回确认" })).toBeVisible();
    const returnButton = screen.getByRole("button", { name: "返回 Worker 任务" });
    expect(returnButton).toBeVisible();

    await user.click(returnButton);
    expect(await screen.findByRole("button", { name: "创建写回确认" })).toBeVisible();
  });
});
