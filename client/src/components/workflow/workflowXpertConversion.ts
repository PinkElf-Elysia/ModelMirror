import {
  type WorkflowDefinition,
  type WorkflowNode,
  type WorkflowNodeKind,
} from "../../types/workflow";
import {
  type NativeValidateResponse,
  type NativeWorkflowDefinition,
} from "../../types/workflow-native";
import {
  type WorkflowNodeRegistryResponse,
  type WorkflowPaletteItem,
} from "./workflowNodeRegistry";
import { analyzeWorkflowVariables } from "./workflowVariables";

export const INDEPENDENT_DEPLOYMENT_NODE_KINDS = new Set<WorkflowNodeKind>([
  "scheduled_start",
  "http_event_entry",
  "form_event_entry",
  "rss_event_entry",
  "email_event_entry",
  "failure_event_entry",
  "workflow_call_entry",
  "invoke_workflow",
  "http_event_reply",
  "suspend_wait",
]);

export interface XpertConversionAnalysis {
  status: "ready" | "selection_required" | "blocked";
  blockers: string[];
  inputCandidates: string[];
  selectedInputVariable: string;
  outputVariable: string;
  convertedEntryNodeId: string | null;
  definition: WorkflowDefinition | null;
}

function cloneDefinition(definition: WorkflowDefinition): WorkflowDefinition {
  return structuredClone(definition);
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function containsTemplateVariable(value: unknown, variableName: string): boolean {
  if (typeof value === "string") {
    const escaped = variableName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`{{\\s*${escaped}\\s*}}`).test(value);
  }
  if (Array.isArray(value)) {
    return value.some((item) => containsTemplateVariable(item, variableName));
  }
  if (value && typeof value === "object") {
    return Object.values(value).some((item) =>
      containsTemplateVariable(item, variableName),
    );
  }
  return false;
}

function registryItems(registry: WorkflowNodeRegistryResponse): WorkflowPaletteItem[] {
  return [
    ...registry.sections.flatMap((section) => section.items),
    ...registry.knowledge_pipeline.items,
  ];
}

function xpertAvailabilityState(item: WorkflowPaletteItem): string {
  const xpert = item.contract?.availability.xpert;
  if (!xpert || typeof xpert !== "object" || Array.isArray(xpert)) return "";
  const state = (xpert as Record<string, unknown>).state;
  return typeof state === "string" ? state : "";
}

function xpertAvailabilityMessage(item: WorkflowPaletteItem): string {
  const xpert = item.contract?.availability.xpert;
  if (!xpert || typeof xpert !== "object" || Array.isArray(xpert)) return "";
  const message = (xpert as Record<string, unknown>).message;
  return typeof message === "string" ? message.trim() : "";
}

function availabilityBlockers(
  nodes: WorkflowNode[],
  registry: WorkflowNodeRegistryResponse,
  convertibleEntryId: string | null,
): string[] {
  const items = new Map(registryItems(registry).map((item) => [item.kind, item]));
  const blockers: string[] = [];
  for (const node of nodes) {
    if (node.id === convertibleEntryId) continue;
    const item = items.get(node.data.kind as WorkflowPaletteItem["kind"]);
    if (!item || item.enabled === false || !item.contract) {
      blockers.push(
        `${node.data.title || node.id}：Registry 未提供可用的 Xpert 节点合同。`,
      );
      continue;
    }
    if (xpertAvailabilityState(item) !== "allow") {
      const independentMessage = INDEPENDENT_DEPLOYMENT_NODE_KINDS.has(
        node.data.kind,
      )
        ? "该节点属于独立发布运行面，不能进入智能体工作流。"
        : "";
      blockers.push(
        `${node.data.title || node.id}：${
          independentMessage ||
          xpertAvailabilityMessage(item) ||
          "该节点不能用于智能体工作流。"
        }`,
      );
    }
  }
  return blockers;
}

function initialResult(): XpertConversionAnalysis {
  return {
    status: "blocked",
    blockers: [],
    inputCandidates: [],
    selectedInputVariable: "",
    outputVariable: "",
    convertedEntryNodeId: null,
    definition: null,
  };
}

/**
 * Analyze a classic workflow without mutating it. A callable entry is replaced only in
 * the returned Xpert copy; the matching global input declaration is removed because the
 * input node becomes that variable's producer.
 */
export function analyzeXpertWorkflowConversion(
  definition: WorkflowDefinition,
  registry: WorkflowNodeRegistryResponse,
  selectedInputVariable?: string,
): XpertConversionAnalysis {
  const result = initialResult();
  const inputNodes = definition.nodes.filter((node) => node.data.kind === "input");
  const callEntries = definition.nodes.filter(
    (node) => node.data.kind === "workflow_call_entry",
  );
  const outputNodes = definition.nodes.filter((node) => node.data.kind === "output");
  const agentNodes = definition.nodes.filter(
    (node) => node.data.kind === "workflow_agent",
  );

  if (inputNodes.length > 1) {
    result.blockers.push("工作流包含多个普通入口；智能体只能保留一个入口。");
  }
  if (callEntries.length > 1) {
    result.blockers.push("工作流包含多个子流程入口，无法确定要转换的入口。");
  }
  if (inputNodes.length === 1 && callEntries.length > 0) {
    result.blockers.push("普通入口与子流程入口同时存在，无法自动转换。");
  }
  if (outputNodes.length !== 1) {
    result.blockers.push("智能体草稿必须恰好包含一个输出节点。");
  } else {
    result.outputVariable = text(outputNodes[0].data.outputVariable);
    if (!result.outputVariable) {
      result.blockers.push("输出节点尚未选择最终输出变量。");
    }
  }
  if (agentNodes.length === 0) {
    result.blockers.push("智能体草稿至少需要一个工作流智能体节点。");
  }

  let convertedEntry: WorkflowNode | null = null;
  if (inputNodes.length === 0) {
    if (callEntries.length !== 1) {
      result.blockers.push("没有可转换的唯一子流程入口。");
    } else {
      convertedEntry = callEntries[0];
      result.convertedEntryNodeId = convertedEntry.id;
      const eventVariable = text(convertedEntry.data.eventVariable) || "call_event";
      const eventDescriptor = analyzeWorkflowVariables(
        definition.nodes,
        definition.edges,
        null,
        definition.variables ?? [],
      ).find((variable) => variable.name === eventVariable);
      if ((eventDescriptor?.references.length ?? 0) > 0) {
        result.blockers.push(
          `子流程调用上下文变量 ${eventVariable} 已被下游引用，转换会改变语义。`,
        );
      }
    }
  }

  result.blockers.push(
    ...availabilityBlockers(definition.nodes, registry, convertedEntry?.id ?? null),
  );

  if (inputNodes.length === 1) {
    result.selectedInputVariable = text(inputNodes[0].data.variableName);
    if (!result.selectedInputVariable) {
      result.blockers.push("普通入口尚未声明输入变量。");
    }
    result.blockers = unique(result.blockers);
    if (result.blockers.length > 0) return result;
    result.status = "ready";
    result.definition = cloneDefinition(definition);
    return result;
  }

  if (!convertedEntry) {
    result.blockers = unique(result.blockers);
    return result;
  }

  const declaredInputs = unique(
    (definition.variables ?? [])
      .filter((variable) => variable.kind === "input")
      .map((variable) => variable.name.trim()),
  );
  const userInputReferenced = definition.nodes.some((node) =>
    containsTemplateVariable(node.data, "user_input"),
  );
  result.inputCandidates = unique([
    ...(declaredInputs.includes("user_input") || userInputReferenced
      ? ["user_input"]
      : []),
    ...declaredInputs,
  ]);
  if (result.inputCandidates.length === 0) {
    result.inputCandidates = ["user_input"];
  }

  const explicitSelection = text(selectedInputVariable);
  result.selectedInputVariable =
    explicitSelection ||
    (result.inputCandidates.includes("user_input")
      ? "user_input"
      : result.inputCandidates[0]);

  if (
    explicitSelection &&
    !result.inputCandidates.includes(result.selectedInputVariable)
  ) {
    result.blockers.push("所选智能体输入不在可转换的输入候选中。");
  }

  if (result.inputCandidates.length > 1 && !explicitSelection) {
    result.blockers = unique(result.blockers);
    if (result.blockers.length > 0) return result;
    result.status = "selection_required";
    return result;
  }

  const otherRequiredInputs = (definition.variables ?? []).filter(
    (variable) =>
      variable.kind === "input" &&
      variable.name !== result.selectedInputVariable &&
      variable.defaultValue === undefined,
  );
  if (otherRequiredInputs.length > 0) {
    result.blockers.push(
      `其他必填输入没有默认值：${otherRequiredInputs
        .map((variable) => variable.name)
        .join("、")}。`,
    );
  }

  result.blockers = unique(result.blockers);
  if (result.blockers.length > 0) return result;

  const converted = cloneDefinition(definition);
  converted.nodes = converted.nodes.map((node) =>
    node.id === convertedEntry?.id
      ? {
          ...node,
          data: {
            kind: "input",
            title: "智能体输入",
            description: "接收用户发给智能体的任务。",
            variableName: result.selectedInputVariable,
          },
        }
      : node,
  );
  converted.variables = (converted.variables ?? []).filter(
    (variable) =>
      !(
        variable.kind === "input" &&
        variable.name === result.selectedInputVariable
      ),
  );
  result.status = "ready";
  result.definition = converted;
  return result;
}

function toNativeDefinition(
  definition: WorkflowDefinition,
): NativeWorkflowDefinition {
  return {
    id: definition.id,
    title: definition.title,
    updatedAt: definition.updatedAt,
    version: "xpert-conversion-v1",
    source: "classic",
    variables: definition.variables ?? [],
    nodes: definition.nodes,
    edges: definition.edges,
  };
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Preserve the stable fallback below when an intermediary returns non-JSON.
  }
  return `静态图校验不可用（HTTP ${response.status}）。`;
}

export async function validateXpertConversionGraph(
  definition: WorkflowDefinition,
  historyVariable = "conversation_history",
): Promise<NativeValidateResponse> {
  const response = await fetch("/api/workflow-native/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workflow: toNativeDefinition(definition) }),
  });
  if (!response.ok) throw new Error(await responseMessage(response));
  const result = (await response.json()) as NativeValidateResponse;
  const injectedHistoryReference = `variable '${historyVariable}'`;
  const issues = result.issues.filter(
    (issue) =>
      !(
        issue.code.endsWith("template_variable") &&
        issue.message.includes(injectedHistoryReference)
      ),
  );
  return {
    ...result,
    issues,
    valid: !issues.some((issue) => issue.severity === "error"),
  };
}
