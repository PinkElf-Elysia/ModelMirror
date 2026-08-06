import { describe, expect, it } from "vitest";
import type { RuntimeMiddlewareNode } from "../types/runtimeMiddleware";
import type { WorkflowEdge, WorkflowNode } from "../types/workflow";
import {
  getSkillCatalogApprovalState,
  reconcileSkillCatalogApprovals,
} from "./skillCatalogApproval";

const approvalDefinition: RuntimeMiddlewareNode = {
  id: "human_in_the_loop",
  kind: "runtime_middleware.human_in_the_loop",
  title: "人机审批",
  description: "等待人工批准。",
  category: "agent",
  icon: "ShieldCheck",
  enabled: true,
  fields: [
    { name: "interrupt_on_tools", label: "需审批工具", type: "textarea", default: "" },
    { name: "allow_edit", label: "允许编辑", type: "boolean", default: true },
    { name: "allow_reject", label: "允许拒绝", type: "boolean", default: true },
  ],
};

function skillNode(bound = true): {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
} {
  const nodes: WorkflowNode[] = [
    {
      id: "skill-runtime",
      type: "workflowNode",
      position: { x: 100, y: 120 },
      data: {
        kind: "runtime_middleware",
        title: "Skill 执行指导",
        description: "测试",
        runtimeMiddlewareId: "skills_runtime",
        runtimeMiddlewareConfig: {
          catalog_search: false,
          catalog_install: true,
          max_catalog_installs: 3,
        },
      },
    },
    {
      id: "agent",
      type: "workflowNode",
      position: { x: 500, y: 120 },
      data: {
        kind: "workflow_agent",
        title: "研究 Agent",
        description: "测试",
      },
    },
  ];
  return {
    nodes,
    edges: bound
      ? [
          {
            id: "skill-agent",
            source: "skill-runtime",
            sourceHandle: "middleware-binding",
            target: "agent",
            targetHandle: "middleware",
          },
        ]
      : [],
  };
}

describe("reconcileSkillCatalogApprovals", () => {
  it("enables catalog search and creates a bound skill_install approval", () => {
    const graph = skillNode();
    const result = reconcileSkillCatalogApprovals(
      graph.nodes,
      graph.edges,
      approvalDefinition,
    );
    const approval = result.nodes.find(
      (node) => node.data.runtimeMiddlewareId === "human_in_the_loop",
    );

    expect(result.nodes[0].data.runtimeMiddlewareConfig).toMatchObject({
      catalog_search: true,
      catalog_install: true,
    });
    expect(approval?.data.runtimeMiddlewareConfig).toMatchObject({
      interrupt_on_tools: "skill_install",
      allow_edit: false,
    });
    expect(result.edges).toContainEqual(
      expect.objectContaining({
        source: approval?.id,
        target: "agent",
        targetHandle: "middleware",
      }),
    );
    expect(
      getSkillCatalogApprovalState(result.nodes, result.edges, "skill-runtime"),
    ).toMatchObject({ enabled: true, searchEnabled: true, covered: true });

    const stable = reconcileSkillCatalogApprovals(
      result.nodes,
      result.edges,
      approvalDefinition,
    );
    expect(stable.nodes).toBe(result.nodes);
    expect(stable.edges).toBe(result.edges);
  });

  it("reuses the approval bound to the same Agent and preserves existing tools", () => {
    const graph = skillNode();
    graph.nodes.push({
      id: "existing-approval",
      type: "workflowNode",
      position: { x: 100, y: 360 },
      data: {
        kind: "runtime_middleware",
        title: "人机审批",
        description: "测试",
        runtimeMiddlewareId: "human_in_the_loop",
        runtimeMiddlewareConfig: { interrupt_on_tools: "write_file" },
      },
    });
    graph.edges.push({
      id: "approval-agent",
      source: "existing-approval",
      sourceHandle: "middleware-binding",
      target: "agent",
      targetHandle: "middleware",
    });

    const result = reconcileSkillCatalogApprovals(
      graph.nodes,
      graph.edges,
      approvalDefinition,
    );
    const approvals = result.nodes.filter(
      (node) => node.data.runtimeMiddlewareId === "human_in_the_loop",
    );

    expect(approvals).toHaveLength(1);
    expect(approvals[0].data.runtimeMiddlewareConfig).toMatchObject({
      interrupt_on_tools: "write_file, skill_install",
    });
  });

  it("creates an unbound approval once and connects it after the Skill node is bound", () => {
    const graph = skillNode(false);
    const first = reconcileSkillCatalogApprovals(
      graph.nodes,
      graph.edges,
      approvalDefinition,
    );
    const approval = first.nodes.find(
      (node) => node.data.runtimeMiddlewareId === "human_in_the_loop",
    );
    expect(approval).toBeDefined();
    expect(first.edges).toHaveLength(0);
    expect(
      getSkillCatalogApprovalState(first.nodes, first.edges, "skill-runtime"),
    ).toMatchObject({ approvalNodeId: approval?.id, covered: false });

    const boundEdges: WorkflowEdge[] = [
      {
        id: "skill-agent",
        source: "skill-runtime",
        sourceHandle: "middleware-binding",
        target: "agent",
        targetHandle: "middleware",
      },
    ];
    const second = reconcileSkillCatalogApprovals(
      first.nodes,
      boundEdges,
      approvalDefinition,
    );

    expect(
      second.nodes.filter(
        (node) => node.data.runtimeMiddlewareId === "human_in_the_loop",
      ),
    ).toHaveLength(1);
    expect(second.edges).toContainEqual(
      expect.objectContaining({ source: approval?.id, target: "agent" }),
    );
  });

  it("keeps wildcard approval unchanged", () => {
    const graph = skillNode();
    graph.nodes.push({
      id: "wildcard-approval",
      type: "workflowNode",
      position: { x: 100, y: 360 },
      data: {
        kind: "runtime_middleware",
        title: "人机审批",
        description: "测试",
        runtimeMiddlewareId: "human_in_the_loop",
        runtimeMiddlewareConfig: { interrupt_on_tools: "*" },
      },
    });
    graph.edges.push({
      id: "approval-agent",
      source: "wildcard-approval",
      sourceHandle: "middleware-binding",
      target: "agent",
      targetHandle: "middleware",
    });

    const result = reconcileSkillCatalogApprovals(
      graph.nodes,
      graph.edges,
      approvalDefinition,
    );
    expect(result.nodes[2]).toBe(graph.nodes[2]);
  });
});
