import type { RuntimeMiddlewareNode } from "../types/runtimeMiddleware";
import type { WorkflowEdge, WorkflowNode } from "../types/workflow";

const SKILL_RUNTIME_ID = "skills_runtime";
const HUMAN_IN_THE_LOOP_ID = "human_in_the_loop";
const SKILL_INSTALL_TOOL = "skill_install";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEnabled(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    return !["", "0", "false", "no", "off"].includes(value.trim().toLowerCase());
  }
  return false;
}

function approvalTools(value: unknown): string[] {
  return String(value ?? "")
    .split(/[,\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function ensureApprovalTool(value: unknown, toolName: string): string {
  const tools = approvalTools(value);
  if (tools.includes("*") || tools.includes(toolName)) {
    return String(value ?? "");
  }
  return [...tools, toolName].join(", ");
}

function middlewareConfig(node: WorkflowNode): Record<string, unknown> {
  return isRecord(node.data.runtimeMiddlewareConfig)
    ? node.data.runtimeMiddlewareConfig
    : {};
}

function middlewareTarget(edges: WorkflowEdge[], nodeId: string): string | undefined {
  return edges.find(
    (edge) => edge.source === nodeId && edge.targetHandle === "middleware",
  )?.target;
}

function hasSkillInstallCoverage(node: WorkflowNode): boolean {
  const tools = approvalTools(middlewareConfig(node).interrupt_on_tools);
  return tools.includes("*") || tools.includes(SKILL_INSTALL_TOOL);
}

function createApprovalNode(
  definition: RuntimeMiddlewareNode,
  skillNode: WorkflowNode,
  nodeId: string,
): WorkflowNode {
  const config = definition.fields.reduce<Record<string, unknown>>((result, field) => {
    if (field.default !== undefined) result[field.name] = field.default;
    return result;
  }, {});
  return {
    id: nodeId,
    type: "workflowNode",
    position: {
      x: skillNode.position.x,
      y: skillNode.position.y + 220,
    },
    data: {
      kind: "runtime_middleware",
      title: definition.title,
      description: definition.description,
      runtimeMiddlewareId: definition.id,
      runtimeMiddlewareKind: definition.kind,
      runtimeMiddlewareFields: definition.fields,
      runtimeMiddlewareMetadata: definition.metadata ?? {},
      runtimeMiddlewareConfig: {
        ...config,
        interrupt_on_tools: SKILL_INSTALL_TOOL,
        allow_edit: false,
        description_prefix: "目录 Skill 安装需要人工审批",
      },
      middlewarePriority: "100",
      autoConfiguredForSkillRuntimeId: skillNode.id,
    },
  };
}

function uniqueAutoNodeId(nodes: WorkflowNode[], skillNodeId: string): string {
  const base = `runtime-middleware-auto-hitl-${skillNodeId}`;
  if (!nodes.some((node) => node.id === base)) return base;
  let suffix = 2;
  while (nodes.some((node) => node.id === `${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

export interface SkillCatalogApprovalState {
  enabled: boolean;
  searchEnabled: boolean;
  targetAgentId?: string;
  approvalNodeId?: string;
  covered: boolean;
}

export function getSkillCatalogApprovalState(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  skillNodeId: string,
): SkillCatalogApprovalState {
  const skillNode = nodes.find((node) => node.id === skillNodeId);
  const config = skillNode ? middlewareConfig(skillNode) : {};
  const targetAgentId = middlewareTarget(edges, skillNodeId);
  const approvalNode = targetAgentId
    ? nodes.find(
        (node) =>
          node.data.kind === "runtime_middleware" &&
          node.data.runtimeMiddlewareId === HUMAN_IN_THE_LOOP_ID &&
          middlewareTarget(edges, node.id) === targetAgentId &&
          hasSkillInstallCoverage(node),
      )
    : undefined;
  const configuredApprovalNode =
    approvalNode ??
    nodes.find(
      (node) =>
        node.data.kind === "runtime_middleware" &&
        node.data.runtimeMiddlewareId === HUMAN_IN_THE_LOOP_ID &&
        node.data.autoConfiguredForSkillRuntimeId === skillNodeId &&
        hasSkillInstallCoverage(node),
    );
  return {
    enabled: isEnabled(config.catalog_install),
    searchEnabled: isEnabled(config.catalog_search),
    targetAgentId,
    approvalNodeId: configuredApprovalNode?.id,
    covered: Boolean(targetAgentId && approvalNode),
  };
}

export interface SkillCatalogApprovalReconcileResult {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export function reconcileSkillCatalogApprovals(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  approvalDefinition: RuntimeMiddlewareNode,
): SkillCatalogApprovalReconcileResult {
  const skillNodes = nodes.filter(
    (node) =>
      node.data.kind === "runtime_middleware" &&
      node.data.runtimeMiddlewareId === SKILL_RUNTIME_ID &&
      isEnabled(middlewareConfig(node).catalog_install),
  );
  if (skillNodes.length === 0) return { nodes, edges };

  let nextNodes = nodes;
  let nextEdges = edges;

  skillNodes.forEach((originalSkillNode) => {
    let skillNode = nextNodes.find((node) => node.id === originalSkillNode.id);
    if (!skillNode) return;
    const skillConfig = middlewareConfig(skillNode);
    if (!isEnabled(skillConfig.catalog_search)) {
      skillNode = {
        ...skillNode,
        data: {
          ...skillNode.data,
          runtimeMiddlewareConfig: {
            ...skillConfig,
            catalog_search: true,
          },
        },
      };
      nextNodes = nextNodes.map((node) =>
        node.id === skillNode?.id ? skillNode : node,
      );
    }

    const targetAgentId = middlewareTarget(nextEdges, skillNode.id);
    let approvalNode = targetAgentId
      ? nextNodes.find(
          (node) =>
            node.data.kind === "runtime_middleware" &&
            node.data.runtimeMiddlewareId === HUMAN_IN_THE_LOOP_ID &&
            middlewareTarget(nextEdges, node.id) === targetAgentId,
        )
      : undefined;

    if (!approvalNode) {
      approvalNode = nextNodes.find((node) => {
        if (
          node.data.kind !== "runtime_middleware" ||
          node.data.runtimeMiddlewareId !== HUMAN_IN_THE_LOOP_ID ||
          node.data.autoConfiguredForSkillRuntimeId !== skillNode?.id
        ) {
          return false;
        }
        const existingTarget = middlewareTarget(nextEdges, node.id);
        return !existingTarget || existingTarget === targetAgentId;
      });
    }

    if (!approvalNode) {
      approvalNode = createApprovalNode(
        approvalDefinition,
        skillNode,
        uniqueAutoNodeId(nextNodes, skillNode.id),
      );
      nextNodes = [...nextNodes, approvalNode];
    } else {
      const config = middlewareConfig(approvalNode);
      const nextTools = ensureApprovalTool(
        config.interrupt_on_tools,
        SKILL_INSTALL_TOOL,
      );
      if (nextTools !== String(config.interrupt_on_tools ?? "")) {
        const updatedApprovalNode: WorkflowNode = {
          ...approvalNode,
          data: {
            ...approvalNode.data,
            runtimeMiddlewareConfig: {
              ...config,
              interrupt_on_tools: nextTools,
            },
          },
        };
        nextNodes = nextNodes.map((node) =>
          node.id === updatedApprovalNode.id ? updatedApprovalNode : node,
        );
        approvalNode = updatedApprovalNode;
      }
    }

    if (
      targetAgentId &&
      !nextEdges.some(
        (edge) =>
          edge.source === approvalNode?.id &&
          edge.target === targetAgentId &&
          edge.targetHandle === "middleware",
      )
    ) {
      nextEdges = [
        ...nextEdges,
        {
          id: `middleware-auto-${approvalNode.id}-${targetAgentId}`,
          source: approvalNode.id,
          sourceHandle: "middleware-binding",
          target: targetAgentId,
          targetHandle: "middleware",
          animated: true,
          className: "modelmirror-workflow-edge modelmirror-middleware-binding-edge",
          style: { stroke: "#a5b4fc", strokeDasharray: "7 5", strokeWidth: 2 },
        },
      ];
    }
  });

  return { nodes: nextNodes, edges: nextEdges };
}
