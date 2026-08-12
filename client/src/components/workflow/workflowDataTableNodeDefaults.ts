import {
  type WorkflowNodeData,
  type WorkflowNodeKind,
} from "../../types/workflow";

type DataTableNodeKind = Extract<
  WorkflowNodeKind,
  | "data_table_query"
  | "data_table_insert"
  | "data_table_update"
  | "data_table_delete"
>;

type TypedCanvasNodeKind = Extract<
  WorkflowNodeKind,
  "json_serialize" | "json_deserialize" | "annotation"
>;

export function createTypedCanvasNodeData(
  kind: WorkflowNodeKind,
): WorkflowNodeData | null {
  const definitions: Record<TypedCanvasNodeKind, WorkflowNodeData> = {
    json_serialize: {
      kind: "json_serialize",
      title: "JSON 序列化",
      description: "将类型化工作流变量转换为 JSON 字符串。",
      inputVariable: "json_value",
      outputVariable: "json_text",
      format: "compact",
    },
    json_deserialize: {
      kind: "json_deserialize",
      title: "JSON 反序列化",
      description: "将 JSON 字符串解析为真实的类型化工作流变量。",
      inputVariable: "json_text",
      outputVariable: "json_value",
    },
    annotation: {
      kind: "annotation",
      title: "注释",
      description: "仅保存画布说明，不参与控制流或运行。",
      content: "",
    },
  };

  return kind in definitions
    ? definitions[kind as TypedCanvasNodeKind]
    : null;
}

export function createDataTableNodeData(
  kind: WorkflowNodeKind,
): WorkflowNodeData | null {
  const shared = {
    tableId: "",
    versionPolicy: "latest",
    pinnedSchemaVersion: "",
  };

  const definitions: Record<DataTableNodeKind, WorkflowNodeData> = {
    data_table_query: {
      ...shared,
      kind: "data_table_query",
      title: "查询数据表",
      description: "按字段、条件和排序读取固定 Schema 的 Agent Table 记录。",
      selectFields: [],
      filter: {},
      sort: [],
      limit: 20,
      returnMode: "list",
      outputVariable: "table_records",
    },
    data_table_insert: {
      ...shared,
      kind: "data_table_insert",
      title: "新增数据",
      description: "按类型化字段绑定向 Agent Table 插入一条记录。",
      valueBindings: {},
      outputVariable: "inserted_record",
    },
    data_table_update: {
      ...shared,
      kind: "data_table_update",
      title: "更新数据",
      description: "使用非空条件批量更新 Agent Table，单次最多影响 100 行。",
      filter: {},
      valueBindings: {},
      outputVariable: "update_result",
    },
    data_table_delete: {
      ...shared,
      kind: "data_table_delete",
      title: "删除数据",
      description: "使用非空条件删除 Agent Table 记录，单次最多影响 100 行。",
      filter: {},
      outputVariable: "delete_result",
    },
  };

  return kind in definitions
    ? definitions[kind as DataTableNodeKind]
    : null;
}

export function normalizeRecentlyEnabledNodeData(
  data: WorkflowNodeData,
): WorkflowNodeData {
  const defaults =
    createDataTableNodeData(data.kind) ?? createTypedCanvasNodeData(data.kind);
  const isOutputFallback =
    defaults !== null &&
    data.outputVariable === "llm_output" &&
    data.variableName === undefined &&
    data.inputVariable === undefined &&
    data.tableId === undefined;

  if (!defaults || !isOutputFallback) {
    return data;
  }

  const { outputVariable: _legacyOutputVariable, ...legacyData } = data;
  return {
    ...defaults,
    ...legacyData,
    title: defaults.title,
    description: defaults.description,
  };
}
