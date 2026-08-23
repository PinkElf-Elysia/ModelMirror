import type {
  WorkflowClassifierCategory,
  WorkflowEdge,
  WorkflowExtractorField,
  WorkflowNodeData,
} from "../../types/workflow";

export interface TypedAiMigrationResult {
  ok: boolean;
  message: string;
  patch?: Partial<WorkflowNodeData>;
  outgoingEdges?: WorkflowEdge[];
}

const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

export function migrateLegacyParameterExtractor(
  data: WorkflowNodeData,
): TypedAiMigrationResult {
  if (Number(data.contractVersion) === 2) {
    return { ok: false, message: "当前参数提取器已经是 V2。" };
  }
  const lines = String(data.schema ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 1 || lines.length > 50) {
    return { ok: false, message: "旧字段描述必须包含 1–50 行。" };
  }
  const seenNames = new Set<string>();
  const fields: WorkflowExtractorField[] = [];
  for (const [index, line] of lines.entries()) {
    const separator = line.indexOf(":");
    const name = separator >= 0 ? line.slice(0, separator).trim() : "";
    const description = separator >= 0 ? line.slice(separator + 1).trim() : "";
    if (!VARIABLE_NAME.test(name) || !description) {
      return {
        ok: false,
        message: `第 ${index + 1} 行无法无损解析，请使用“字段名: 说明”。`,
      };
    }
    if (seenNames.has(name)) {
      return { ok: false, message: `字段名 ${name} 重复，无法自动升级。` };
    }
    seenNames.add(name);
    fields.push({
      id: `field_${index + 1}`,
      name,
      description,
      valueType: "string",
      required: true,
      nullable: false,
    });
  }
  return {
    ok: true,
    message: "已升级为 V2 字段表；请复核字段类型和必填设置。",
    patch: {
      contractVersion: 2,
      schemaMode: "fields",
      outputShape: "object",
      fields,
      jsonSchema: undefined,
      repairAttempts: 0,
      schema: undefined,
    },
  };
}

export function migrateLegacyQuestionClassifier(
  data: WorkflowNodeData,
  outgoingEdges: WorkflowEdge[],
  existingEdgeIds: string[] = outgoingEdges.map((edge) => edge.id),
): TypedAiMigrationResult {
  if (Number(data.contractVersion) === 2) {
    return { ok: false, message: "当前问题分类器已经是 V2。" };
  }
  if (
    outgoingEdges.length > 1 ||
    outgoingEdges.some((edge) => Boolean(edge.sourceHandle))
  ) {
    return {
      ok: false,
      message: "旧节点存在多条出边或自定义出口，请先整理为至多一条普通出边。",
    };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(String(data.categories ?? ""));
  } catch {
    return { ok: false, message: "旧分类规则不是合法 JSON，无法自动升级。" };
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    return { ok: false, message: "旧分类规则必须是类别到关键词数组的对象。" };
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length < 2 || entries.length > 8) {
    return { ok: false, message: "V2 分类器需要 2–8 个类别。" };
  }
  const categories: WorkflowClassifierCategory[] = [];
  const seenLabels = new Set<string>();
  for (const [index, [label, rawKeywords]] of entries.entries()) {
    const cleanLabel = label.trim();
    if (
      !cleanLabel
      || cleanLabel.length > 100
      || seenLabels.has(cleanLabel)
      || !Array.isArray(rawKeywords)
    ) {
      return { ok: false, message: `类别 ${index + 1} 的名称或关键词格式不合法。` };
    }
    const keywords = rawKeywords.map((item) =>
      typeof item === "string" ? item.trim() : "",
    );
    if (
      keywords.length < 1 ||
      keywords.length > 20 ||
      keywords.some((item) => !item) ||
      new Set(keywords).size !== keywords.length
    ) {
      return { ok: false, message: `类别“${label}”的关键词必须是 1–20 个唯一非空文本。` };
    }
    categories.push({
      id: `category_${index + 1}`,
      label: cleanLabel,
      description: "",
      keywords,
      matchMode:
        data.matchMode === "contains_all" ? "contains_all" : "contains_any",
    });
    seenLabels.add(cleanLabel);
  }

  const templateEdge = outgoingEdges[0];
  const reservedEdgeIds = new Set(
    existingEdgeIds.filter((id) => id !== templateEdge?.id),
  );
  const nextEdgeId = (handle: string) => {
    const base = `${templateEdge!.id}-${handle}`;
    let candidate = base;
    for (let suffix = 2; reservedEdgeIds.has(candidate); suffix += 1) {
      candidate = `${base}-${suffix}`;
    }
    reservedEdgeIds.add(candidate);
    return candidate;
  };
  const migratedEdges = templateEdge
    ? [...categories.map((category) => ({
        ...templateEdge,
        id: nextEdgeId(category.id),
        sourceHandle: category.id,
      })), {
        ...templateEdge,
        id: nextEdgeId("default"),
        sourceHandle: "default",
      }]
    : [];
  return {
    ok: true,
    message: templateEdge
      ? "已升级为 V2，并为所有分类出口复制原连线。"
      : "已升级为 V2；发布前请为每个分类和默认出口各连接一次。",
    patch: {
      contractVersion: 2,
      classificationMode:
        data.useLlmFallback === true || data.useLlmFallback === "true"
          ? "rules_then_model"
          : "rules_only",
      categoriesV2: categories,
      caseSensitive: data.caseSensitive === true || data.caseSensitive === "true",
      modelId: data.modelId ?? "",
      defaultLabel: String(data.defaultCategory ?? "未分类") || "未分类",
      categories: undefined,
      defaultCategory: undefined,
      matchMode: undefined,
      useLlmFallback: undefined,
      llmFallbackPrompt: undefined,
    },
    outgoingEdges: migratedEdges,
  };
}

export function nextStableId(
  prefix: "field" | "category" | "rule",
  existingIds: string[],
  maximum: number,
): string | null {
  const used = new Set(existingIds);
  for (let index = 1; index <= maximum; index += 1) {
    const candidate = `${prefix}_${index}`;
    if (!used.has(candidate)) return candidate;
  }
  return null;
}
