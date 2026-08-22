import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SkillApplicationCard, {
  buildSkillApplicationStates,
  requiredSkillIdsFromWorkflowNodes,
  type SkillRuntimeStatusEventLike,
} from "./SkillApplicationCard";

describe("SkillApplicationCard", () => {
  it("keeps required and optional sources distinct while aggregating evidence", () => {
    const events: SkillRuntimeStatusEventLike[] = [
      { event: "skill_runtime_status", status: "required", skill_id: "pdf" },
      { event: "skill_runtime_status", status: "available", skill_id: "plugin-helper" },
      { event: "skill_runtime_status", status: "reading", skill_id: "plugin-helper" },
      { event: "skill_runtime_status", status: "repair_requested", required_skill_ids: ["pdf"] },
      { event: "skill_runtime_status", status: "reading", skill_id: "pdf", resource_paths: ["SKILL.md"] },
      { event: "skill_runtime_status", status: "verified", required_skill_ids: ["pdf"] },
      { event: "skill_runtime_status", status: "staged", skill_id: "pdf", resource_count: 3, resource_paths: ["SKILL.md", "references/guide.md"] },
      { event: "skill_runtime_status", status: "resource_accessed", skill_id: "pdf", resource_count: 1, resource_paths: ["references/guide.md"] },
    ];

    expect(buildSkillApplicationStates(events)).toMatchObject([
      {
        skillId: "pdf",
        requirement: "required",
        read: true,
        verified: true,
        repairRequested: true,
        stagedResourceCount: 3,
        accessedResourceCount: 1,
      },
      {
        skillId: "plugin-helper",
        requirement: "available",
        read: true,
        verified: false,
      },
    ]);
  });

  it("renders a stable waiting row before the first runtime event", () => {
    render(<SkillApplicationCard events={[]} expectedRequiredSkillIds={["tdd"]} />);

    expect(screen.getByText("tdd")).toBeVisible();
    expect(screen.getByText("等待读取")).toBeVisible();
    expect(screen.getByText(/读取证明交付/)).toBeVisible();
  });

  it("extracts only explicit skills_runtime selections", () => {
    expect(requiredSkillIdsFromWorkflowNodes([
      {
        data: {
          kind: "runtime_middleware",
          runtimeMiddlewareId: "skills_runtime",
          runtimeMiddlewareConfig: { skill_ids: "pdf, tdd\npdf", auto_discover: true },
        },
      },
      {
        data: {
          kind: "plugin_resource",
          runtimeMiddlewareConfig: { skill_ids: "plugin-helper" },
        },
      },
    ])).toEqual(["pdf", "tdd"]);
  });
});
