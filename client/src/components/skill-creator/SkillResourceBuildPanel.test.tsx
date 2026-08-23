import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SkillCreatorSession,
  SkillCreatorStatus,
  SkillResourceBuild,
  SkillResourcePlan,
} from "../../utils/skillCreatorApi";
import SkillResourceBuildPanel from "./SkillResourceBuildPanel";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const plan: SkillResourcePlan = {
  plan_id: "plan_1",
  session_id: "creator_1",
  revision: 2,
  digest: "b".repeat(64),
  state: "confirmed",
  session_revision: 3,
  skill_name: "review-incidents",
  skill_description: "Create incident reviews when users need a factual timeline.",
  workflow_steps: [{ step_id: "collect", instruction: "Collect facts." }],
  output_contract: ["Return Markdown."],
  failure_modes: ["Mark missing facts."],
  resources: [],
  clarifications: [],
  clarification_answers: {},
  created_at: 1,
  updated_at: 2,
};

const status: SkillCreatorStatus = {
  enabled: true,
  version: "skill-creator-v2",
  model_available: true,
  assistant_agent_id: "skill-creator-assistant-v1",
  supported_sources: ["blank"],
  resource_authoring_enabled: true,
  resource_planner_available: true,
  resource_build_enabled: true,
  resource_builder_available: true,
  script_sandbox_configured: true,
};

const baseSession: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 3,
  draft_state_revision: 0,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  authoring_flow: "resource",
  intent: "Create factual incident reviews.",
  positive_examples: ["Review this incident."],
  near_miss_examples: ["Rewrite this sentence."],
  expected_output: "Markdown report.",
  success_criteria: ["Do not invent facts."],
  selected_evidence: [],
  evidence_confirmed: true,
  state: "selecting_evidence",
  resource_plan: plan,
  created_at: 1,
  updated_at: 2,
};

const build: SkillResourceBuild = {
  build_id: "build_1",
  session_id: "creator_1",
  revision: 5,
  digest: "c".repeat(64),
  state: "awaiting_review",
  phase: "resources",
  session_revision: 3,
  plan_id: plan.plan_id,
  plan_revision: plan.revision,
  plan_digest: plan.digest,
  skill_name: plan.skill_name,
  skill_description: plan.skill_description,
  workflow_steps: plan.workflow_steps,
  output_contract: plan.output_contract,
  failure_modes: plan.failure_modes,
  resources: [{
    resource_id: "resource_1",
    spec_digest: "d".repeat(64),
    kind: "reference",
    action: "create",
    path: "references/evidence-policy.md",
    purpose: "Keep detailed evidence rules separate.",
    source_ids: ["intent"],
    used_by_steps: ["collect"],
    depends_on: [],
    acceptance_checks: ["Defines unsupported claims."],
    state: "awaiting_review",
    attempt: 1,
    repair_count: 0,
    chunks: ["# Evidence policy\n\nUse facts.\n"],
    content: "# Evidence policy\n\nUse facts.\n",
    content_digest: "e".repeat(64),
    script_tests: [],
    validation_issues: [],
    feedback: "",
  }],
  current_resource_id: "resource_1",
  skill_chunks: [],
  skill_attempt: 1,
  skill_repair_count: 0,
  skill_validation_issues: [],
  skill_feedback: "",
  requirement_coverage: [],
  created_at: 1,
  updated_at: 2,
};

afterEach(() => vi.unstubAllGlobals());

describe("SkillResourceBuildPanel", () => {
  it("starts a build only after the immutable plan is confirmed", async () => {
    const started = { ...build, state: "planned" as const, phase: "resources" as const };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((input, _init) => String(input).endsWith("/next")
      ? jsonResponse({ resource_build: build })
      : jsonResponse({ resource_build: started }, 201));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={baseSession} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "开始生成内容" }));

    expect(await screen.findByRole("heading", { name: "逐项生成内容" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resource-build")).toBe(true);
    expect(JSON.parse(String(init?.body))).toMatchObject({
      plan_id: plan.plan_id,
      expected_session_revision: baseSession.session_revision,
      expected_plan_revision: plan.revision,
      expected_plan_digest: plan.digest,
    });
  });

  it("offers a replacement build when the retained build belongs to an older plan", async () => {
    const currentPlan = { ...plan, revision: 4, digest: "4".repeat(64) };
    const replacement = {
      ...build,
      build_id: "build_2",
      revision: 1,
      digest: "5".repeat(64),
      state: "planned" as const,
      plan_revision: currentPlan.revision,
      plan_digest: currentPlan.digest,
    };
    const refresh = vi.fn();
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((input, _init) => String(input).endsWith("/next")
      ? jsonResponse({ resource_build: { ...replacement, state: "awaiting_review", resources: build.resources } })
      : jsonResponse({ resource_build: replacement }, 201));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={refresh}
      session={{
        ...baseSession,
        resource_plan: currentPlan,
        resource_build: { ...build, state: "stale", stale: true },
      }}
      status={status}
    />);

    expect(screen.getByText(/旧版本仍会只读保留/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "按新方案开始生成" }));

    expect(await screen.findByRole("heading", { name: "逐项生成内容" })).toBeVisible();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resource-build")).toBe(true);
    expect(JSON.parse(String(init?.body))).toMatchObject({
      plan_id: currentPlan.plan_id,
      expected_plan_revision: currentPlan.revision,
      expected_plan_digest: currentPlan.digest,
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("saves a complete direct edit as a new server revision", async () => {
    const edited = {
      ...build,
      revision: 6,
      digest: "f".repeat(64),
      resources: [{
        ...build.resources[0],
        content: "# Evidence policy\n\nUse facts and mark gaps.\n",
      }],
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((_input, _init) => jsonResponse({ resource_build: edited }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={{ ...baseSession, resource_build: build }} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "直接编辑完整资源" }));
    const editor = screen.getByLabelText("编辑完整资源");
    await userEvent.clear(editor);
    await userEvent.type(editor, "# Evidence policy\n\nUse facts and mark gaps.\n");
    await userEvent.click(screen.getByRole("button", { name: "保存资源 revision" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resources/resource_1")).toBe(true);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      expected_revision: build.revision,
      expected_digest: build.digest,
      content: "# Evidence policy\n\nUse facts and mark gaps.\n",
    });
    expect(await screen.findByText(/已保存 references\/evidence-policy.md 的新构建 revision/)).toBeVisible();
  });

  it("follows the server-frozen current resource after advancing", async () => {
    const nextResource = {
      ...build,
      revision: 6,
      digest: "9".repeat(64),
      current_resource_id: "resource_2",
      resources: [
        { ...build.resources[0], state: "accepted" as const },
        {
          ...build.resources[0],
          resource_id: "resource_2",
          path: "assets/report-template.md",
          kind: "asset" as const,
          state: "awaiting_review" as const,
          content: "# Report template\n",
          content_digest: "8".repeat(64),
        },
      ],
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((_input, _init) => jsonResponse({ resource_build: nextResource }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={{ ...baseSession, resource_build: { ...build, state: "planned" } }} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "生成下一个资源" }));

    expect(await screen.findByRole("heading", { name: "assets/report-template.md" })).toBeVisible();
    expect(screen.getByText("# Report template")).toBeVisible();
  });

  it("regenerates a rejected resource in the same user action", async () => {
    const rejected: SkillResourceBuild = {
      ...build,
      revision: 6,
      digest: "6".repeat(64),
      state: "revision_requested",
      current_resource_id: null,
      resources: [{
        ...build.resources[0],
        state: "revision_requested",
        content: null,
        content_digest: null,
        chunks: [],
        feedback: "Keep the frozen path and improve the rule detail.",
      }],
    };
    const regenerated: SkillResourceBuild = {
      ...build,
      revision: 8,
      digest: "8".repeat(64),
      resources: [{
        ...build.resources[0],
        content: "# Evidence policy\n\nUse explicit facts and mark every gap.\n",
        content_digest: "9".repeat(64),
      }],
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((input) => String(input).endsWith("/next")
      ? jsonResponse({ resource_build: regenerated })
      : jsonResponse({ resource_build: rejected }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_build: build }}
      status={status}
    />);
    await userEvent.type(
      screen.getByLabelText("重做反馈"),
      "Keep the frozen path and improve the rule detail.",
    );
    await userEvent.click(screen.getByRole("button", { name: "按反馈重做" }));

    expect(await screen.findByText(/已按反馈重新生成并完成基础检查/)).toBeVisible();
    expect(screen.getByText(/Use explicit facts and mark every gap/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/review");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/next");
  });

  it("regenerates a rejected final SKILL.md in the same user action", async () => {
    const finalBuild: SkillResourceBuild = {
      ...build,
      phase: "skill_markdown",
      state: "awaiting_review",
      current_resource_id: null,
      resources: [{ ...build.resources[0], state: "accepted" }],
      skill_markdown: "# First version\n",
      skill_markdown_digest: "f".repeat(64),
    };
    const rejected: SkillResourceBuild = {
      ...finalBuild,
      revision: 6,
      digest: "6".repeat(64),
      state: "revision_requested",
      skill_markdown: null,
      skill_markdown_digest: null,
      skill_feedback: "Describe the typed Hook output contract exactly.",
    };
    const regenerated: SkillResourceBuild = {
      ...rejected,
      revision: 8,
      digest: "8".repeat(64),
      state: "awaiting_review",
      skill_markdown: "# Corrected version\n\nUse typed validation and deny outputs.\n",
      skill_markdown_digest: "9".repeat(64),
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((input) => String(input).endsWith("/next")
      ? jsonResponse({ resource_build: regenerated })
      : jsonResponse({ resource_build: rejected }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_build: finalBuild }}
      status={status}
    />);
    await userEvent.type(
      screen.getByLabelText("最终文档反馈"),
      "Describe the typed Hook output contract exactly.",
    );
    await userEvent.click(screen.getByRole("button", { name: "按反馈重做 SKILL.md" }));

    expect(await screen.findByText(/已按反馈重新生成并完成校验/)).toBeVisible();
    expect(screen.getByText(/Use typed validation and deny outputs/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/finalize");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/next");
  });

  it("does not present passing CLI fixtures as a passing Hook contract", () => {
    const hookFailed: SkillResourceBuild = {
      ...build,
      state: "failed",
      resources: [{
        ...build.resources[0],
        kind: "script",
        state: "failed",
        validation_issues: [{
          code: "skill_creator_hook_test_failed",
          message: "Generated Hook script failed its typed offline contract tests.",
          path: "scripts/check.py",
          severity: "error",
        }],
        script_receipt: {
          receipt_id: "script_receipt_1",
          script_digest: "a".repeat(64),
          profile: "skill_authoring_v1",
          passed: true,
          created_at: 1,
          results: [{
            test_id: "case_1",
            passed: true,
            exit_code: 0,
            stdout_sha256: "b".repeat(64),
            stderr_sha256: "c".repeat(64),
            duration_ms: 5,
            issues: [],
          }],
        },
      }],
    };

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_build: hookFailed }}
      status={status}
    />);

    expect(screen.getByText("基础 CLI 通过")).toBeVisible();
    expect(screen.getByText(/Hook 的类型化 context\/result 合同未通过/)).toBeVisible();
    expect(screen.queryByText("全部通过")).not.toBeInTheDocument();
  });

  it("shows one Hook validation boundary and keeps the manifest read-only", () => {
    const hookBuild: SkillResourceBuild = {
      ...build,
      state: "planned",
      current_resource_id: null,
      resources: [{ ...build.resources[0], state: "accepted" }],
      hooks: [{
        hook_id: "check-release-name",
        spec_digest: "1".repeat(64),
        event: "pre_tool_use",
        mode: "guard",
        tool_names: ["sandbox_write_file"],
        purpose: "Block unsafe release filenames.",
        script_resource_id: "resource_1",
        source_ids: ["intent"],
        used_by_steps: ["collect"],
        acceptance_checks: ["Deny executable suffixes."],
        action: "create",
      }],
      hook_manifest: null,
      hook_manifest_digest: null,
    };

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_build: hookBuild }}
      status={{ ...status, hook_authoring_enabled: true }}
    />);

    expect(screen.getByRole("heading", { name: "Hook 合同与实测" })).toBeVisible();
    expect(screen.getByText("等待离线实测")).toBeVisible();
    expect(screen.getByRole("button", { name: "实测 Hook 并生成 SKILL.md" })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: /manifest/i })).not.toBeInTheDocument();
  });

  it("fails closed before starting or advancing a Hook build when Hook V2 is disabled", () => {
    const hookPlan: SkillResourcePlan = {
      ...plan,
      hooks: [{
        hook_id: "check-release-name",
        spec_digest: "1".repeat(64),
        event: "pre_tool_use",
        mode: "guard",
        tool_names: ["sandbox_write_file"],
        purpose: "Block unsafe release filenames.",
        script_resource_id: "resource_1",
        source_ids: ["intent"],
        used_by_steps: ["collect"],
        acceptance_checks: ["Deny executable suffixes."],
        action: "create",
      }],
    };

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_plan: hookPlan }}
      status={{ ...status, hook_authoring_enabled: false }}
    />);

    expect(screen.getByRole("button", { name: "开始生成内容" })).toBeDisabled();
    expect(screen.getByText(/该计划包含 Hook，但 Hook V2 当前已关闭/)).toBeVisible();
  });

  it("does not spend a model call when the Hook authoring Sidecar is unavailable", () => {
    const hookPlan: SkillResourcePlan = {
      ...plan,
      hooks: [{
        hook_id: "check-release-name",
        spec_digest: "1".repeat(64),
        event: "pre_tool_use",
        mode: "guard",
        tool_names: ["sandbox_write_file"],
        purpose: "Block unsafe release filenames.",
        script_resource_id: "resource_1",
        source_ids: ["intent"],
        used_by_steps: ["collect"],
        acceptance_checks: ["Deny executable suffixes."],
        action: "create",
      }],
    };

    render(<SkillResourceBuildPanel
      onProposal={vi.fn()}
      onSessionRefresh={vi.fn()}
      session={{ ...baseSession, resource_plan: hookPlan }}
      status={{ ...status, hook_authoring_enabled: true, script_sandbox_configured: false }}
    />);

    expect(screen.getByRole("button", { name: "开始生成内容" })).toBeDisabled();
    expect(screen.getByText(/离线 authoring Sidecar 不可用/)).toBeVisible();
  });
});
