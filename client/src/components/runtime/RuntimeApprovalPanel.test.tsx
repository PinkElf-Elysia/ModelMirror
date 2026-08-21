import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RuntimeApprovalPanel, { type RuntimeApproval } from "./RuntimeApprovalPanel";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RuntimeApprovalPanel Skill install approval", () => {
  it("does not flash an empty approval card while the initial request is loading", async () => {
    let resolveRequest!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    const fetchMock = vi.fn(() => pendingResponse);
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RuntimeApprovalPanel
        pollIntervalMs={60_000}
        requestTypes={["tool_call", "final_output"]}
        taskId="task-empty"
        title="Agent 运行审批"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("Agent 运行审批")).not.toBeInTheDocument();

    await act(async () => {
      resolveRequest(await jsonResponse({ items: [] }));
      await pendingResponse;
    });
    expect(screen.queryByText("Agent 运行审批")).not.toBeInTheDocument();
  });

  it("does not restart polling when an equivalent request-type array is recreated", async () => {
    const fetchMock = vi.fn(() => jsonResponse({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <RuntimeApprovalPanel
        pollIntervalMs={60_000}
        requestTypes={["tool_call", "final_output"]}
        taskId="task-stable"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    rerender(
      <RuntimeApprovalPanel
        pollIntervalMs={60_000}
        requestTypes={["tool_call", "final_output"]}
        taskId="task-stable"
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps an explicit empty request-type filter distinct from no filter", async () => {
    const approval: RuntimeApproval = {
      approval_id: "approval-filtered",
      request_type: "tool_call",
      task_id: "task-filtered",
      run_id: "run-filtered",
      node_id: "agent-filtered",
      node_title: "Filtered Agent",
      status: "pending",
      revision: 1,
      scope_type: "workflow",
      scope_id: "task-filtered",
      tool_name: "skill_read",
      arguments: {},
      description: "This approval must remain filtered.",
      content_preview: "",
      allowed_decisions: ["approve", "reject"],
      expires_at: 2_000_000_000,
      created_at: 1_900_000_000,
      metadata: {},
    };
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ items: [approval] })));

    render(
      <RuntimeApprovalPanel
        pollIntervalMs={60_000}
        requestTypes={[]}
        taskId="task-filtered"
      />,
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(approval.description)).not.toBeInTheDocument();
  });

  it("shows trusted fixed-SHA details without exposing editable install arguments", async () => {
    const approval: RuntimeApproval = {
      approval_id: "approval-skill-install",
      request_type: "tool_call",
      task_id: "task-1",
      run_id: "run-1",
      node_id: "agent-1",
      node_title: "合同处理 Agent",
      status: "pending",
      revision: 1,
      scope_type: "workflow",
      scope_id: "task-1",
      tool_name: "skill_install",
      arguments: {
        candidate_id: "catalog:project:router-pdf",
        candidate_fingerprint: "f".repeat(64),
      },
      description: "安装已核验 Skill 需要人工审批",
      content_preview: "",
      allowed_decisions: ["approve", "reject"],
      expires_at: 2_000_000_000,
      created_at: 1_900_000_000,
      metadata: {
        skill_approval: {
          candidate_id: "catalog:project:router-pdf",
          name: "Router PDF",
          repo_url: "https://github.com/example/router-skills",
          sub_path: "skills/pdf",
          current_sha: null,
          target_sha: "1".repeat(40),
          install_action: "install",
          authorization_scope: "global_install_current_run_only",
          trust: {
            riskLevel: "high",
            trustStatus: "conditional",
            installPolicy: "confirm",
            compatibilityStatus: "conditional",
            routerEligible: true,
            reasonCodes: ["trust_network_required"],
            missingCapabilities: [],
          },
        },
      },
    };
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ items: [approval] })));

    render(
      <RuntimeApprovalPanel
        pollIntervalMs={60_000}
        taskId="task-1"
      />,
    );

    expect(await screen.findByText("安装 Skill：Router PDF")).toBeVisible();
    expect(screen.getByText("全局安装 · 仅本轮授权")).toBeVisible();
    expect(screen.getByText("https://github.com/example/router-skills")).toBeVisible();
    expect(screen.getByText("skills/pdf")).toBeVisible();
    expect(screen.getByText("未安装")).toBeVisible();
    expect(screen.getByText("1".repeat(40))).toBeVisible();
    expect(screen.getByText(/风险：/)).toHaveTextContent("high");
    expect(screen.getByText(/兼容性：/)).toHaveTextContent("conditional");
    expect(screen.getByText(/trust_network_required/)).toBeVisible();
    expect(screen.getByText(/批准只授权当前 Agent 运行/)).toBeVisible();
    expect(screen.getByRole("button", { name: "批准" })).toBeVisible();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "编辑参数" })).not.toBeInTheDocument();
    expect(screen.queryByText(/candidate_fingerprint/)).not.toBeInTheDocument();
  });

  it("does not render trusted install details for a broader authorization scope", async () => {
    const approval: RuntimeApproval = {
      approval_id: "approval-global-scope",
      request_type: "tool_call",
      task_id: "task-2",
      run_id: "run-2",
      node_id: "agent-2",
      node_title: "Agent",
      status: "pending",
      revision: 1,
      scope_type: "workflow",
      scope_id: "task-2",
      tool_name: "skill_install",
      arguments: {},
      description: "Review install",
      content_preview: "",
      allowed_decisions: ["approve", "reject"],
      expires_at: 2_000_000_000,
      created_at: 1_900_000_000,
      metadata: {
        skill_approval: {
          candidate_id: "catalog:project:unsafe-scope",
          name: "Unsafe Scope Candidate",
          repo_url: "https://github.com/example/skills",
          sub_path: "skills/example",
          current_sha: null,
          target_sha: "2".repeat(40),
          install_action: "install",
          authorization_scope: "global",
          trust: {},
        },
      },
    };
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ items: [approval] })));

    render(<RuntimeApprovalPanel pollIntervalMs={60_000} taskId="task-2" />);

    expect(await screen.findByText("Review install")).toBeVisible();
    expect(screen.queryByText(/Unsafe Scope Candidate/)).not.toBeInTheDocument();
  });
});
