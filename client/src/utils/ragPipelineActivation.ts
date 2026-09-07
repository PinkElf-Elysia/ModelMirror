export interface PipelineContentIndexContractEvidence {
  contract_version?: string | null;
  chunker_contract_version?: string | null;
  lexical_contract_version?: string | null;
  parser_contract_version?: string | null;
  status?: string | null;
  components?: Record<string, string | null | undefined> | null;
}

export interface PipelineVersionActivationEvidence {
  activated_at?: number | null;
  content_index_contract?: PipelineContentIndexContractEvidence | null;
  index_schema_version?: number | null;
}

export interface PipelineDraftExecutionDisposition {
  status: "blocked" | "diagnostic_only" | "normal";
  canExecute: boolean;
  message: string;
}

export function draftExecutionDisposition(
  contract: PipelineContentIndexContractEvidence | null | undefined,
  retrievalMode: unknown,
  indexSchemaVersion: unknown,
): PipelineDraftExecutionDisposition {
  if (indexSchemaVersion !== 3) {
    return {
      status: "blocked",
      canExecute: false,
      message: "历史索引 schema 只读，不能新建候选。",
    };
  }
  const components = contract?.components ?? {};
  if (components.chunker !== "current") {
    return {
      status: "blocked",
      canExecute: false,
      message: "历史字符分块合同只读；请先保存估算 Token 分块预算。",
    };
  }
  const mode = typeof retrievalMode === "string" ? retrievalMode : "";
  if (!new Set(["vector", "fulltext", "hybrid"]).has(mode)) {
    return {
      status: "blocked",
      canExecute: false,
      message: "检索模式缺失或不可识别；执行保持禁用。",
    };
  }
  if (
    (mode === "fulltext" || mode === "hybrid")
    && components.lexical !== "current"
  ) {
    return {
      status: "blocked",
      canExecute: false,
      message: "4A 仅 vector diagnostic 可执行；全文合同待4B。",
    };
  }
  if (
    contract?.status === "current"
    && components.chunker === "current"
    && components.lexical === "current"
    && components.parser === "current"
  ) {
    return {
      status: "normal",
      canExecute: true,
      message: "内容索引合同完整，可构建候选。",
    };
  }
  return {
    status: "diagnostic_only",
    canExecute: true,
    message: "当前仅允许 vector diagnostic 候选；不能首次激活或晋级。",
  };
}

export function isPipelineVersionFirstActivationBlocked(
  version: PipelineVersionActivationEvidence,
): boolean {
  return !hasPriorPipelineActivation(version.activated_at) && (
    version.content_index_contract?.status !== "current"
    || (version.index_schema_version ?? 1) < 3
  );
}

export function hasPriorPipelineActivation(value: unknown): boolean {
  return typeof value === "number"
    && Number.isFinite(value)
    && value > 0;
}
