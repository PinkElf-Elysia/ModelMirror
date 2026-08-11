import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CodingWorkerConsole from "./CodingWorkerConsole";

const api = vi.hoisted(() => ({
  getCodingWorkerStatus: vi.fn(),
  listCodingWorkerTasks: vi.fn(),
  getCodingWorkerTask: vi.fn(),
  handoffCodingWorkerTask: vi.fn(),
  listCodingWorkerApprovals: vi.fn(),
  listCodingWorkerEvidence: vi.fn(),
  listCodingWorkerArtifacts: vi.fn(),
  listCodingWorkerTree: vi.fn(),
  readCodingWorkerDiff: vi.fn(),
  connectCodingWorkerEvents: vi.fn(),
  decideCodingWorkerApproval: vi.fn(),
  changeCodingWorkerTask: vi.fn(),
  createCodingWorkerTask: vi.fn(),
  readCodingWorkerEntry: vi.fn(),
  sendCodingWorkerMessage: vi.fn(),
  codingWorkerArtifactUrl: vi.fn((taskId: string, artifactId: string) => `/artifact/${taskId}/${artifactId}`),
}));

vi.mock("../utils/codingWorkerApi", () => api);

const task = {
  task_id: "task_1234567890abcdef1234567890abcdef",
  spec: {
    client_task_id: "console_1234567890abcdef1234567890abcdef",
    origin: { module: "worker-console", object_id: "local-user" },
    objective: "修复失败测试并生成证据",
    workspace_source: { kind: "host_snapshot", source_id: "source_1", revision: "rev_1" },
    acceptance: {
      contract_id: "contract_1",
      required_checks: [{ check_id: "tests", kind: "command", label: "运行测试", required: true }],
      required_artifacts: [],
    },
    policy_profile: "develop",
    model_route: "coding/default",
    budget: { max_seconds: 3600, max_turns: 64, max_tool_calls: 512, max_output_bytes: 8_388_608 },
    context_refs: [],
  },
  state: "waiting_approval",
  workspace_id: "workspace_1234567890abcdef1234567890abcdef",
  created_at: 1,
  updated_at: 2,
  expires_at: 100,
  pinned: false,
  last_event_sequence: 1,
  reason: null,
} as const;

beforeEach(() => {
  Object.values(api).forEach((mock) => "mockReset" in mock && mock.mockReset());
  api.codingWorkerArtifactUrl.mockImplementation((taskId: string, artifactId: string) => `/artifact/${taskId}/${artifactId}`);
  api.getCodingWorkerStatus.mockResolvedValue({ enabled: true, available: true, version: "v1", max_active_tasks: 2, retention_seconds: 604800, network_enabled: false, acceptance_checks: ["python-pytest", "react-build"], reason: null });
  api.listCodingWorkerTasks.mockResolvedValue([task]);
  api.getCodingWorkerTask.mockResolvedValue(task);
  api.listCodingWorkerApprovals.mockResolvedValue([{ approval_id: "approval_1234567890abcdef1234567890abcdef", task_id: task.task_id, operation_id: "operation_1", capability: "command", status: "pending", request: { command: "pytest" }, lease: null, created_at: 1, decided_at: null }]);
  api.listCodingWorkerEvidence.mockResolvedValue([{ evidence_id: "evidence_1", task_id: task.task_id, check_id: "tests", operation_id: "operation_1", workspace_tree_hash: "a".repeat(64), status: "failed", exit_code: 1, artifact_id: "artifact_1", created_at: 2 }]);
  api.listCodingWorkerArtifacts.mockResolvedValue([]);
  api.listCodingWorkerTree.mockResolvedValue({ workspace_id: task.workspace_id, tree_hash: "a".repeat(64), entries: [{ entry_id: "entry_1", name: "app.py", display_path: "src/app.py", kind: "file", size: 12, sha256: "b".repeat(64) }] });
  api.readCodingWorkerDiff.mockResolvedValue("diff --git a/src/app.py b/src/app.py");
  api.connectCodingWorkerEvents.mockReturnValue(() => undefined);
  api.decideCodingWorkerApproval.mockResolvedValue({});
});

describe("CodingWorkerConsole", () => {
  it("shows task state, workspace, approval and Coding writeback boundary", async () => {
    const user = userEvent.setup();
    render(<CodingWorkerConsole context="coding" />);

    expect((await screen.findAllByText("修复失败测试并生成证据")).length).toBe(2);
    expect(screen.getByText("宿主写回")).toBeInTheDocument();
    expect(await screen.findByText("src/app.py")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "证据" }));
    await user.click(await screen.findByRole("button", { name: "批准一次" }));
    await waitFor(() => expect(api.decideCodingWorkerApproval).toHaveBeenCalledWith(
      task.task_id,
      "approval_1234567890abcdef1234567890abcdef",
      "approve_once",
    ));
  });

  it("keeps domain writeback controls out of the generic Agent context", async () => {
    render(<CodingWorkerConsole context="agent" />);
    expect((await screen.findAllByText("修复失败测试并生成证据")).length).toBe(2);
    expect(screen.queryByText("宿主写回")).not.toBeInTheDocument();
  });

  it("hands a completed Host Snapshot task to the v13 confirmation chain", async () => {
    const completed = { ...task, state: "completed" as const };
    api.listCodingWorkerTasks.mockResolvedValue([completed]);
    api.getCodingWorkerTask.mockResolvedValue(completed);
    api.handoffCodingWorkerTask.mockResolvedValue({
      id: "session_1234567890abcdef1234567890abcdef",
      status: "ready",
      project: { id: "hostgit_1234567890abcdef1234567890abcdef" },
      revision: 1,
      task_id: task.task_id,
    });
    const onCodingHandoff = vi.fn();
    const user = userEvent.setup();
    render(<CodingWorkerConsole context="coding" onCodingHandoff={onCodingHandoff} />);

    await user.click(await screen.findByRole("button", { name: "进入 v13 写回确认" }));

    await waitFor(() => expect(api.handoffCodingWorkerTask).toHaveBeenCalledWith(task.task_id));
    expect(onCodingHandoff).toHaveBeenCalledWith(expect.objectContaining({ revision: 1 }));
  });
});
