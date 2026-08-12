import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CodingWorkerConsole from "./CodingWorkerConsole";

const api = vi.hoisted(() => ({
  getCodingWorkerStatus: vi.fn(),
  listCodingWorkerTasks: vi.fn(),
  getCodingWorkerTask: vi.fn(),
  getCodingWorkerChangeset: vi.fn(),
  getCodingWorkerDiagnostics: vi.fn(),
  handoffCodingWorkerTask: vi.fn(),
  listCodingWorkerApprovals: vi.fn(),
  listCodingWorkerEvidence: vi.fn(),
  listCodingWorkerOperationOutput: vi.fn(),
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
  api.getCodingWorkerStatus.mockResolvedValue({ enabled: true, available: true, version: "v1", max_active_tasks: 2, retention_seconds: 604800, network_enabled: false, acceptance_checks: ["python-pytest", "react-build"], model_routes: ["coding/default", "coding/quality"], reason: null, capabilities: { api_version: "v1", task_runtime: true, professional_file_tools: true, shell: true, operation_output: true, changesets: true, code_intelligence: true } });
  api.listCodingWorkerTasks.mockResolvedValue([task]);
  api.getCodingWorkerTask.mockResolvedValue(task);
  api.listCodingWorkerApprovals.mockResolvedValue([{ approval_id: "approval_1234567890abcdef1234567890abcdef", task_id: task.task_id, operation_id: "operation_1", capability: "command", status: "pending", request: { command: "pytest" }, lease: null, created_at: 1, decided_at: null }]);
  api.listCodingWorkerEvidence.mockResolvedValue([{ evidence_id: "evidence_1", task_id: task.task_id, check_id: "tests", operation_id: "operation_1", workspace_tree_hash: "a".repeat(64), status: "failed", exit_code: 1, artifact_id: "artifact_1", created_at: 2 }]);
  api.listCodingWorkerArtifacts.mockResolvedValue([]);
  api.listCodingWorkerTree.mockResolvedValue({ workspace_id: task.workspace_id, tree_hash: "a".repeat(64), entries: [{ entry_id: "entry_1", name: "app.py", display_path: "src/app.py", kind: "file", size: 12, sha256: "b".repeat(64) }] });
  api.readCodingWorkerDiff.mockResolvedValue("diff --git a/src/app.py b/src/app.py");
  api.listCodingWorkerOperationOutput.mockResolvedValue([]);
  api.getCodingWorkerChangeset.mockRejectedValue(new Error("not a changeset"));
  api.getCodingWorkerDiagnostics.mockRejectedValue(new Error("not diagnostics"));
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

    expect((await screen.findAllByText("修复失败测试并生成证据")).length).toBe(3);
    expect(screen.getByText("宿主写回")).toBeInTheDocument();
    expect(await screen.findByText("src/app.py")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "批准本次执行" }));
    await waitFor(() => expect(api.decideCodingWorkerApproval).toHaveBeenCalledWith(
      task.task_id,
      "approval_1234567890abcdef1234567890abcdef",
      "approve_once",
    ));
  });

  it("keeps domain writeback controls out of the generic Agent context", async () => {
    render(<CodingWorkerConsole context="agent" />);
    expect((await screen.findAllByText("修复失败测试并生成证据")).length).toBe(3);
    expect(screen.queryByText("宿主写回")).not.toBeInTheDocument();
  });

  it("shows public plans, exact shell approval, replayed output, changesets and diagnostics", async () => {
    api.listCodingWorkerApprovals.mockResolvedValue([{
      approval_id: "approval_1234567890abcdef1234567890abcdef",
      task_id: task.task_id,
      operation_id: "operation_shell_1",
      capability: "shell",
      status: "pending",
      request: {
        operation_id: "operation_shell_1",
        script_sha256: "c".repeat(64),
        cwd: "src",
        mode: "mutate",
        timeout_seconds: 120,
        network_scope_sha256: null,
      },
      lease: null,
      created_at: 1,
      decided_at: null,
    }]);
    api.listCodingWorkerOperationOutput.mockResolvedValue([{
      task_id: task.task_id,
      operation_id: "operation_shell_1",
      sequence: 3,
      stream: "stderr",
      text: "pytest failed\n",
      created_at: 2,
      truncated: false,
    }]);
    api.getCodingWorkerChangeset.mockResolvedValue({
      changeset_id: "changeset_1234567890abcdef1234567890abcdef",
      task_id: task.task_id,
      operation_id: "operation_shell_1",
      base_tree_hash: "a".repeat(64),
      result_tree_hash: "d".repeat(64),
      state: "applied",
      entries: [{ entry_id: "entry_1", kind: "modify", display_path: "src/app.py", destination_display_path: null, preimage_sha256: "b".repeat(64), postimage_sha256: "d".repeat(64), binary: false }],
      artifact_id: null,
      created_at: 2,
      updated_at: 3,
    });
    api.getCodingWorkerDiagnostics.mockResolvedValue({
      task_id: task.task_id,
      operation_id: "operation_shell_1",
      entry_id: "entry_1",
      language: "python",
      workspace_tree_hash: "d".repeat(64),
      current_tree_hash: "d".repeat(64),
      stale: false,
      diagnostics: [{ diagnostic_id: "diagnostic_1", task_id: task.task_id, entry_id: "entry_1", workspace_tree_hash: "d".repeat(64), range: { start: { line: 4, character: 2 }, end: { line: 4, character: 8 } }, severity: "error", code: "reportGeneralTypeIssues", message: "返回类型不匹配", created_at: 3 }],
    });
    api.connectCodingWorkerEvents.mockImplementation((_taskId, _after, handlers) => {
      queueMicrotask(() => {
        handlers.onEvent({ sequence: 1, task_id: task.task_id, type: "provider_event", payload: { kind: "plan", data: { summary: "先复现失败，再修复并复测。" } }, created_at: 1 });
        handlers.onEvent({ sequence: 2, task_id: task.task_id, type: "tool_operation", payload: { operation_id: "operation_shell_1", state: "completed" }, created_at: 2 });
        handlers.onEvent({ sequence: 3, task_id: task.task_id, type: "provider_event", payload: { kind: "internal_frame" }, created_at: 3 });
      });
      return () => undefined;
    });

    const user = userEvent.setup();
    render(<CodingWorkerConsole context="agent" />);

    expect((await screen.findAllByText("先复现失败，再修复并复测。")).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("provider event")).not.toBeInTheDocument();
    expect(await screen.findByText("Shell 单次审批")).toBeInTheDocument();
    await user.click(screen.getByText("查看操作绑定"));
    expect(screen.getByText((content) => content.includes("c".repeat(64)))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "本任务批准" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "终端" }));
    expect(await screen.findByText("pytest failed", { exact: false })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "变更" }));
    expect(await screen.findByText("src/app.py")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "诊断" }));
    expect(await screen.findByText("返回类型不匹配")).toBeInTheDocument();
    expect(screen.getByText("5:3 · reportGeneralTypeIssues")).toBeInTheDocument();
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

    await user.click(await screen.findByRole("button", { name: "进入写回确认" }));

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
    api.createCodingWorkerTask.mockResolvedValue(task);

    const user = userEvent.setup();
    render(<CodingWorkerConsole context="coding" />);
    await user.click(await screen.findByRole("button", { name: "创建任务" }));
    await user.click(screen.getByRole("button", { name: "添加本地项目" }));

    await waitFor(() => expect(screen.getByLabelText("项目")).toHaveValue(newProject.id));
    expect(screen.getByLabelText("基准 revision")).toHaveValue(newProject.head);
    expect(projectApi.createCodingProjectSelection).toHaveBeenCalledTimes(1);

    await user.selectOptions(screen.getByLabelText("执行方式"), "coding/quality");
    await user.type(screen.getByLabelText("任务目标"), "修复类型错误并完成复测");
    await user.click(screen.getByRole("button", { name: "创建并开始" }));
    await waitFor(() => expect(api.createCodingWorkerTask).toHaveBeenCalledWith(
      expect.objectContaining({ model_route: "coding/quality" }),
    ));
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

    await waitFor(() => expect(screen.getByLabelText("项目")).toHaveValue(lateProject.id));
    expect(screen.getByLabelText("基准 revision")).toHaveValue(lateProject.head);
    expect(screen.queryByText("选择请求已超时", { exact: false })).not.toBeInTheDocument();
  });
});
