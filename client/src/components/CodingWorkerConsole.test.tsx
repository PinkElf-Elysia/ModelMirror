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

const projectApi = vi.hoisted(() => ({
  createCodingProjectSelection: vi.fn(),
  getCodingProjectSelection: vi.fn(),
  getCodingProjects: vi.fn(),
  getCodingWorkerHostSource: vi.fn(),
}));

vi.mock("../utils/codingApi", () => projectApi);

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
  Object.values(projectApi).forEach((mock) => "mockReset" in mock && mock.mockReset());
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
  projectApi.getCodingProjects.mockResolvedValue({
    enabled: true,
    configured: true,
    available: true,
    selection: true,
    default_project_id: "modelmirror",
    max_projects: 50,
    projects: [{
      id: "hostgit_existing",
      name: "现有项目",
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
        commands: true,
      },
      writeback_reason: null,
    }],
  });
  projectApi.getCodingWorkerHostSource.mockImplementation(async (projectId: string) => ({
    source_id: projectId,
    name: "已授权项目",
    branch: "main",
    revision: projectId === "hostgit_python_sample"
      ? "c8c27a695d3a6562b5ff8fe3df28548ecc388df4"
      : "b".repeat(40),
  }));
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

  it("requests a Helper folder selection and binds the returned project revision", async () => {
    const newProject = {
      id: "hostgit_python_sample",
      name: "Python 折扣样例",
      kind: "host_git",
      state: "available",
      reason: null,
      branch: "main",
      head: "c8c27a695d3a6562b5ff8fe3df28548ecc388df4",
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
        commands: true,
      },
      writeback_reason: null,
    } as const;
    projectApi.createCodingProjectSelection.mockResolvedValue({
      request_id: "selection_1",
      status: "completed",
      project_id: newProject.id,
      error: null,
      expires_at: 100,
    });
    projectApi.getCodingProjects
      .mockResolvedValueOnce(await projectApi.getCodingProjects())
      .mockResolvedValueOnce({
        enabled: true,
        configured: true,
        available: true,
        selection: true,
        default_project_id: "modelmirror",
        max_projects: 50,
        projects: [newProject],
      });

    const user = userEvent.setup();
    render(<CodingWorkerConsole context="coding" />);
    await user.click(await screen.findByRole("button", { name: "创建任务" }));
    await user.click(screen.getByRole("button", { name: "添加本地项目" }));

    await waitFor(() => expect(screen.getByLabelText("本地项目")).toHaveValue(newProject.id));
    expect(screen.getByLabelText("基准 revision")).toHaveValue(newProject.head);
    expect(projectApi.createCodingProjectSelection).toHaveBeenCalledTimes(1);
  });

  it("recovers a project registered after the selection request expires", async () => {
    const lateProject = {
      id: "hostgit_late_sample",
      name: "延迟返回样例",
      kind: "host_git",
      state: "available",
      reason: null,
      branch: "main",
      head: "b".repeat(40),
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
        commands: true,
      },
      writeback_reason: null,
    } as const;
    projectApi.createCodingProjectSelection.mockResolvedValue({
      request_id: "selection_late",
      status: "expired",
      project_id: null,
      error: "project_selection_expired",
      expires_at: 100,
    });
    projectApi.getCodingProjects
      .mockResolvedValueOnce(await projectApi.getCodingProjects())
      .mockResolvedValueOnce({
        enabled: true,
        configured: true,
        available: true,
        selection: true,
        default_project_id: "modelmirror",
        max_projects: 50,
        projects: [lateProject],
      });

    const user = userEvent.setup();
    render(<CodingWorkerConsole context="coding" />);
    await user.click(await screen.findByRole("button", { name: "创建任务" }));
    await user.click(screen.getByRole("button", { name: "添加本地项目" }));

    await waitFor(() => expect(screen.getByLabelText("本地项目")).toHaveValue(lateProject.id));
    expect(screen.getByLabelText("基准 revision")).toHaveValue(lateProject.head);
    expect(screen.queryByText("选择请求已超时", { exact: false })).not.toBeInTheDocument();
  });
});
