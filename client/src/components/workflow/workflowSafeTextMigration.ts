import type {
  SafeTextOperation,
  WorkflowNodeData,
} from "../../types/workflow";

export interface WorkflowSafeTextMigrationResult {
  ok: boolean;
  message: string;
  data?: WorkflowNodeData;
}

const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const MAX_SAFE_TEXT_LITERAL_CHARS = 100_000;
const SAFE_OPERATIONS = new Set<SafeTextOperation>([
  "upper",
  "lower",
  "replace",
  "concat",
]);

export function isSafeTextV2(data: WorkflowNodeData): boolean {
  return data.kind === "code" && data.contractVersion === 2;
}

export function migrateLegacyCodeNode(
  data: WorkflowNodeData,
  availableVariables: ReadonlySet<string>,
): WorkflowSafeTextMigrationResult {
  if (data.kind !== "code") {
    return { ok: false, message: "当前节点不是旧版文本加工节点。" };
  }
  if (isSafeTextV2(data)) {
    return { ok: false, message: "当前安全文本加工节点已经是 V2。" };
  }
  if (data.contractVersion !== undefined && data.contractVersion !== 1) {
    return {
      ok: false,
      message: "节点合同版本不是规范的 V1 或 V2，不能自动解释或迁移。",
    };
  }

  const operation = String(data.codeOperation ?? "upper");
  if (operation === "python") {
    return {
      ok: false,
      message: "旧版 Python 配置无法无损迁移。请改用受控节点重新表达处理逻辑。",
    };
  }
  if (!SAFE_OPERATIONS.has(operation as SafeTextOperation)) {
    return { ok: false, message: "旧版操作类型无法识别，不能自动迁移。" };
  }

  const inputVariable = String(data.codeInputVariable ?? "llm_output").trim();
  const outputVariable = String(data.codeOutputVariable ?? "code_output").trim();
  if (!VARIABLE_NAME.test(inputVariable)) {
    return { ok: false, message: "旧版输入变量名不合法，请先修正后再迁移。" };
  }
  if (!VARIABLE_NAME.test(outputVariable)) {
    return { ok: false, message: "旧版输出变量名不合法，请先修正后再迁移。" };
  }
  if (!availableVariables.has(inputVariable)) {
    return {
      ok: false,
      message: `输入变量 ${inputVariable} 不能保证在当前节点之前产生，请先调整变量或连线。`,
    };
  }
  const replaceFrom = String(data.replaceFrom ?? "");
  const replaceTo = String(data.replaceTo ?? "");
  const concatValue = String(data.concatValue ?? "");
  if (
    (operation === "replace"
      && (replaceFrom.length > MAX_SAFE_TEXT_LITERAL_CHARS
        || replaceTo.length > MAX_SAFE_TEXT_LITERAL_CHARS))
    || (operation === "concat"
      && concatValue.length > MAX_SAFE_TEXT_LITERAL_CHARS)
  ) {
    return {
      ok: false,
      message: "旧版固定文本超过 100000 字符，无法迁移到安全合同。",
    };
  }

  return {
    ok: true,
    message: "已升级为安全文本加工 V2。请复核操作参数后再发布。",
    data: {
      kind: "code",
      title: "安全文本加工",
      description: "把变量稳定转换为文本后，执行受控的大小写、替换或拼接操作。",
      contractVersion: 2,
      inputVariable,
      outputVariable,
      operation: operation as SafeTextOperation,
      replaceFrom,
      replaceTo,
      concatValue,
    },
  };
}

export function migrateLegacyTemplateTransform(
  data: WorkflowNodeData,
  availableVariables: ReadonlySet<string>,
): WorkflowSafeTextMigrationResult {
  if (data.kind !== "template_transform") {
    return { ok: false, message: "当前节点不是已退役的模板转换节点。" };
  }
  const template = String(data.template ?? "");
  const outputVariable = String(data.outputVariable ?? "").trim();
  if (!template.trim()) {
    return { ok: false, message: "模板内容不能为空，请先补齐后再迁移。" };
  }
  if (template.length > 100_000) {
    return { ok: false, message: "模板内容超过 100000 字符，无法迁移。" };
  }
  if (!VARIABLE_NAME.test(outputVariable)) {
    return { ok: false, message: "输出变量名不合法，请先修正后再迁移。" };
  }
  if (/\{\{\{|\}\}\}/.test(template)) {
    return {
      ok: false,
      message: "模板含有无法无损识别的花括号表达式，请先改为 {{变量名}}。",
    };
  }
  const references = new Set<string>();
  const withoutReferences = template.replace(
    /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g,
    (_match, name: string) => {
      references.add(name);
      return "";
    },
  );
  if (withoutReferences.includes("{{") || withoutReferences.includes("}}")) {
    return {
      ok: false,
      message: "模板含有无法无损识别的花括号表达式，请先改为 {{变量名}}。",
    };
  }
  const unavailableReferences = [...references].filter(
    (name) => !availableVariables.has(name),
  );
  if (unavailableReferences.length > 0) {
    return {
      ok: false,
      message: `模板引用的变量在当前节点不可用：${unavailableReferences.join("、")}。`,
    };
  }

  return {
    ok: true,
    message: "已迁移为变量赋值 V2 模板模式。请复核下游变量类型后再发布。",
    data: {
      kind: "variable_assign",
      title: "变量赋值",
      description: "把类型化字面量、变量副本或模板文本写入变量。",
      contractVersion: 2,
      outputVariable,
      valueSource: "template",
      template,
    },
  };
}
