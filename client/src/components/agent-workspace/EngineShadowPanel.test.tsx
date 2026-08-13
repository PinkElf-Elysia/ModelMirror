import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EngineShadowRun } from "../../types/agentWorkspace";

const api = vi.hoisted(() => ({
  connect: vi.fn(() => vi.fn()),
  create: vi.fn(),
  list: vi.fn(),
  listWorkspace: vi.fn(),
  read: vi.fn(),
  readFile: vi.fn(),
  stop: vi.fn(),
}));

vi.mock("../../utils/agentWorkspaceApi", () => ({
  connectEngineShadowEvents: api.connect,
  createEngineShadowRun: api.create,
  listEngineShadowRuns: api.list,
  listEngineShadowWorkspace: api.listWorkspace,
  readEngineShadowRun: api.read,
  readEngineShadowWorkspaceFile: api.readFile,
  stopEngineShadowRun: api.stop,
}));

import EngineShadowPanel from "./EngineShadowPanel";

const candidate: EngineShadowRun = {
  run_id: "run-1",
  session_id: "upstream-session-1",
  status: "candidate_ready",
  objective: "构建离线单文件应用",
  model_base_id: "deepseek-v4-pro-0813",
  resolved_model_id: "deepseek/deepseek-v4-pro-0813",
  thinking_level: "medium",
  token_budget: 750_000,
  max_goal_rounds: 12,
  max_task_turns: 100,
  goal_round: 2,
  model_turns: 4,
  retry_count: 0,
  token_total: 12_345,
  usage_source: "provider",
  tool_calls: 3,
  tool_failures: 0,
  candidate_sha256: "a".repeat(64),
  error_code: "",
  public_error: "",
  upstream_revision: "047505dccc0cc16ad92be11011347d635f33ceb0",
  protocol: "modelmirror.upstream-workbench/1",
  created_at: 1,
  updated_at: 2,
  started_at: 1,
  finished_at: 2,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function arrange(run: EngineShadowRun = candidate) {
  api.list.mockResolvedValue([run]);
  api.read.mockResolvedValue({ run, last_event_sequence: 2 });
  api.listWorkspace.mockResolvedValue([
    { name: "index.html", path: "index.html", kind: "file", size: 10, modified_at: 1 },
  ]);
  api.readFile.mockResolvedValue({ path: "index.html", content: "<html />", size: 8 });
  api.create.mockResolvedValue(run);
}

describe("EngineShadowPanel", () => {
  it("labels an upstream candidate as unverified and exposes no publish claim", async () => {
    arrange();
    render(<EngineShadowPanel />);

    expect(await screen.findByText("候选已就绪（未验收）")).toBeVisible();
    expect(
      screen.getByText(/不会调用 Browser，也不会创建 App、Version、Artifact 或发布记录/),
    ).toBeVisible();
    expect(screen.queryByText(/已验证应用|发布成功|立即试玩/)).not.toBeInTheDocument();
    expect(screen.getByText(/4 次模型轮次/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /index\.html/ }));
    expect(await screen.findByText("<html />")).toBeVisible();
  });

  it("shows aggregate retries and sanitized tool failure counts", async () => {
    arrange({ ...candidate, retry_count: 2, tool_failures: 1 });
    render(<EngineShadowPanel />);

    expect(await screen.findByText(/2 次 Worker 重试/)).toBeVisible();
    expect(screen.getByText(/1 次工具失败/)).toBeVisible();
  });

  it("starts with the registered base model and the default 750k token budget", async () => {
    arrange({ ...candidate, status: "pending", candidate_sha256: "" });
    render(<EngineShadowPanel />);

    await screen.findByText("等待启动");
    await userEvent.type(screen.getByLabelText("影子构建目标"), "构建一个记忆卡片游戏");
    await userEvent.click(screen.getByRole("button", { name: "启动影子构建" }));

    await waitFor(() => {
      expect(api.create).toHaveBeenCalledWith({
        objective: "构建一个记忆卡片游戏",
        model_base_id: "deepseek-v4-pro-0813",
        thinking_level: "medium",
        token_budget: 750_000,
      });
    });
  });
});
