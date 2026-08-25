import type { WorkflowNodeData } from "../../types/workflow";
import type { WorkflowVariableValueType } from "./workflowVariables";

const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const TEMPLATE_VARIABLE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

export interface IterationMigrationResult {
  ok: boolean;
  message: string;
  data?: WorkflowNodeData;
}

export function isIterationV2(data: WorkflowNodeData) {
  return data.kind === "iteration" && Number(data.contractVersion) === 2;
}

export function migrateLegacyIteration(
  data: WorkflowNodeData,
  availableVariables: Set<string>,
  sourceType: WorkflowVariableValueType | undefined,
): IterationMigrationResult {
  if (isIterationV2(data)) {
    return { ok: false, message: "该节点已经使用批量处理 V2。" };
  }
  const inputVariable = String(data.inputVariable ?? "").trim();
  const itemVariable = String(data.iterationVariable ?? "").trim();
  const itemTemplate = String(data.itemTemplate ?? "");
  const outputVariable = String(data.outputVariable ?? "").trim();
  if (![inputVariable, itemVariable, outputVariable].every((value) => VARIABLE_NAME.test(value))) {
    return { ok: false, message: "旧版输入、单项和输出变量都必须是合法标识符。" };
  }
  if (!itemTemplate || itemTemplate.length > 20_000) {
    return { ok: false, message: "旧版单项模板必须包含 1 到 20000 个字符。" };
  }
  if (itemVariable === outputVariable) {
    return {
      ok: false,
      message: "旧版单项变量与输出变量同名，无法无损升级；请先修改其中一个名称。",
    };
  }
  if (!availableVariables.has(inputVariable)) {
    return { ok: false, message: `输入变量 ${inputVariable} 在该节点之前不可用。` };
  }
  if (sourceType && !["json", "unknown"].includes(sourceType)) {
    return {
      ok: false,
      message: sourceType === "text"
        ? "旧版文本或逗号列表不能无损升级；请先把输入改为真正的 JSON 数组。"
        : "旧版输入已知不是数组，不能无损升级。",
    };
  }
  const scoped = new Set(availableVariables);
  scoped.add(itemVariable);
  const missing = Array.from(itemTemplate.matchAll(TEMPLATE_VARIABLE))
    .map((match) => match[1])
    .filter((name) => !scoped.has(name));
  if (missing.length) {
    return {
      ok: false,
      message: `模板引用了当前不可用的变量：${Array.from(new Set(missing)).join("、")}。`,
    };
  }
  const reservedNames = new Set([...availableVariables, itemVariable, outputVariable]);
  const indexVariable = [
    "item_index",
    "batch_index",
    "current_index",
    ...Array.from({ length: 61 }, (_, index) => `item_index_${index + 2}`),
  ].find((candidate) => !reservedNames.has(candidate));
  if (!indexVariable) {
    return {
      ok: false,
      message: "找不到不会遮蔽现有变量的批次序号名称，请先重命名旧变量。",
    };
  }
  const migrated: WorkflowNodeData = {
    ...data,
    title: "批量处理",
    description: "逐项渲染安全模板，输出真正的字符串数组。",
    contractVersion: 2,
    mode: "template_map",
    inputVariable,
    itemVariable,
    indexVariable,
    itemTemplate,
    outputVariable,
  };
  delete migrated.iterationVariable;
  return {
    ok: true,
    message: sourceType === "unknown"
      ? "已准备升级。运行时只接受真正的 JSON 数组，输出将从旧字符串改为字符串数组。"
      : "已准备升级。输出将从旧字符串改为真正的字符串数组。",
    data: migrated,
  };
}
