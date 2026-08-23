import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SkillHookApplicationCard, {
  hookSkillIdsFromWorkflowNodes,
  type SkillHookStatusEventLike,
} from "./SkillHookApplicationCard";

describe("SkillHookApplicationCard", () => {
  it("renders a stable placeholder before the first Hook boundary", () => {
    render(<SkillHookApplicationCard events={[]} expectedSkillIds={["release-guard"]} />);

    expect(screen.getByText("release-guard")).toBeVisible();
    expect(screen.getByText("等待事件边界")).toBeVisible();
    expect(screen.getByText(/Typed V2/)).toBeVisible();
  });

  it("shows typed outcomes without exposing arguments or result bodies", () => {
    const events: SkillHookStatusEventLike[] = [
      {
        event: "skill_hook_status",
        status: "denied",
        skill_id: "release-guard",
        hook_id: "check-release-name",
        hook_event: "pre_tool_use",
        hook_mode: "guard",
        tool_name: "sandbox_write_file",
        code: "unsafe_release_name",
      },
      {
        event: "skill_hook_status",
        status: "failed",
        skill_id: "release-audit",
        hook_id: "verify-release-output",
        hook_event: "post_tool_use",
        hook_mode: "validation",
        tool_name: "sandbox_write_file",
        code: "extension_mismatch",
      },
    ];

    render(<SkillHookApplicationCard events={events} />);

    expect(screen.getByText("release-guard / check-release-name")).toBeVisible();
    expect(screen.getByText("已阻断")).toBeVisible();
    expect(screen.getByText(/工具已执行，副作用未自动回滚/)).toBeVisible();
    expect(screen.queryByText(/arguments/i)).not.toBeInTheDocument();
  });

  it("extracts typed Hook selections but never upgrades Legacy nodes", () => {
    expect(hookSkillIdsFromWorkflowNodes([
      {
        data: {
          kind: "runtime_middleware",
          runtimeMiddlewareId: "plugin_hooks",
          runtimeMiddlewareConfig: {
            hook_mode: "typed_v2",
            skill_ids: "release-guard, audit-hook\nrelease-guard",
          },
        },
      },
      {
        data: {
          kind: "runtime_middleware",
          runtimeMiddlewareId: "plugin_hooks",
          runtimeMiddlewareConfig: {
            skill_ids: "legacy-hook",
            fail_closed: true,
          },
        },
      },
    ])).toEqual(["release-guard", "audit-hook"]);
  });
});
