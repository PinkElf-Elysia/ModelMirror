import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgencyDagRunPanel from "./AgencyDagRunPanel";
import type {
  AgencyDagRevisionPayload,
  AgencyDagRun,
  AgencyExecutionCapabilities,
} from "./AgencyExpertTeamTypes";

const capabilities: AgencyExecutionCapabilities = {
  enabled: true,
  worker_available: true,
  protocol: "mm-agency-bridge/v2",
  max_steps: 6,
  max_concurrency: 2,
  max_model_calls: 10,
  max_tokens_per_call: 4096,
  timeout_seconds: 900,
  supports_replay: true,
  supports_cancel: true,
  supports_retry: true,
  supports_restart_resume: false,
  revision: {
    enabled: true,
    supports_feedback: true,
    supports_intermediate_steps: true,
    max_feedback_chars: 4000,
    max_model_calls: 10,
    budget_mode: "fresh",
  },
  hitl: {
    enabled: true,
    protocol: "mm-agency-bridge/v3",
    supports_human_input: true,
    supports_approval: true,
    max_interactions: 2,
    max_input_chars: 20000,
    wait_timeout_seconds: 86400,
    supports_reopen: true,
    supports_restart_wait: true,
    auto_insert_policy: "conservative",
  },
};

const run: AgencyDagRun = {
  task_id: "agency_dag_revision",
  run_id: "run-revision",
  model_id: "deepseek/deepseek-chat",
  goal: "制定交付计划",
  team_name: "交付专家团",
  selected_agent_ids: ["agent-alpha", "agent-beta"],
  status: "completed",
  sequence: 5,
  events: [],
  steps: [
    {
      event: "agency.step.completed",
      sequence: 1,
      task_id: "research",
      status: "completed",
      output: "研究证据",
      reused: true,
    },
    {
      event: "agency.step.completed",
      sequence: 2,
      task_id: "implementation_plan",
      status: "completed",
      output: "第一版实施方案",
    },
    {
      event: "agency.step.completed",
      sequence: 3,
      task_id: "final_report",
      status: "completed",
      output: "第一版最终报告",
    },
  ],
  task_definitions: [
    {
      task_id: "research",
      title: "证据研究",
      objective: "收集证据",
      depends_on: [],
      agent_id: "agent-alpha",
      acceptance: "来源明确",
    },
    {
      task_id: "implementation_plan",
      title: "实施计划",
      objective: "形成实施步骤",
      depends_on: ["research"],
      agent_id: "agent-beta",
      acceptance: "计划可执行",
    },
    {
      task_id: "final_report",
      title: "最终报告",
      objective: "整合交付",
      depends_on: ["implementation_plan"],
      agent_id: "agent-beta",
      acceptance: "结论完整",
    },
  ],
  final_output: "第一版最终报告",
  warnings: [],
  model_calls: 3,
  usage: { input_tokens: 100, output_tokens: 60 },
  revisable: true,
  lineage_model_calls: 8,
  lineage_usage: { input_tokens: 300, output_tokens: 140 },
  created_at: 1,
  updated_at: 2,
};

function historyResponse() {
  return Promise.resolve(new Response(JSON.stringify({
    items: [
      {
        task_id: "agency_dag_revision",
        run_id: "run-revision",
        model_id: "deepseek/deepseek-chat",
        goal: "制定交付计划",
        team_name: "交付专家团",
        selected_agent_ids: ["agent-alpha", "agent-beta"],
        status: "completed",
        sequence: 5,
        final_output_preview: "修订结果",
        model_calls: 3,
        usage: {},
        lineage_model_calls: 8,
        lineage_usage: {},
        revisable: true,
        revision: {
          parent_task_id: "agency_dag_parent",
          root_task_id: "agency_dag_parent",
          revision_index: 1,
          target_task_id: "implementation_plan",
          feedback_preview: "预算改为待确认",
          affected_task_ids: ["implementation_plan", "final_report"],
        },
        created_at: 1,
        updated_at: 2,
      },
    ],
    total: 1,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

function panelProps(overrides: Record<string, unknown> = {}) {
  return {
    capabilities,
    agentCatalog: [
      {
        id: "agent-alpha", name: "研究专家", department: "研究", expertise: "研究",
        scenarios: "研究", emoji: "🔎",
      },
      {
        id: "agent-beta", name: "交付专家", department: "产品", expertise: "交付",
        scenarios: "交付", emoji: "📦",
      },
    ],
    preview: null,
    invalid: false,
    modelName: "DeepSeek Chat",
    estimatedCostCny: 0.12,
    run,
    error: "",
    busy: false,
    confirmMode: null as "start" | "retry" | "revise" | null,
    pendingRevision: null as AgencyDagRevisionPayload | null,
    onConfirm: vi.fn(),
    onDismissConfirm: vi.fn(),
    onCancel: vi.fn(),
    onRetryRequest: vi.fn(),
    onRevisionRequest: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AgencyDagRunPanel revision flow", () => {
  it("shows target, prior output, affected/reused steps and a second paid confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn(() => historyResponse()));
    const onRevisionRequest = vi.fn();
    const props = panelProps({ onRevisionRequest });
    const view = render(<AgencyDagRunPanel {...props} />);
    const targetCard = screen.getByRole("heading", { name: "实施计划" }).closest("article");
    expect(targetCard).not.toBeNull();
    fireEvent.click(within(targetCard!).getByRole("button", { name: "要求修改" }));

    expect(screen.getByText(/交付专家.*实施计划.*implementation_plan/)).toBeVisible();
    expect(screen.getByText(/上一版输出：第一版实施方案/)).toBeVisible();
    expect(screen.getByText(/将执行：实施计划、最终报告/)).toBeVisible();
    expect(screen.getByText(/将复用：证据研究/)).toBeVisible();

    const feedback = "请保留证据，并将预算与负责人明确标为待确认。";
    fireEvent.change(screen.getByPlaceholderText(/说明需要保留/), {
      target: { value: feedback },
    });
    fireEvent.click(screen.getByRole("button", { name: "检查费用并继续" }));
    expect(onRevisionRequest).toHaveBeenCalledWith({
      target_task_id: "implementation_plan",
      feedback,
    });

    view.rerender(<AgencyDagRunPanel {...panelProps({
      confirmMode: "revise",
      pendingRevision: { target_task_id: "implementation_plan", feedback },
      onRevisionRequest,
    })} />);
    expect(screen.getByText(/本次返工新开最多 10 次调用/)).toBeVisible();
    expect(screen.getByRole("button", { name: "确认并返工" })).toBeVisible();

    expect(await screen.findByText(/implementation_plan：预算改为待确认/)).toBeVisible();
    expect(screen.getByRole("link", { name: "查看父版本" })).toHaveAttribute(
      "href",
      expect.stringContaining("dag_task=agency_dag_parent"),
    );
  });

  it("keeps completed history visible but hides revision actions when the switch is off", () => {
    vi.stubGlobal("fetch", vi.fn(() => historyResponse()));
    const onRevisionRequest = vi.fn();
    render(<AgencyDagRunPanel {...panelProps({
      capabilities: {
        ...capabilities,
        revision: { ...capabilities.revision, enabled: false },
      },
      onRevisionRequest,
    })} />);

    expect(screen.queryByRole("button", { name: "要求修改" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续完善" })).not.toBeInTheDocument();
    expect(screen.getByText(/对话式返工当前未启用/)).toBeVisible();
    expect(screen.getAllByText("第一版最终报告").length).toBeGreaterThan(0);
    expect(onRevisionRequest).not.toHaveBeenCalled();
  });
});

describe("AgencyDagRunPanel HITL flow", () => {
  it("requires a second paid confirmation before submitting human input", () => {
    vi.stubGlobal("fetch", vi.fn(() => historyResponse()));
    const onInteractionDecision = vi.fn();
    const waitingRun: AgencyDagRun = {
      ...run,
      status: "waiting",
      final_output: null,
      pending_interaction: {
        approval_id: "approval-input",
        step_id: "audience_input",
        kind: "human_input",
        prompt: "请补充采购流程中的最终决策人。",
        content_preview: "已有证据摘要",
        allowed_decisions: ["replace"],
        revision: 3,
        status: "pending",
        created_at: 1,
        updated_at: 1,
        expires_at: 100,
      },
    };
    render(<AgencyDagRunPanel {...panelProps({
      run: waitingRun,
      onInteractionDecision,
    })} />);

    expect(screen.getByText("等待人工输入")).toBeVisible();
    expect(screen.getByText("已有证据摘要")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText(/输入继续执行所需的信息/), {
      target: { value: "最终决策人为采购总监，法务负责合规复核。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "检查费用并提交" }));
    expect(onInteractionDecision).not.toHaveBeenCalled();
    expect(screen.getByText("恢复下游执行前请确认")).toBeVisible();
    expect(screen.getByText(/累计最多 10 次/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "确认并恢复执行" }));
    expect(onInteractionDecision).toHaveBeenCalledWith({
      approval_id: "approval-input",
      revision: 3,
      decision: "replace",
      replacement_text: "最终决策人为采购总监，法务负责合规复核。",
    });
  });

  it("rejects an approval without a paid confirmation and can reopen expiry", () => {
    vi.stubGlobal("fetch", vi.fn(() => historyResponse()));
    const onInteractionDecision = vi.fn();
    const onInteractionReopen = vi.fn();
    const approvalRun: AgencyDagRun = {
      ...run,
      status: "waiting",
      final_output: null,
      pending_interaction: {
        approval_id: "approval-gate",
        step_id: "release_gate",
        kind: "approval",
        prompt: "是否允许进入最终交付？",
        allowed_decisions: ["approve", "reject"],
        revision: 2,
        status: "pending",
        created_at: 1,
        updated_at: 1,
        expires_at: 100,
      },
    };
    const view = render(<AgencyDagRunPanel {...panelProps({
      run: approvalRun,
      onInteractionDecision,
      onInteractionReopen,
    })} />);
    fireEvent.change(screen.getByPlaceholderText(/拒绝原因/), {
      target: { value: "预算依据尚未确认" },
    });
    fireEvent.click(screen.getByRole("button", { name: "拒绝并终止任务" }));
    expect(screen.queryByText("恢复下游执行前请确认")).not.toBeInTheDocument();
    expect(onInteractionDecision).toHaveBeenCalledWith({
      approval_id: "approval-gate",
      revision: 2,
      decision: "reject",
      message: "预算依据尚未确认",
    });

    view.rerender(<AgencyDagRunPanel {...panelProps({
      run: {
        ...approvalRun,
        pending_interaction: {
          ...approvalRun.pending_interaction!,
          status: "expired",
          revision: 3,
        },
      },
      onInteractionDecision,
      onInteractionReopen,
    })} />);
    fireEvent.click(screen.getByRole("button", { name: "重新开启 24 小时" }));
    expect(onInteractionReopen).toHaveBeenCalledTimes(1);
  });

  it("keeps historical interaction visible but disables actions when HITL is off", () => {
    vi.stubGlobal("fetch", vi.fn(() => historyResponse()));
    const onInteractionDecision = vi.fn();
    render(<AgencyDagRunPanel {...panelProps({
      capabilities: {
        ...capabilities,
        hitl: { ...capabilities.hitl!, enabled: false },
      },
      run: {
        ...run,
        status: "waiting",
        pending_interaction: {
          approval_id: "disabled-input",
          step_id: "audience_input",
          kind: "human_input",
          prompt: "请补充受众。",
          allowed_decisions: ["replace"],
          revision: 1,
          status: "pending",
          created_at: 1,
          updated_at: 1,
          expires_at: 100,
        },
      },
      onInteractionDecision,
    })} />);
    expect(screen.getByText(/人工交互开关已关闭/)).toBeVisible();
    expect(screen.getByRole("button", { name: "检查费用并提交" })).toBeDisabled();
    expect(onInteractionDecision).not.toHaveBeenCalled();
  });
});
