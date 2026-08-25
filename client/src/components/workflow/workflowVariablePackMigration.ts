import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowVariablePackBinding,
} from "../../types/workflow";

export interface WorkflowVariablePackMigrationResult {
  ok: boolean;
  message: string;
  data?: WorkflowNodeData;
}

const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const JSON_REFERENCE_FIELDS: Readonly<Record<string, ReadonlySet<string>>> = {
  condition: new Set(["inputVariable"]),
  data_aggregate: new Set(["inputVariable"]),
  data_merge: new Set(["leftVariable", "rightVariable"]),
  dataset_compare: new Set(["leftVariable", "rightVariable"]),
  file_output: new Set(["inputVariable"]),
  json_serialize: new Set(["inputVariable"]),
  list_operation: new Set(["inputVariable"]),
  multi_route: new Set(["inputVariable"]),
  object_transform: new Set(["inputVariable"]),
  output: new Set(["outputVariable"]),
  variable_assign: new Set(["sourceVariable"]),
};

export function isVariablePackV2(data: WorkflowNodeData): boolean {
  return data.kind === "variable_aggregator" && data.contractVersion === 2;
}

function parseVariableNames(value: unknown): string[] {
  return String(value ?? "")
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter((item, index, items) => Boolean(item) && items.indexOf(item) === index);
}

function descendantIds(
  nodeId: string,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
): Set<string> {
  const known = new Set(nodes.map((node) => node.id));
  const descendants = new Set<string>();
  const queue = edges.filter((edge) => edge.source === nodeId).map((edge) => edge.target);
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (!known.has(current) || descendants.has(current)) continue;
    descendants.add(current);
    edges
      .filter((edge) => edge.source === current)
      .forEach((edge) => queue.push(edge.target));
  }
  return descendants;
}

function containsVariableReference(value: unknown, variable: string): boolean {
  if (typeof value === "string") {
    return value === variable
      || new RegExp(`\\{\\{\\s*${variable}\\s*\\}\\}`).test(value);
  }
  if (Array.isArray(value)) {
    return value.some((item) => containsVariableReference(item, variable));
  }
  if (value && typeof value === "object") {
    return Object.values(value).some((item) => containsVariableReference(item, variable));
  }
  return false;
}

function unsafeDownstreamReferences(
  node: WorkflowNode,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  outputVariable: string,
): string[] {
  const descendants = descendantIds(node.id, nodes, edges);
  const problems: string[] = [];
  nodes.forEach((candidate) => {
    if (!descendants.has(candidate.id)) return;
    const allowedFields = JSON_REFERENCE_FIELDS[candidate.data.kind] ?? new Set<string>();
    Object.entries(candidate.data).forEach(([field, value]) => {
      if (!containsVariableReference(value, outputVariable)) return;
      const exactTypedReference = allowedFields.has(field) && value === outputVariable;
      const variableAssignV2 = candidate.data.kind !== "variable_assign"
        || candidate.data.contractVersion === 2;
      if (!exactTypedReference || !variableAssignV2) {
        problems.push(`${candidate.data.title || candidate.id}.${field}`);
      }
    });
  });
  return [...new Set(problems)].sort();
}

function expandedLegacyTemplate(
  outputTemplate: string,
  variableNames: string[],
): string | null {
  const withoutSupportedTokens = outputTemplate
    .replaceAll("{name}", "")
    .replaceAll("{value}", "");
  if (withoutSupportedTokens.includes("{") || withoutSupportedTokens.includes("}")) {
    return null;
  }
  return variableNames
    .map((name) => outputTemplate
      .replaceAll("{name}", name)
      .replaceAll("{value}", `{{${name}}}`))
    .join("");
}

export function migrateLegacyVariableAggregator(
  node: WorkflowNode,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  availableVariables: ReadonlySet<string>,
): WorkflowVariablePackMigrationResult {
  const data = node.data;
  if (data.kind !== "variable_aggregator") {
    return { ok: false, message: "当前节点不是旧版变量聚合节点。" };
  }
  if (isVariablePackV2(data)) {
    return { ok: false, message: "当前变量打包节点已经是 V2。" };
  }
  if (data.contractVersion !== undefined && data.contractVersion !== 1) {
    return { ok: false, message: "节点合同版本不是规范的 V1 或 V2，不能迁移。" };
  }
  const variableNames = parseVariableNames(data.variableNames);
  if (variableNames.length < 1 || variableNames.length > 50) {
    return { ok: false, message: "旧节点必须包含 1 至 50 个唯一变量名。" };
  }
  const invalidNames = variableNames.filter((name) => !VARIABLE_NAME.test(name));
  if (invalidNames.length > 0) {
    return { ok: false, message: `旧节点包含非法变量名：${invalidNames.join("、")}。` };
  }
  const unavailable = variableNames.filter((name) => !availableVariables.has(name));
  if (unavailable.length > 0) {
    return { ok: false, message: `以下来源在当前节点之前不可用：${unavailable.join("、")}。` };
  }
  const outputVariable = String(data.outputVariable ?? "").trim();
  if (!VARIABLE_NAME.test(outputVariable)) {
    return { ok: false, message: "旧节点输出变量名不合法。" };
  }
  if (variableNames.includes(outputVariable)) {
    return { ok: false, message: "输出变量不能覆盖任一来源变量。" };
  }

  const outputTemplate = String(data.outputTemplate ?? "");
  if (outputTemplate) {
    const template = expandedLegacyTemplate(outputTemplate, variableNames);
    if (template === null) {
      return {
        ok: false,
        message: "旧输出模板含有 {name}/{value} 以外的花括号，无法无损迁移。",
      };
    }
    if (template.length > 100_000) {
      return { ok: false, message: "展开后的模板超过 100000 字符，无法迁移。" };
    }
    return {
      ok: true,
      message: "已迁移为变量赋值 V2；节点 ID、位置和连线保持不变。",
      data: {
        kind: "variable_assign",
        title: "变量赋值",
        description: "把旧聚合模板无损展开为 V2 文本模板。",
        contractVersion: 2,
        valueSource: "template",
        template,
        outputVariable,
      },
    };
  }

  const unsafeReferences = unsafeDownstreamReferences(
    node,
    nodes,
    edges,
    outputVariable,
  );
  if (unsafeReferences.length > 0) {
    return {
      ok: false,
      message: (
        "输出将从 JSON 字符串变为对象，但以下下游没有明确接受 JSON："
        + `${unsafeReferences.join("、")}。`
      ),
    };
  }
  const bindings: WorkflowVariablePackBinding[] = variableNames.map((name, index) => ({
    id: `binding_${index + 1}`,
    sourceVariable: name,
    outputField: name,
  }));
  return {
    ok: true,
    message: "已迁移为变量打包 V2；输出将从 JSON 字符串变为类型化对象，请复核下游。",
    data: {
      kind: "variable_aggregator",
      title: "变量打包",
      description: "把多个类型化变量深复制到一个 JSON 对象。",
      contractVersion: 2,
      bindings,
      outputVariable,
    },
  };
}
