import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type NodeChange,
  type EdgeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DEFAULT_CHAT_MODEL_ID } from "../../context/ModelPreferenceContext";
import { DEFAULT_WORKFLOW_AGENT_MODEL_ID } from "../../data/modelOptions";
import { models } from "../../data/models";
import {
  type CodeOperation,
  type ConditionOperator,
  type HttpRequestMethod,
  type ListOperationOperator,
  type WorkflowDefinition,
  type WorkflowEdge,
  type WorkflowNode,
  type WorkflowNodeData,
  type WorkflowNodeKind,
} from "../../types/workflow";
import {
  fetchRuntimeMiddlewareNodes,
  type RuntimeMiddlewareField,
  type RuntimeMiddlewareNode,
} from "../../types/runtimeMiddleware";
import { type XpertListResponse, type XpertSummary } from "../../types/xpert";
import {
  createXpert,
  toXpertDraftWorkflow,
  updateXpert,
} from "../../utils/xpertApi";
import {
  isLegacyStarterWorkflow,
  readStoredWorkflow,
  saveStoredWorkflow,
} from "../../utils/workflowStorage";
import { reconcileRuntimeMiddlewareNodes } from "../../utils/runtimeMiddlewareMigration";
import {
  getSkillCatalogApprovalState,
  reconcileSkillCatalogApprovals,
} from "../../utils/skillCatalogApproval";
import NodePalette from "./NodePalette";
import WorkflowTypedDataNodeConfig from "./WorkflowTypedDataNodeConfig";
import WorkflowNodeCard from "./WorkflowNodeCard";
import WorkflowRun from "./WorkflowRun";
import TrustedSkillSelect, {
  type TrustSelectableSkill,
} from "../skill-trust/TrustedSkillSelect";
import {
  createDataTableNodeData,
  createTypedCanvasNodeData,
  normalizeRecentlyEnabledNodeData,
} from "./workflowDataTableNodeDefaults";

const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

interface RuntimeMiddlewareDragPayload {
  kind: "runtime_middleware";
  runtimeMiddlewareId?: string;
  runtimeMiddlewareKind?: string;
  title?: string;
  description?: string;
  fields?: RuntimeMiddlewareField[];
  metadata?: Record<string, unknown>;
}

const runtimeMiddlewareFieldTypes = new Set([
  "text",
  "textarea",
  "select",
  "boolean",
  "number",
  "json",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isRuntimeMiddlewareField(value: unknown): value is RuntimeMiddlewareField {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.label === "string" &&
    typeof value.type === "string" &&
    runtimeMiddlewareFieldTypes.has(value.type)
  );
}

function parseRuntimeMiddlewarePayload(
  raw: string,
): RuntimeMiddlewareDragPayload | null {
  if (!raw.trim().startsWith("{")) {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.kind !== "runtime_middleware") {
      return null;
    }

    return {
      kind: "runtime_middleware",
      runtimeMiddlewareId:
        typeof parsed.runtimeMiddlewareId === "string"
          ? parsed.runtimeMiddlewareId
          : undefined,
      runtimeMiddlewareKind:
        typeof parsed.runtimeMiddlewareKind === "string"
          ? parsed.runtimeMiddlewareKind
          : undefined,
      title: typeof parsed.title === "string" ? parsed.title : undefined,
      description:
        typeof parsed.description === "string" ? parsed.description : undefined,
      fields: Array.isArray(parsed.fields)
        ? parsed.fields.filter(isRuntimeMiddlewareField)
        : [],
      metadata: isRecord(parsed.metadata) ? parsed.metadata : {},
    };
  } catch {
    return null;
  }
}

function createRuntimeMiddlewareConfig(
  fields: RuntimeMiddlewareField[],
): Record<string, unknown> {
  return fields.reduce<Record<string, unknown>>((config, field) => {
    if (field.default !== undefined) {
      config[field.name] = field.default;
    }
    return config;
  }, {});
}

function createNodeData(
  kind: WorkflowNodeKind,
  payload?: RuntimeMiddlewareDragPayload,
): WorkflowNodeData {
  const typedCanvasNode = createTypedCanvasNodeData(kind);
  if (typedCanvasNode) {
    return typedCanvasNode;
  }

  const dataTableNode = createDataTableNodeData(kind);
  if (dataTableNode) {
    return dataTableNode;
  }

  if (kind === "input") {
    return {
      kind,
      title: "接待处输入",
      description: "收集用户给流水线的原始任务。",
      variableName: "user_input",
    };
  }

  if (kind === "llm") {
    return {
      kind,
      title: "模型工位",
      description: "调用模型，把上游变量加工成新结果。",
      modelId: DEFAULT_CHAT_MODEL_ID,
      prompt: "请基于以下输入给出清晰回答：\n\n{{user_input}}",
      outputVariable: "llm_output",
    };
  }

  if (kind === "condition") {
    return {
      kind,
      title: "分流判断",
      description: "根据变量内容决定走“是”或“否”。",
      conditionVariable: "user_input",
      conditionOperator: "contains",
      conditionValue: "代码",
    };
  }

  if (kind === "code") {
    return {
      kind,
      title: "安全加工",
      description: "只执行预置字符串操作，不运行任意代码。",
      codeOperation: "upper",
      codeInputVariable: "llm_output",
      codeOutputVariable: "code_output",
      replaceFrom: "",
      replaceTo: "",
      concatValue: "",
      pythonCode: "print(input)",
    };
  }

  if (kind === "variable_assign") {
    return {
      kind,
      title: "变量赋值",
      description: "把模板内容写入一个新变量。",
      variableName: "assigned_text",
      template: "收到：{{user_input}}",
    };
  }

  if (kind === "template_transform") {
    return {
      kind,
      title: "模板转换",
      description: "把变量填入长文本模板，产出报告或结构化文本。",
      template: "## 处理结果\n\n用户输入：{{user_input}}\n",
      outputVariable: "template_output",
    };
  }

  if (kind === "variable_aggregator") {
    return {
      kind,
      title: "变量聚合器",
      description: "汇总多个变量，便于下游统一处理。",
      variableNames: "user_input",
      outputTemplate: "{name}={value}\n",
      outputVariable: "aggregated_output",
    };
  }

  if (kind === "parameter_extractor") {
    return {
      kind,
      title: "参数提取器",
      description: "调用模型从文本中抽取字段，返回 JSON 字符串。",
      inputVariable: "user_input",
      schema: "name: 姓名\nemail_address: 邮箱地址",
      modelId: DEFAULT_CHAT_MODEL_ID,
      outputVariable: "parameters_json",
    };
  }

  if (kind === "knowledge_retrieval") {
    return {
      kind,
      title: "知识检索",
      description: "检索指定知识库的活动版本。",
      contractVersion: 2,
      knowledgeBaseId: "",
      queryVariable: "user_input",
      top_k: "5",
      returnMode: "result",
      outputVariable: "knowledge_result",
    };
  }

  if (kind === "knowledge_citation") {
    return {
      kind,
      title: "知识引用锚点",
      description: "把本地 RAG 检索结果转换为 CitationAnchor JSON。",
      queryVariable: "user_input",
      knowledgeBaseId: "",
      top_k: "4",
      outputVariable: "citation_anchors_json",
    };
  }

  if (kind === "document_extractor") {
    return {
      kind,
      title: "文档提取器",
      description: "从当前工作流作用域的文件资产中提取纯文本。",
      assetIdVariable: "document_asset_id",
      outputVariable: "document_text",
    };
  }

  if (kind === "human_intervention") {
    return {
      kind,
      title: "人工确认",
      description: "暂停流水线，等待用户补充文本后继续。",
      prompt: "请确认或补充这段内容：\n\n{{user_input}}",
      outputVariable: "human_input",
    };
  }

  if (kind === "question_classifier") {
    return {
      kind,
      title: "问题分类",
      description: "关键词规则分类",
      inputVariable: "user_input",
      categories:
        '{"投诉":["差","投诉","退款"],"咨询":["咨询","如何","怎么"],"订单":["订单","下单","购买"],"售后":["售后","退货","换"]}',
      outputVariable: "category",
      defaultCategory: "未知",
      matchMode: "contains_any",
      caseSensitive: "false",
      useLlmFallback: "false",
      modelId: "",
      llmFallbackPrompt: "",
    };
  }

  if (kind === "agent") {
    return {
      kind,
      title: "Agent",
      description: "模型驱动的任务执行节点。",
      agentMode: "tool_first",
      agentStrategy: "auto",
      instruction: "{{user_input}}",
      modelId: "",
      toolNames: "",
      outputVariable: "agent_output",
      maxIterations: "5",
      temperature: "0.7",
      promptSuffix: "",
      disableOutput: "false",
      enableFileUnderstanding: "false",
      parallelToolCalls: "false",
      maxToolConcurrency: "2",
      maxToolCalls: "12",
      maxToolDepth: "4",
      retryOnFailure: "false",
      fallbackModelId: "",
      exceptionHandling: "none",
      outputSchemaMode: "default",
      outputSchemaJson: "",
      memoryReadEnabled: "false",
      memoryReadScope: "both",
      memoryWriteEnabled: "false",
      memoryWriteTarget: "xpert",
      nodeParametersJson: "[]",
    };
  }

  if (kind === "workflow_agent") {
    return {
      kind,
      title: "工作流智能体",
      description: "模型驱动的单步智能体执行节点。",
      agentName: "workflow-agent",
      modelId: DEFAULT_WORKFLOW_AGENT_MODEL_ID,
      rolePrompt: "你是负责执行当前工作流步骤的智能体，请直接输出结果。",
      taskInput: "{{user_input}}",
      toolMode: "none",
      agentStrategy: "auto",
      toolNames: "",
      maxIterations: "5",
      promptSuffix: "",
      outputVariable: "agent_output",
      disableOutput: "false",
      enableFileUnderstanding: "false",
      parallelToolCalls: "false",
      maxToolConcurrency: "2",
      maxToolCalls: "12",
      maxToolDepth: "4",
      retryOnFailure: "false",
      fallbackModelId: "",
      exceptionHandling: "none",
      outputSchemaMode: "default",
      outputSchemaJson: "",
      memoryReadEnabled: "false",
      memoryReadScope: "both",
      memoryWriteEnabled: "false",
      memoryWriteTarget: "xpert",
      knowledgeReadEnabled: "false",
      knowledgeWriteEnabled: "false",
      knowledgeBaseIds: "",
      nodeParametersJson: "[]",
    };
  }

  if (kind === "external_xpert") {
    return {
      kind,
      title: "外部智能体",
      description: "将已发布智能体作为当前智能体的同步协作者工具。",
      xpertId: "",
      toolName: "external_expert",
      versionPolicy: "current_published",
      pinnedVersion: "",
    };
  }

  if (kind === "knowledge_base") {
    return {
      kind,
      title: "知识库资源",
      description: "将一个知识库绑定到当前智能体的只读知识工具。",
      knowledgeBaseId: "",
      topK: "5",
      scoreThreshold: "0",
    };
  }

  if (kind === "toolset_resource") {
    return {
      kind,
      title: "MCP Toolset",
      description: "将已发布的 MCP Toolset 版本绑定到当前智能体。",
      toolsetId: "",
      versionPolicy: "current_published",
      pinnedVersion: "",
    };
  }

  if (kind === "plugin_resource") {
    return {
      kind,
      title: "Plugin 资源",
      description: "将已发布 Plugin 的固定资源包绑定到当前工作流智能体。",
      pluginId: "",
      versionPolicy: "latest",
      pinnedVersion: "",
    };
  }

  if (kind === "agent_task") {
    return {
      kind,
      title: "智能体任务",
      description: "创建 Agent Task Runtime 任务，并把 task_id 写入变量。",
      taskTitle: "工作流任务：{{user_input}}",
      taskInput: "{{user_input}}",
      assignedAgent: "workflow-planner",
      outputVariable: "agent_task_id",
    };
  }

  if (kind === "agent_handoff") {
    return {
      kind,
      title: "智能体移交",
      description: "将已有 Agent Task 移交给另一个智能体，输出 handoff_id。",
      taskIdVariable: "agent_task_id",
      sourceAgent: "workflow-planner",
      targetAgent: "review-agent",
      executionMode: "manual",
      waitForCompletion: "false",
      resultVariable: "handoff_result",
      waitTimeoutSeconds: "120",
      reason: "请接手处理：{{user_input}}",
      outputVariable: "agent_handoff_id",
    };
  }

  if (kind === "handoff_router") {
    return {
      kind,
      title: "移交路由器",
      description: "读取工作流智能体输出，创建 AgentTask 并投递 pending Handoff。",
      sourceVariable: "agent_output",
      taskTitle: "来自工作流智能体的任务",
      targetAgent: "review-agent",
      executionMode: "manual",
      waitForCompletion: "false",
      resultVariable: "handoff_result",
      waitTimeoutSeconds: "120",
      sourceAgent: "workflow-agent",
      reasonTemplate: "请处理工作流智能体输出：{{agent_output}}",
      outputVariable: "agent_handoff_id",
    };
  }

  if (kind === "mcp_tool") {
    return {
      kind,
      title: "MCP Tool",
      description: "调用已注册的 MCP 工具",
      toolName: "",
      argumentsJson: "{}",
      outputVariable: "mcp_output",
      errorMode: "fail_safe",
    };
  }

  if (kind === "time_tool") {
    return {
      kind,
      title: "时间工具",
      description: "获取当前时间或格式化日期",
      operation: "now_iso",
      formatString: "%Y-%m-%d %H:%M:%S",
      outputVariable: "current_time",
    };
  }

  if (kind === "http_request") {
    return {
      kind,
      title: "HTTP 请求",
      description: "调用一个外部接口并保存响应文本。默认运行器不会真实出站。",
      url: "https://example.com",
      method: "GET",
      headersJson: "",
      bodyVariable: "",
      outputVariable: "http_output",
    };
  }

  if (kind === "list_operation") {
    return {
      kind,
      title: "列表操作",
      description: "把逗号分隔文本当作列表做轻量处理。",
      inputVariable: "user_input",
      operator: "length",
      joinSeparator: " / ",
      outputVariable: "list_output",
    };
  }

  if (kind === "iteration") {
    return {
      kind,
      title: "迭代处理",
      description: "逐项渲染模板，把结果汇总为 JSON 数组字符串。",
      inputVariable: "user_input",
      iterationVariable: "item",
      itemTemplate: "处理：{{item}}",
      outputVariable: "iteration_output",
    };
  }

  if (kind === "runtime_middleware") {
    const fields = payload?.fields ?? [];
    const middlewareId = payload?.runtimeMiddlewareId ?? "unknown";
    const middlewareKind =
      payload?.runtimeMiddlewareKind ?? "runtime_middleware.unknown";
    return {
      kind,
      title: payload?.title ?? "中间件节点",
      description: payload?.description ?? "运行时中间件节点。",
      runtimeMiddlewareId: middlewareId,
      runtimeMiddlewareKind: middlewareKind,
      runtimeMiddlewareFields: fields,
      runtimeMiddlewareMetadata: payload?.metadata ?? {},
      runtimeMiddlewareConfig: createRuntimeMiddlewareConfig(fields),
      middlewarePriority: "100",
    };
  }

  if (kind === "output") {
    return {
      kind,
      title: "最终交付",
      description: "把指定变量作为工作流结果交付。",
      outputVariable: "llm_output",
    };
  }

  throw new Error(`不支持的工作流节点类型：${kind}`);
}

function createNode(
  kind: WorkflowNodeKind,
  x: number,
  y: number,
  payload?: RuntimeMiddlewareDragPayload,
): WorkflowNode {
  return {
    id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`,
    type: "workflowNode",
    position: { x, y },
    data: createNodeData(kind, payload),
  };
}

function initialDefinition(workflowId: string): WorkflowDefinition {
  const inputNode: WorkflowNode = {
    id: "input-1",
    type: "workflowNode",
    position: { x: 0, y: 80 },
    data: createNodeData("input"),
  };
  const workflowAgentNode: WorkflowNode = {
    id: "workflow-agent-1",
    type: "workflowNode",
    position: { x: 340, y: 80 },
    data: createNodeData("workflow_agent"),
  };
  const outputNode: WorkflowNode = {
    id: "output-1",
    type: "workflowNode",
    position: { x: 700, y: 80 },
    data: {
      ...createNodeData("output"),
      outputVariable: "agent_output",
    },
  };

  return {
    id: workflowId,
    title: "新建 AI 流水线",
    nodes: [inputNode, workflowAgentNode, outputNode],
    edges: [
      {
        id: "edge-input-workflow-agent",
        source: inputNode.id,
        target: workflowAgentNode.id,
      },
      {
        id: "edge-workflow-agent-output",
        source: workflowAgentNode.id,
        target: outputNode.id,
      },
    ],
    updatedAt: new Date().toISOString(),
  };
}

function cloneDefinition(definition: WorkflowDefinition): WorkflowDefinition {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => ({
      ...node,
      position: { ...node.position },
      data: normalizeRecentlyEnabledNodeData({ ...node.data }),
    })),
    edges: definition.edges.map((edge) => ({ ...edge })),
  };
}

function loadDefinition(
  workflowId: string,
  controlledDefinition?: WorkflowDefinition,
) {
  if (controlledDefinition) {
    return cloneDefinition(controlledDefinition);
  }
  const storedDefinition = readStoredWorkflow(workflowId);
  if (!storedDefinition) {
    return initialDefinition(workflowId);
  }
  if (isLegacyStarterWorkflow(storedDefinition)) {
    const upgradedDefinition = initialDefinition(workflowId);
    saveStoredWorkflow(upgradedDefinition);
    return upgradedDefinition;
  }
  return cloneDefinition(storedDefinition);
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-300">{label}</span>
      <div className="mt-2">{children}</div>
    </label>
  );
}

function textInputClass() {
  return "w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";
}

function runtimeMiddlewareFieldValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): unknown {
  if (config && Object.prototype.hasOwnProperty.call(config, field.name)) {
    return config[field.name];
  }
  return field.default;
}

function runtimeMiddlewareStringValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): string {
  const value = runtimeMiddlewareFieldValue(config, field);
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

function runtimeMiddlewareBooleanValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): boolean {
  const value = runtimeMiddlewareFieldValue(config, field);
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return value.toLowerCase() === "true";
  }
  return false;
}

interface RegistryToolOption {
  name: string;
  description?: string;
}

function isRegistryToolOption(value: unknown): value is RegistryToolOption {
  return (
    typeof value === "object" &&
    value !== null &&
    "name" in value &&
    typeof (value as { name?: unknown }).name === "string"
  );
}

function workflowBooleanValue(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return value.toLowerCase() === "true";
  }
  return false;
}

function ConfigSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
      <div className="mb-3">
        <h4 className="text-sm font-semibold text-slate-100">{title}</h4>
        {description ? (
          <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        ) : null}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function ConfigSwitch({
  label,
  description,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 ${disabled ? "opacity-55" : ""}`}
    >
      <span>
        <span className="block text-sm font-medium text-slate-100">{label}</span>
        {description ? (
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            {description}
          </span>
        ) : null}
      </span>
      <input
        checked={checked}
        className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-brand-300"
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
    </label>
  );
}

function HandoffExecutionConfig({
  data,
  update,
  publishedXperts,
  publishedXpertsError,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  publishedXperts: XpertSummary[];
  publishedXpertsError: string;
}) {
  const executionMode = data.executionMode ?? "manual";
  const waitForCompletion = workflowBooleanValue(data.waitForCompletion);
  const automatic = executionMode === "xpert_auto";

  return (
    <ConfigSection
      description="人工目标进入 Inbox；已发布智能体可自动领取并执行。"
      title="移交执行"
    >
      <Field label="执行方式">
        <select
          className={textInputClass()}
          onChange={(event) => {
            const mode = event.target.value;
            const firstTarget = publishedXperts[0]
              ? `xpert:${publishedXperts[0].slug}`
              : "";
            update({
              executionMode: mode,
              targetAgent:
                mode === "xpert_auto" && !String(data.targetAgent ?? "").startsWith("xpert:")
                  ? firstTarget
                  : data.targetAgent,
              waitForCompletion: mode === "xpert_auto" ? "true" : "false",
            });
          }}
          value={executionMode}
        >
          <option className="bg-slate-950" value="manual">
            人工移交
          </option>
          <option className="bg-slate-950" value="xpert_auto">
            自动执行已发布智能体
          </option>
        </select>
      </Field>

      {automatic ? (
        <Field label="目标智能体">
          <select
            className={textInputClass()}
            onChange={(event) => update({ targetAgent: event.target.value })}
            value={data.targetAgent ?? ""}
          >
            <option className="bg-slate-950" value="">
              {publishedXperts.length ? "选择已发布智能体" : "暂无已发布智能体"}
            </option>
            {publishedXperts.map((xpert) => (
              <option
                className="bg-slate-950"
                key={xpert.id}
                value={`xpert:${xpert.slug}`}
              >
                {xpert.name} · v{xpert.published_version ?? "-"}
              </option>
            ))}
          </select>
          {publishedXpertsError ? (
            <p className="mt-2 text-xs leading-5 text-amber-200">
              {publishedXpertsError}
            </p>
          ) : null}
        </Field>
      ) : (
        <Field label="目标 Agent">
          <input
            className={textInputClass()}
            onChange={(event) => update({ targetAgent: event.target.value })}
            placeholder="例如：review-agent"
            value={data.targetAgent ?? ""}
          />
        </Field>
      )}

      {automatic ? (
        <>
          <ConfigSwitch
            checked={waitForCompletion}
            description="等待目标智能体完成，并把结果写入下游变量。关闭后源工作流立即继续。"
            label="等待执行结果"
            onChange={(checked) =>
              update({ waitForCompletion: checked ? "true" : "false" })
            }
          />
          {waitForCompletion ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="结果变量">
                <input
                  className={textInputClass()}
                  onChange={(event) =>
                    update({ resultVariable: event.target.value })
                  }
                  value={data.resultVariable ?? "handoff_result"}
                />
              </Field>
              <Field label="等待超时（秒）">
                <input
                  className={textInputClass()}
                  max={600}
                  min={5}
                  onChange={(event) =>
                    update({ waitTimeoutSeconds: event.target.value })
                  }
                  type="number"
                  value={data.waitTimeoutSeconds ?? "120"}
                />
              </Field>
            </div>
          ) : null}
        </>
      ) : null}
    </ConfigSection>
  );
}

function AgentStudioPanel({
  data,
  update,
  registryTools,
  registryToolsError,
  boundMiddlewares,
  boundResources,
  onSelectNode,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  registryTools: RegistryToolOption[];
  registryToolsError: string;
  boundMiddlewares: WorkflowNode[];
  boundResources: WorkflowNode[];
  onSelectNode: (nodeId: string) => void;
}) {
  const isWorkflowAgent = data.kind === "workflow_agent";
  const toolsEnabled = isWorkflowAgent
    ? data.toolMode === "mcp_tools"
    : data.agentMode !== "direct";
  const selectedStrategy = data.agentStrategy ?? "auto";
  const [knowledgeBases, setKnowledgeBases] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [knowledgeBasesError, setKnowledgeBasesError] = useState("");
  const selectedKnowledgeBaseIds = useMemo(
    () =>
      new Set(
        String(data.knowledgeBaseIds ?? "")
          .split(/[,\n]/)
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    [data.knowledgeBaseIds],
  );

  useEffect(() => {
    if (!isWorkflowAgent) return;
    let cancelled = false;
    void fetch("/api/rag/knowledge_bases")
      .then(async (response) => {
        if (!response.ok) throw new Error("知识库列表暂不可用。");
        return (await response.json()) as {
          knowledge_bases?: Array<{ id: string; name: string }>;
        };
      })
      .then((payload) => {
        if (!cancelled) {
          setKnowledgeBases(payload.knowledge_bases ?? []);
          setKnowledgeBasesError("");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setKnowledgeBasesError(
            error instanceof Error ? error.message : "知识库列表暂不可用。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isWorkflowAgent]);

  function toggleKnowledgeBase(kbId: string, checked: boolean) {
    const next = new Set(selectedKnowledgeBaseIds);
    if (checked) next.add(kbId);
    else next.delete(kbId);
    update({ knowledgeBaseIds: Array.from(next).slice(0, 5).join(",") });
  }
  const setStringBoolean = (
    key:
      | "disableOutput"
      | "enableFileUnderstanding"
      | "parallelToolCalls"
      | "retryOnFailure"
      | "memoryReadEnabled"
      | "memoryWriteEnabled"
      | "knowledgeReadEnabled"
      | "knowledgeWriteEnabled",
    checked: boolean,
  ) => update({ [key]: checked ? "true" : "false" });
  const toolNamesPlaceholder = registryTools.length
    ? registryTools.map((tool) => tool.name).slice(0, 3).join(", ")
    : "先在 MCP 页面连接工具 Server";

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-brand-300/25 bg-brand-300/10 px-3 py-2 text-xs leading-5 text-brand-50">
        Agent Strategy V2：工具模式支持原生 Function Calling 与 ReAct，所有调用继续经过权限、中间件和审计链路。
      </div>

      <ConfigSection
        description="控制节点身份和基础运行开关。"
        title="节点"
      >
        {isWorkflowAgent ? (
          <Field label="智能体名称">
            <input
              className={textInputClass()}
              onChange={(event) => update({ agentName: event.target.value })}
              value={data.agentName ?? ""}
            />
          </Field>
        ) : (
          <Field label="执行模式">
            <select
              className={textInputClass()}
              onChange={(event) => update({ agentMode: event.target.value })}
              value={data.agentMode ?? "tool_first"}
            >
              <option className="bg-slate-950" value="tool_first">
                tool_first：优先规划工具调用
              </option>
              <option className="bg-slate-950" value="direct">
                direct：直接回答
              </option>
            </select>
          </Field>
        )}

        <div className="grid gap-2">
          <ConfigSwitch
            checked={workflowBooleanValue(data.disableOutput)}
            description="节点仍会执行；开启后不把最终结果写入输出变量。"
            label="禁用输出"
            onChange={(checked) => setStringBoolean("disableOutput", checked)}
          />
          <ConfigSwitch
            checked={workflowBooleanValue(data.enableFileUnderstanding)}
            description="仅显示已有配置的只读状态；通用文件资产变量尚未接入，当前不能新建或修改。"
            disabled
            label="文件理解（只读）"
            onChange={(checked) =>
              setStringBoolean("enableFileUnderstanding", checked)
            }
          />
          <ConfigSwitch
            checked={workflowBooleanValue(data.parallelToolCalls)}
            description={
              !toolsEnabled
                ? "当前为直接回答模式，不会调度工具。"
                : selectedStrategy === "react"
                  ? "ReAct 每轮只允许一个 Action；切换到 auto 或 function_calling 后可启用。"
                  : "允许原生 Function Calling 在同一轮并发执行多个工具；默认关闭以保护有副作用的工具。"
            }
            disabled={!toolsEnabled || selectedStrategy === "react"}
            label="并行工具调用"
            onChange={(checked) =>
              setStringBoolean("parallelToolCalls", checked)
            }
          />
        </div>
      </ConfigSection>

      <ConfigSection
        description="定义传入智能体的可选参数，当前仅作为配置草稿保存。"
        title="参数"
      >
        <Field label="参数 JSON">
          <textarea
            className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
            onChange={(event) =>
              update({ nodeParametersJson: event.target.value })
            }
            placeholder='[{"name":"topic","optional":false}]'
            value={data.nodeParametersJson ?? "[]"}
          />
        </Field>
      </ConfigSection>

      <ConfigSection title="提示词 / 模型">
        <Field label="调用模型">
          <select
            className={textInputClass()}
            onChange={(event) => update({ modelId: event.target.value })}
            value={data.modelId ?? ""}
          >
            <option className="bg-slate-950" value="">
              请选择模型
            </option>
            {models.map((model) => (
              <option
                className="bg-slate-950 text-white"
                key={model.id}
                value={model.id}
              >
                {model.name}
              </option>
            ))}
          </select>
        </Field>

        {isWorkflowAgent ? (
          <>
            <Field label="角色提示词（支持 {{变量}}）">
              <textarea
                className={`${textInputClass()} min-h-32 resize-none leading-6`}
                onChange={(event) => update({ rolePrompt: event.target.value })}
                value={data.rolePrompt ?? ""}
              />
            </Field>
            <Field label="任务输入（支持 {{变量}}）">
              <textarea
                className={`${textInputClass()} min-h-32 resize-none leading-6`}
                onChange={(event) => update({ taskInput: event.target.value })}
                value={data.taskInput ?? ""}
              />
            </Field>
          </>
        ) : (
          <>
            <Field label="任务指令（支持 {{变量}}）">
              <textarea
                className={`${textInputClass()} min-h-36 resize-none leading-6`}
                onChange={(event) => update({ instruction: event.target.value })}
                placeholder="例如：请基于 {{user_input}} 制定处理计划。"
                value={data.instruction ?? ""}
              />
            </Field>
            <Field label="Temperature">
              <input
                className={textInputClass()}
                max={2}
                min={0}
                onChange={(event) => update({ temperature: event.target.value })}
                step={0.1}
                type="number"
                value={data.temperature ?? "0.7"}
              />
            </Field>
          </>
        )}
      </ConfigSection>

      <ConfigSection
        description="通过紫色绑定边附加 Agent 级能力，按优先级稳定执行。HITL 会在工具调用或最终输出处持久暂停。"
        title="中间件"
      >
        {boundMiddlewares.length ? (
          <div className="space-y-2">
            {boundMiddlewares.map((middleware, index) => (
              <button
                className="flex w-full items-center gap-3 rounded-lg border border-indigo-300/20 bg-indigo-300/10 px-3 py-2 text-left transition hover:border-indigo-200/45"
                key={middleware.id}
                onClick={() => onSelectNode(middleware.id)}
                type="button"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-300/15 text-xs font-semibold text-indigo-100">
                  {index + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold text-indigo-50">
                    {middleware.data.title}
                  </span>
                  <span className="block truncate text-[11px] text-indigo-200/70">
                    {String(middleware.data.runtimeMiddlewareId ?? "middleware")}
                  </span>
                </span>
                <span className="text-[10px] text-indigo-200/70">
                  P{String(middleware.data.middlewarePriority ?? "100")}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            暂无绑定中间件。可从节点库拖入运行时中间件，并从紫色端口连接到当前 Agent。
          </p>
        )}
      </ConfigSection>

      <ConfigSection
        description="知识工具读取活动版本；写入只创建待审批提议，不会直接修改活动索引。"
        title="知识库"
      >
        {isWorkflowAgent ? (
          <div className="space-y-3">
            <ConfigSwitch
              checked={workflowBooleanValue(data.knowledgeReadEnabled)}
              label="启用知识检索、原文与引用工具"
              onChange={(checked) => {
                setStringBoolean("knowledgeReadEnabled", checked);
                if (checked && data.toolMode !== "mcp_tools") {
                  update({ toolMode: "mcp_tools" });
                }
              }}
            />
            <ConfigSwitch
              checked={workflowBooleanValue(data.knowledgeWriteEnabled)}
              description="模型只能提出写入，必须在 Knowledge Inbox 中人工审批。"
              label="允许提出知识写入"
              onChange={(checked) => {
                setStringBoolean("knowledgeWriteEnabled", checked);
                if (checked && data.toolMode !== "mcp_tools") {
                  update({ toolMode: "mcp_tools" });
                }
              }}
            />
            <div className="space-y-2">
              <p className="text-xs font-semibold text-slate-300">
                可访问知识库（最多 5 个）
              </p>
              {knowledgeBases.length ? (
                knowledgeBases.map((kb) => (
                  <label
                    className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-300"
                    key={kb.id}
                  >
                    <input
                      checked={selectedKnowledgeBaseIds.has(kb.id)}
                      disabled={
                        !selectedKnowledgeBaseIds.has(kb.id) &&
                        selectedKnowledgeBaseIds.size >= 5
                      }
                      onChange={(event) =>
                        toggleKnowledgeBase(kb.id, event.target.checked)
                      }
                      type="checkbox"
                    />
                    <span className="min-w-0 flex-1 truncate">{kb.name}</span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {kb.id}
                    </span>
                  </label>
                ))
              ) : (
                <p className="rounded-md border border-dashed border-white/15 px-3 py-2 text-xs text-slate-400">
                  {knowledgeBasesError || "暂无知识库，请先在 RAG 页面创建。"}
                </p>
              )}
            </div>
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            动态知识工具仅对 workflow_agent 开放；普通 Agent 可继续连接知识节点。
          </p>
        )}
      </ConfigSection>

      <ConfigSection title="工具">
        {isWorkflowAgent ? (
          <Field label="Runtime 工具模式">
            <select
              className={textInputClass()}
              onChange={(event) => update({ toolMode: event.target.value })}
              value={data.toolMode ?? "none"}
            >
              <option className="bg-slate-950" value="none">
                none：直接调用模型
              </option>
              <option className="bg-slate-950" value="mcp_tools">
                mcp_tools：启用 MCP / Memory / Knowledge 工具
              </option>
            </select>
          </Field>
        ) : null}

        {toolsEnabled ? (
          <>
            <Field label="Agent 策略">
              <select
                className={textInputClass()}
                onChange={(event) => {
                  const nextStrategy = event.target.value as
                    | "auto"
                    | "function_calling"
                    | "react";
                  update({
                    agentStrategy: nextStrategy,
                    ...(nextStrategy === "react"
                      ? { parallelToolCalls: "false" }
                      : {}),
                  });
                }}
                value={selectedStrategy}
              >
                <option className="bg-slate-950" value="auto">
                  auto：优先 Function Calling，安全回退 ReAct
                </option>
                <option className="bg-slate-950" value="function_calling">
                  function_calling：原生工具调用
                </option>
                <option className="bg-slate-950" value="react">
                  react：文本 Action / Observation 循环
                </option>
              </select>
            </Field>
            <Field label="允许工具名（逗号分隔，留空代表全部已注册工具）">
              <input
                className={textInputClass()}
                onChange={(event) => update({ toolNames: event.target.value })}
                placeholder={toolNamesPlaceholder}
                value={data.toolNames ?? ""}
              />
              {registryToolsError ? (
                <p className="mt-2 text-xs text-rose-200">
                  {registryToolsError}
                </p>
              ) : null}
            </Field>
            <Field label="最大工具循环次数">
              <input
                className={textInputClass()}
                inputMode="numeric"
                max={20}
                min={1}
                onChange={(event) =>
                  update({ maxIterations: event.target.value })
                }
                type="number"
                value={data.maxIterations ?? "5"}
              />
            </Field>
            {isWorkflowAgent ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="最大并发">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={8}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolConcurrency: event.target.value })
                    }
                    type="number"
                    value={data.maxToolConcurrency ?? "2"}
                  />
                </Field>
                <Field label="总调用预算">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={50}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolCalls: event.target.value })
                    }
                    type="number"
                    value={data.maxToolCalls ?? "12"}
                  />
                </Field>
                <Field label="嵌套深度">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={4}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolDepth: event.target.value })
                    }
                    type="number"
                    value={data.maxToolDepth ?? "4"}
                  />
                </Field>
              </div>
            ) : null}
          </>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            当前为 none 模式，不会进入 MCP 工具循环。
          </p>
        )}

        <Field label="补充提示词（可选，支持 {{变量}}）">
          <textarea
            className={`${textInputClass()} min-h-24 resize-none leading-6`}
            onChange={(event) => update({ promptSuffix: event.target.value })}
            placeholder="可加入输出格式、语气或额外约束。"
            value={data.promptSuffix ?? ""}
          />
        </Field>
      </ConfigSection>

      <ConfigSection
        description="当前仅保存配置，后续再接入真实重试和 fallback 执行。"
        title="运行策略"
      >
        <ConfigSwitch
          checked={workflowBooleanValue(data.retryOnFailure)}
          label="失败时重试"
          onChange={(checked) => setStringBoolean("retryOnFailure", checked)}
        />
        <Field label="备用模型">
          <select
            className={textInputClass()}
            onChange={(event) => update({ fallbackModelId: event.target.value })}
            value={data.fallbackModelId ?? ""}
          >
            <option className="bg-slate-950" value="">
              不使用备用模型
            </option>
            {models.map((model) => (
              <option
                className="bg-slate-950 text-white"
                key={model.id}
                value={model.id}
              >
                {model.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="异常处理">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({ exceptionHandling: event.target.value })
            }
            value={data.exceptionHandling ?? "none"}
          >
            <option className="bg-slate-950" value="none">
              无
            </option>
            <option className="bg-slate-950" value="continue">
              继续后续节点
            </option>
            <option className="bg-slate-950" value="fail">
              标记失败
            </option>
          </select>
        </Field>
      </ConfigSection>

      <ConfigSection title="输出结构">
        <Field label="输出变量">
          <input
            className={textInputClass()}
            onChange={(event) => update({ outputVariable: event.target.value })}
            value={data.outputVariable ?? ""}
          />
        </Field>
        <Field label="输出结构模式">
          <select
            className={textInputClass()}
            onChange={(event) => update({ outputSchemaMode: event.target.value })}
            value={data.outputSchemaMode ?? "default"}
          >
            <option className="bg-slate-950" value="default">
              默认
            </option>
            <option className="bg-slate-950" value="text">
              文本
            </option>
            <option className="bg-slate-950" value="json">
              JSON
            </option>
          </select>
        </Field>
        <Field label="输出结构 JSON（可选）">
          <textarea
            className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
            onChange={(event) => update({ outputSchemaJson: event.target.value })}
            placeholder='{"content":"string"}'
            value={data.outputSchemaJson ?? ""}
          />
        </Field>
      </ConfigSection>

      <ConfigSection
        description="当前仅保存配置草稿，不会写入长期记忆。"
        title="记忆写入"
      >
        <ConfigSwitch
          checked={workflowBooleanValue(data.memoryReadEnabled)}
          label={"\u8bfb\u53d6\u76f8\u5173\u8bb0\u5fc6"}
          onChange={(checked) =>
            setStringBoolean("memoryReadEnabled", checked)
          }
        />
        <Field label={"\u8bb0\u5fc6\u8bfb\u53d6\u8303\u56f4"}>
          <select
            className={textInputClass()}
            disabled={!workflowBooleanValue(data.memoryReadEnabled)}
            onChange={(event) => update({ memoryReadScope: event.target.value })}
            value={data.memoryReadScope ?? "both"}
          >
            <option className="bg-slate-950" value="both">{"\u4f1a\u8bdd + \u667a\u80fd\u4f53"}</option>
            <option className="bg-slate-950" value="conversation">{"\u4ec5\u5f53\u524d\u4f1a\u8bdd"}</option>
            <option className="bg-slate-950" value="xpert">{"\u4ec5\u667a\u80fd\u4f53\u957f\u671f\u8bb0\u5fc6"}</option>
          </select>
        </Field>
        <ConfigSwitch
          checked={workflowBooleanValue(data.memoryWriteEnabled)}
          label="写入记忆"
          onChange={(checked) =>
            setStringBoolean("memoryWriteEnabled", checked)
          }
        />
        <Field label="记忆目标">
          <input
            className={textInputClass()}
            disabled={!workflowBooleanValue(data.memoryWriteEnabled)}
            onChange={(event) =>
              update({ memoryWriteTarget: event.target.value })
            }
            placeholder="例如：agent_memory"
            value={data.memoryWriteTarget ?? ""}
          />
        </Field>
      </ConfigSection>

      {isWorkflowAgent ? (
        <ConfigSection
          description="资源绑定不进入控制流，只向当前智能体开放对应 Runtime 工具。"
          title="资源绑定"
        >
          {boundResources.length ? (
            <div className="space-y-2">
              {boundResources.map((resource) => (
                <button
                  className="flex w-full items-center gap-3 rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-left transition hover:border-cyan-200/45"
                  key={resource.id}
                  onClick={() => onSelectNode(resource.id)}
                  type="button"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-cyan-300/15 text-[10px] font-semibold text-cyan-100">
                    {resource.data.kind === "external_xpert"
                      ? "XP"
                      : resource.data.kind === "knowledge_base"
                        ? "KB"
                        : resource.data.kind === "toolset_resource"
                          ? "TS"
                          : "PL"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-cyan-50">
                      {resource.data.title}
                    </span>
                    <span className="block truncate text-[11px] text-cyan-200/70">
                      {resource.data.kind === "external_xpert"
                        ? String(resource.data.toolName || "external_xpert")
                        : resource.data.kind === "knowledge_base"
                          ? String(resource.data.knowledgeBaseId || "未选择知识库")
                          : resource.data.kind === "toolset_resource"
                            ? String(resource.data.toolsetId || "未选择 Toolset")
                            : String(resource.data.pluginId || "未选择 Plugin")}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
              从节点库拖入外部智能体、知识库、Toolset 或 Plugin，再用专用端口绑定到当前智能体。
            </p>
          )}
        </ConfigSection>
      ) : null}
    </div>
  );
}

interface WorkflowResourceOption {
  id: string;
  slug?: string;
  name: string;
  description?: string;
  status?: string;
  published_version?: number | null;
  active_version_id?: string | null;
  document_count?: number;
  tool_count?: number;
  prompt_count?: number;
  skill_count?: number;
  middleware_count?: number;
}

function useWorkflowResourceOptions(resourceKind: string) {
  const [options, setOptions] = useState<WorkflowResourceOption[]>([]);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void fetch(`/api/workflow/resource-options?kind=${resourceKind}`)
      .then(async (response) => {
        const payload = (await response.json()) as {
          items?: WorkflowResourceOption[];
        };
        if (!response.ok || !Array.isArray(payload.items)) {
          throw new Error("资源选项暂不可用。");
        }
        if (!cancelled) {
          setOptions(payload.items);
          setLoadError("");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setOptions([]);
          setLoadError(
            error instanceof Error ? error.message : "资源选项加载失败。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resourceKind]);

  return { loadError, options };
}

function ResourceNodeConfig({
  data,
  update,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const resourceKind =
    data.kind === "external_xpert"
      ? "external_xpert"
      : data.kind === "toolset_resource"
        ? "toolset"
        : data.kind === "plugin_resource"
          ? "plugin"
          : "knowledge_base";
  const { loadError, options } = useWorkflowResourceOptions(resourceKind);

  if (data.kind === "external_xpert") {
    return (
      <ConfigSection
        description="发布智能体时会把当前发布版本解析为不可变固定版本。"
        title="外部智能体"
      >
        <Field label="已发布智能体">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                xpertId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.xpertId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择外部智能体
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="工具名称">
          <input
            className={textInputClass()}
            onChange={(event) => update({ toolName: event.target.value })}
            placeholder="research_expert"
            value={data.toolName ?? ""}
          />
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.xpertId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "current_published"}
          >
            <option className="bg-slate-950" value="current_published">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  if (data.kind === "toolset_resource") {
    return (
      <ConfigSection
        description="草稿可跟随当前发布版本；发布智能体时会解析并固定不可变 Toolset 版本。"
        title="MCP Toolset"
      >
        <Field label="已发布 Toolset">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                toolsetId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.toolsetId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择 Toolset
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"} ·{" "}
                {item.tool_count ?? 0} tools
              </option>
            ))}
          </select>
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.toolsetId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "current_published"}
          >
            <option className="bg-slate-950" value="current_published">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        <a
          className="inline-flex text-xs font-semibold text-cyan-200 hover:text-cyan-100"
          href="/toolsets"
        >
          打开 Toolset 管理页
        </a>
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  if (data.kind === "plugin_resource") {
    return (
      <ConfigSection
        description="草稿可跟随当前发布版本；发布智能体时会解析并固定不可变 Plugin 版本。"
        title="Plugin 资源"
      >
        <Field label="已发布 Plugin">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                pluginId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.pluginId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择 Plugin
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"} ·{" "}
                {(item.prompt_count ?? 0) + (item.skill_count ?? 0) + (item.tool_count ?? 0) + (item.middleware_count ?? 0)} resources
              </option>
            ))}
          </select>
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.pluginId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "latest"}
          >
            <option className="bg-slate-950" value="latest">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        <a
          className="inline-flex text-xs font-semibold text-cyan-200 hover:text-cyan-100"
          href="/plugins"
        >
          打开 Plugin 管理页
        </a>
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  return (
    <ConfigSection
      description="使用知识库活动索引的 Retrieval Profile；该绑定只提供读取、原文和引用工具。"
      title="知识库资源"
    >
      <Field label="知识库">
        <select
          className={textInputClass()}
          onChange={(event) =>
            update({ knowledgeBaseId: event.target.value })
          }
          value={data.knowledgeBaseId ?? ""}
        >
          <option className="bg-slate-950" value="">
            选择知识库
          </option>
          {options.map((item) => (
            <option className="bg-slate-950" key={item.id} value={item.id}>
              {item.name} ·{" "}
              {item.status === "active"
                ? `active ${item.active_version_id ?? ""}`
                : "暂无活动索引"}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Top-K">
          <input
            className={textInputClass()}
            max={10}
            min={1}
            onChange={(event) => update({ topK: event.target.value })}
            type="number"
            value={data.topK ?? "5"}
          />
        </Field>
        <Field label="Score 阈值">
          <input
            className={textInputClass()}
            max={1}
            min={0}
            onChange={(event) =>
              update({ scoreThreshold: event.target.value })
            }
            step={0.05}
            type="number"
            value={data.scoreThreshold ?? "0"}
          />
        </Field>
      </div>
      {loadError ? (
        <p className="text-xs leading-5 text-amber-200">{loadError}</p>
      ) : null}
    </ConfigSection>
  );
}

function KnowledgeBaseSelector({
  allowLegacyEmpty = false,
  onChange,
  value,
}: {
  allowLegacyEmpty?: boolean;
  onChange: (value: string) => void;
  value: string;
}) {
  const { loadError, options } = useWorkflowResourceOptions("knowledge_base");
  return (
    <>
      <select
        className={textInputClass()}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option className="bg-slate-950" value="">
          {allowLegacyEmpty ? "未固定（仅单知识库旧图兼容）" : "选择知识库"}
        </option>
        {options.map((item) => (
          <option className="bg-slate-950" key={item.id} value={item.id}>
            {item.name} · {item.active_version_id ? "活动索引可用" : "暂无活动索引"}
          </option>
        ))}
      </select>
      {loadError ? (
        <p className="mt-2 text-xs leading-5 text-amber-200">{loadError}</p>
      ) : null}
    </>
  );
}

function KnowledgeRetrievalNodeConfig({
  data,
  update,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const isLegacy = !data.contractVersion;
  return (
    <>
      <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
        {isLegacy
          ? "兼容旧版文本契约。保存现有工作流不会自动改变输出类型。"
          : "V2 只执行活动知识版本检索；结果模式返回上下文、来源、CitationAnchor 与安全诊断。"}
      </div>
      <Field label="知识库">
        <KnowledgeBaseSelector
          allowLegacyEmpty={isLegacy}
          onChange={(value) => update({ knowledgeBaseId: value })}
          value={data.knowledgeBaseId ?? ""}
        />
      </Field>
      <Field label="查询变量">
        <input
          className={textInputClass()}
          onChange={(event) => update({ queryVariable: event.target.value })}
          value={data.queryVariable ?? ""}
        />
      </Field>
      <Field label="返回片段数 Top K">
        <input
          className={textInputClass()}
          inputMode="numeric"
          max={isLegacy ? 20 : 10}
          min={1}
          onChange={(event) => update({ top_k: event.target.value })}
          type="number"
          value={data.top_k ?? "5"}
        />
      </Field>
      {!isLegacy ? (
        <Field label="返回模式">
          <select
            className={textInputClass()}
            onChange={(event) => update({ returnMode: event.target.value as "context" | "result" })}
            value={data.returnMode ?? "result"}
          >
            <option className="bg-slate-950" value="result">
              类型化结果（推荐）
            </option>
            <option className="bg-slate-950" value="context">
              纯文本上下文
            </option>
          </select>
        </Field>
      ) : null}
      <Field label="输出变量">
        <input
          className={textInputClass()}
          onChange={(event) => update({ outputVariable: event.target.value })}
          value={data.outputVariable ?? ""}
        />
      </Field>
    </>
  );
}

function LegacyKnowledgeCitationConfig({
  data,
  update,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
}) {
  return (
    <>
      <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
        该节点已弃用，仅用于旧工作流兼容。新流程请使用知识检索 V2 的类型化结果。
      </div>
      <Field label="查询变量">
        <input
          className={textInputClass()}
          onChange={(event) => update({ queryVariable: event.target.value })}
          value={data.queryVariable ?? ""}
        />
      </Field>
      <Field label="知识库">
        <KnowledgeBaseSelector
          allowLegacyEmpty
          onChange={(value) => update({ knowledgeBaseId: value })}
          value={data.knowledgeBaseId ?? ""}
        />
      </Field>
      <Field label="返回引用数 Top K">
        <input
          className={textInputClass()}
          inputMode="numeric"
          max={10}
          min={1}
          onChange={(event) => update({ top_k: event.target.value })}
          type="number"
          value={data.top_k ?? "4"}
        />
      </Field>
      <Field label="输出变量">
        <input
          className={textInputClass()}
          onChange={(event) => update({ outputVariable: event.target.value })}
          value={data.outputVariable ?? ""}
        />
      </Field>
    </>
  );
}

interface NodeConfigProps {
  node: WorkflowNode | null;
  onChange: (nodeId: string, data: Partial<WorkflowNodeData>) => void;
  onRuntimeMiddlewareConfigChange: (
    nodeId: string,
    fieldName: string,
    value: unknown,
  ) => void;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  onSelectNode: (nodeId: string) => void;
}

function NodeConfig({
  node,
  onChange,
  onRuntimeMiddlewareConfigChange,
  nodes,
  edges,
  onSelectNode,
}: NodeConfigProps) {
  const [registryTools, setRegistryTools] = useState<RegistryToolOption[]>([]);
  const [registryToolsError, setRegistryToolsError] = useState("");
  const [publishedXperts, setPublishedXperts] = useState<XpertSummary[]>([]);
  const [publishedXpertsError, setPublishedXpertsError] = useState("");
  const [installedSkills, setInstalledSkills] = useState<TrustSelectableSkill[]>([]);
  const [clientHosts, setClientHosts] = useState<Array<{
    host_id: string;
    name: string;
    status: string;
    host_type?: "chrome" | "office";
    office_app?: "word" | "excel" | "powerpoint" | "";
    bound_tab?: { bound?: boolean; title?: string; origin?: string };
    document_binding?: { bound?: boolean; title?: string; binding_id?: string };
    revoked?: boolean;
  }>>([]);

  useEffect(() => {
    let cancelled = false;

    async function loadRegistryTools() {
      try {
        const response = await fetch("/api/registry/tools");
        if (!response.ok) {
          throw new Error("工具注册表暂时不可用。");
        }
        const payload: unknown = await response.json();
        const tools = Array.isArray(payload)
          ? payload.filter(isRegistryToolOption)
          : [];
        if (!cancelled) {
          setRegistryTools(tools);
          setRegistryToolsError("");
        }
      } catch (error) {
        if (!cancelled) {
          setRegistryTools([]);
          setRegistryToolsError(
            error instanceof Error ? error.message : "工具注册表加载失败。",
          );
        }
      }
    }

    async function loadPublishedXperts() {
      try {
        const response = await fetch("/api/xperts?status=published&limit=200");
        const payload = (await response.json()) as XpertListResponse;
        if (!response.ok || !Array.isArray(payload.items)) {
          throw new Error("已发布智能体列表暂时不可用。");
        }
        if (!cancelled) {
          setPublishedXperts(payload.items);
          setPublishedXpertsError("");
        }
      } catch (error) {
        if (!cancelled) {
          setPublishedXperts([]);
          setPublishedXpertsError(
            error instanceof Error ? error.message : "已发布智能体列表加载失败。",
          );
        }
      }
    }

    async function loadClientHosts() {
      try {
        const response = await fetch("/api/runtime/client-hosts");
        const payload = (await response.json()) as { hosts?: typeof clientHosts };
        if (!response.ok) throw new Error("客户端宿主列表暂不可用");
        if (!cancelled) setClientHosts(payload.hosts ?? []);
      } catch {
        if (!cancelled) setClientHosts([]);
      }
    }

    async function loadInstalledSkills() {
      try {
        const response = await fetch("/api/skills/installed");
        const payload = (await response.json()) as { skills?: TrustSelectableSkill[] };
        if (!response.ok || !Array.isArray(payload.skills)) {
          throw new Error("已安装 Skill 列表暂不可用");
        }
        if (!cancelled) setInstalledSkills(payload.skills);
      } catch {
        if (!cancelled) setInstalledSkills([]);
      }
    }

    void loadRegistryTools();
    void loadPublishedXperts();
    void loadClientHosts();
    void loadInstalledSkills();

    return () => {
      cancelled = true;
    };
  }, []);

  if (!node) {
    return (
      <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-4 py-8 text-center text-sm leading-6 text-slate-400">
        点击画布上的工位牌，即可编辑节点配置。
      </div>
    );
  }

  const data = node.data;
  const update = (patch: Partial<WorkflowNodeData>) => onChange(node.id, patch);
  const runtimeMiddlewareConfig = isRecord(data.runtimeMiddlewareConfig)
    ? data.runtimeMiddlewareConfig
    : undefined;
  const appendTrustedSkill = (skillId: string) => {
    if (!skillId || !runtimeMiddlewareConfig) return;
    const current = String(runtimeMiddlewareConfig.skill_ids ?? "")
      .split(/[,\n]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!current.includes(skillId)) current.push(skillId);
    onRuntimeMiddlewareConfigChange(node.id, "skill_ids", current.join(", "));
  };
  const boundMiddlewares =
    data.kind === "workflow_agent"
      ? edges
          .filter(
            (edge) =>
              edge.target === node.id && edge.targetHandle === "middleware",
          )
          .map((edge) => nodes.find((candidate) => candidate.id === edge.source))
          .filter(
            (candidate): candidate is WorkflowNode =>
              candidate?.data.kind === "runtime_middleware",
          )
          .sort((left, right) => {
            const priorityDifference =
              Number(left.data.middlewarePriority ?? 100) -
              Number(right.data.middlewarePriority ?? 100);
            return priorityDifference || left.id.localeCompare(right.id);
          })
      : [];
  const boundResources =
    data.kind === "workflow_agent"
      ? edges
          .filter(
            (edge) =>
              edge.target === node.id &&
              ["expert", "knowledge", "toolset", "plugin"].includes(
                edge.targetHandle ?? "",
              ),
          )
          .map((edge) => nodes.find((candidate) => candidate.id === edge.source))
          .filter(
            (candidate): candidate is WorkflowNode =>
              candidate?.data.kind === "external_xpert" ||
              candidate?.data.kind === "knowledge_base" ||
              candidate?.data.kind === "toolset_resource" ||
              candidate?.data.kind === "plugin_resource",
          )
      : [];
  const updateRuntimeMiddlewareConfig = (fieldName: string, value: unknown) =>
    onRuntimeMiddlewareConfigChange(node.id, fieldName, value);
  const skillCatalogApprovalState =
    data.kind === "runtime_middleware" &&
    data.runtimeMiddlewareId === "skills_runtime"
      ? getSkillCatalogApprovalState(nodes, edges, node.id)
      : null;

  return (
    <div className="space-y-4">
      <Field label="工位名称">
        <input
          className={textInputClass()}
          onChange={(event) => update({ title: event.target.value })}
          value={data.title}
        />
      </Field>

      <Field label="说明">
        <textarea
          className={`${textInputClass()} min-h-20 resize-none leading-6`}
          onChange={(event) => update({ description: event.target.value })}
          value={data.description}
        />
      </Field>

      {[
        "json_serialize",
        "json_deserialize",
        "annotation",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
      ].includes(data.kind) ? (
        <WorkflowTypedDataNodeConfig data={data} onChange={update} />
      ) : null}

      {data.kind === "input" ? (
        <Field label="输入变量名">
          <input
            className={textInputClass()}
            onChange={(event) => update({ variableName: event.target.value })}
            value={data.variableName ?? ""}
          />
        </Field>
      ) : null}

      {data.kind === "llm" ? (
        <>
          <Field label="调用模型">
            <select
              className={textInputClass()}
              onChange={(event) => update({ modelId: event.target.value })}
              value={data.modelId ?? DEFAULT_CHAT_MODEL_ID}
            >
              {models.map((model) => (
                <option
                  className="bg-slate-950 text-white"
                  key={model.id}
                  value={model.id}
                >
                  {model.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="提示词（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-36 resize-none leading-6`}
              onChange={(event) => update({ prompt: event.target.value })}
              value={data.prompt ?? ""}
            />
          </Field>
          <Field label="输出变量名">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "condition" ? (
        <>
          <Field label="判断变量">
            <input
              className={textInputClass()}
              onChange={(event) =>
                update({ conditionVariable: event.target.value })
              }
              value={data.conditionVariable ?? ""}
            />
          </Field>
          <Field label="判断方式">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({
                  conditionOperator: event.target.value as ConditionOperator,
                })
              }
              value={data.conditionOperator ?? "contains"}
            >
              <option className="bg-slate-950" value="contains">
                包含
              </option>
              <option className="bg-slate-950" value="equals">
                等于
              </option>
            </select>
          </Field>
          <Field label="比较值">
            <input
              className={textInputClass()}
              onChange={(event) => update({ conditionValue: event.target.value })}
              value={data.conditionValue ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "code" ? (
        <>
          <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
            安全提示：MVP 不执行任意代码，仅提供 upper、lower、replace、concat 四种字符串操作。
          </div>
          <Field label="内置操作">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({ codeOperation: event.target.value as CodeOperation })
              }
              value={data.codeOperation ?? "upper"}
            >
              <option className="bg-slate-950" value="upper">
                转大写
              </option>
              <option className="bg-slate-950" value="lower">
                转小写
              </option>
              <option className="bg-slate-950" value="replace">
                替换
              </option>
              <option className="bg-slate-950" value="concat">
                拼接
              </option>
              <option className="bg-slate-950" value="python">
                Python sandbox
              </option>
            </select>
          </Field>
          <Field label="输入变量">
            <input
              className={textInputClass()}
              onChange={(event) =>
                update({ codeInputVariable: event.target.value })
              }
              value={data.codeInputVariable ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) =>
                update({ codeOutputVariable: event.target.value })
              }
              value={data.codeOutputVariable ?? ""}
            />
          </Field>
          {data.codeOperation === "replace" ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="把">
                <input
                  className={textInputClass()}
                  onChange={(event) => update({ replaceFrom: event.target.value })}
                  value={data.replaceFrom ?? ""}
                />
              </Field>
              <Field label="替换为">
                <input
                  className={textInputClass()}
                  onChange={(event) => update({ replaceTo: event.target.value })}
                  value={data.replaceTo ?? ""}
                />
              </Field>
            </div>
          ) : null}
          {data.codeOperation === "concat" ? (
            <Field label="追加内容">
              <input
                className={textInputClass()}
                onChange={(event) => update({ concatValue: event.target.value })}
                value={data.concatValue ?? ""}
              />
            </Field>
          ) : null}
          {data.codeOperation === "python" ? (
            <Field label="Python 代码">
              <textarea
                className={`${textInputClass()} min-h-40 resize-none font-mono text-xs leading-5`}
                onChange={(event) => update({ pythonCode: event.target.value })}
                placeholder="print(len(input.split()))"
                value={data.pythonCode ?? ""}
              />
              <p className="mt-2 text-xs leading-5 text-slate-400">
                可用变量：input（输入变量内容）和 variables（全部变量字典）。请用 print() 输出结果。
              </p>
            </Field>
          ) : null}
        </>
      ) : null}

      {data.kind === "variable_assign" ? (
        <>
          <Field label="写入变量名">
            <input
              className={textInputClass()}
              onChange={(event) => update({ variableName: event.target.value })}
              value={data.variableName ?? ""}
            />
          </Field>
          <Field label="赋值模板（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-28 resize-none leading-6`}
              onChange={(event) => update({ template: event.target.value })}
              value={data.template ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "template_transform" ? (
        <>
          <Field label="模板内容（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-36 resize-none leading-6`}
              onChange={(event) => update({ template: event.target.value })}
              value={data.template ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "variable_aggregator" ? (
        <>
          <Field label="变量名列表（逗号分隔）">
            <input
              className={textInputClass()}
              onChange={(event) => update({ variableNames: event.target.value })}
              value={data.variableNames ?? ""}
            />
          </Field>
          <Field label="输出模板（可选，支持 {name} / {value}）">
            <textarea
              className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
              onChange={(event) => update({ outputTemplate: event.target.value })}
              value={data.outputTemplate ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "parameter_extractor" ? (
        <>
          <Field label="待提取文本变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ inputVariable: event.target.value })}
              value={data.inputVariable ?? ""}
            />
          </Field>
          <Field label="调用模型">
            <select
              className={textInputClass()}
              onChange={(event) => update({ modelId: event.target.value })}
              value={data.modelId ?? DEFAULT_CHAT_MODEL_ID}
            >
              {models.map((model) => (
                <option
                  className="bg-slate-950 text-white"
                  key={model.id}
                  value={model.id}
                >
                  {model.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="字段描述">
            <textarea
              className={`${textInputClass()} min-h-28 resize-none leading-6`}
              onChange={(event) => update({ schema: event.target.value })}
              value={data.schema ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "knowledge_retrieval" ? (
        <KnowledgeRetrievalNodeConfig data={data} update={update} />
      ) : null}

      {data.kind === "knowledge_citation" ? (
        <LegacyKnowledgeCitationConfig data={data} update={update} />
      ) : null}

      {data.kind === "document_extractor" ? (
        <>
          {data.sourcePathVariable && !data.assetIdVariable ? (
            <>
              <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
                这是旧版路径配置，仅保留一个发布周期的读取兼容。当前不能修改或新建路径型配置，请迁移到文件资产变量。
              </div>
              <Field label="旧路径变量（只读）">
                <input
                  aria-readonly="true"
                  className={`${textInputClass()} cursor-not-allowed opacity-70`}
                  readOnly
                  value={String(data.sourcePathVariable)}
                />
              </Field>
            </>
          ) : (
            <>
              <div className="rounded-lg border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs leading-5 text-sky-50">
                运行前在试运行面板选择文件。后端只接受当前工作流作用域内的 asset_id，不接受服务器路径。
              </div>
              <Field label="文件资产变量">
                <input
                  className={textInputClass()}
                  onChange={(event) =>
                    update({ assetIdVariable: event.target.value })
                  }
                  value={data.assetIdVariable ?? ""}
                />
              </Field>
            </>
          )}
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "human_intervention" ? (
        <>
          <div className="rounded-lg border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs leading-5 text-sky-50">
            运行到这里会暂停流水线，等待用户在运行面板提交文本后继续。
          </div>
          <Field label="提示文案（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-32 resize-none leading-6`}
              onChange={(event) => update({ prompt: event.target.value })}
              value={data.prompt ?? ""}
            />
          </Field>
          <Field label="写入变量名">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "question_classifier" ? (
        <>
          <div className="rounded-lg border border-yellow-300/25 bg-yellow-300/10 px-3 py-2 text-xs leading-5 text-yellow-50">
            默认只按关键词规则分类；LLM 回退关闭时不会产生模型调用。
          </div>
          <Field label="输入文本变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ inputVariable: event.target.value })}
              value={data.inputVariable ?? ""}
            />
          </Field>
          <Field label="分类规则 JSON">
            <textarea
              className={`${textInputClass()} min-h-36 resize-none font-mono text-xs leading-5`}
              onChange={(event) => update({ categories: event.target.value })}
              placeholder='{"投诉":["差","投诉","退款"],"咨询":["咨询","如何","怎么"]}'
              value={data.categories ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          <Field label="默认类别">
            <input
              className={textInputClass()}
              onChange={(event) => update({ defaultCategory: event.target.value })}
              value={data.defaultCategory ?? ""}
            />
          </Field>
          <Field label="匹配模式">
            <select
              className={textInputClass()}
              onChange={(event) => update({ matchMode: event.target.value })}
              value={data.matchMode ?? "contains_any"}
            >
              <option className="bg-slate-950" value="contains_any">
                任一关键词命中
              </option>
              <option className="bg-slate-950" value="contains_all">
                全部关键词命中
              </option>
            </select>
          </Field>
          <Field label="大小写敏感">
            <select
              className={textInputClass()}
              onChange={(event) => update({ caseSensitive: event.target.value })}
              value={data.caseSensitive ?? "false"}
            >
              <option className="bg-slate-950" value="false">
                否
              </option>
              <option className="bg-slate-950" value="true">
                是
              </option>
            </select>
          </Field>
          <Field label="启用 LLM 回退">
            <select
              className={textInputClass()}
              onChange={(event) => update({ useLlmFallback: event.target.value })}
              value={data.useLlmFallback ?? "false"}
            >
              <option className="bg-slate-950" value="false">
                否
              </option>
              <option className="bg-slate-950" value="true">
                是
              </option>
            </select>
          </Field>
          {data.useLlmFallback === "true" ? (
            <>
              <Field label="回退模型">
                <select
                  className={textInputClass()}
                  onChange={(event) => update({ modelId: event.target.value })}
                  value={data.modelId ?? ""}
                >
                  <option className="bg-slate-950" value="">
                    请选择模型
                  </option>
                  {models.map((model) => (
                    <option
                      className="bg-slate-950"
                      key={model.id}
                      value={model.id}
                    >
                      {model.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="LLM 回退提示词（可选，支持 {{变量}}）">
                <textarea
                  className={`${textInputClass()} min-h-28 resize-none leading-6`}
                  onChange={(event) =>
                    update({ llmFallbackPrompt: event.target.value })
                  }
                  placeholder="留空则使用后端默认分类提示词。"
                  value={data.llmFallbackPrompt ?? ""}
                />
              </Field>
            </>
          ) : null}
        </>
      ) : null}

      {data.kind === "external_xpert" ||
      data.kind === "knowledge_base" ||
      data.kind === "toolset_resource" ||
      data.kind === "plugin_resource" ? (
        <ResourceNodeConfig data={data} update={update} />
      ) : null}

      {data.kind === "agent" || data.kind === "workflow_agent" ? (
        <AgentStudioPanel
          boundMiddlewares={boundMiddlewares}
          boundResources={boundResources}
          data={data}
          onSelectNode={onSelectNode}
          registryTools={registryTools}
          registryToolsError={registryToolsError}
          update={update}
        />
      ) : null}

      {data.kind === "agent_task" ? (
        <>
          <div className="rounded-lg border border-violet-300/25 bg-violet-300/10 px-3 py-2 text-xs leading-5 text-violet-50">
            该节点会在运行时创建 Agent Task，并将新任务的 task_id 写入输出变量；当前不做真实多 Agent 调度。
          </div>
          <Field label="任务标题（支持 {{变量}}）">
            <input
              className={textInputClass()}
              onChange={(event) => update({ taskTitle: event.target.value })}
              value={data.taskTitle ?? ""}
            />
          </Field>
          <Field label="任务输入（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-32 resize-none leading-6`}
              onChange={(event) => update({ taskInput: event.target.value })}
              value={data.taskInput ?? ""}
            />
          </Field>
          <Field label="指派智能体">
            <input
              className={textInputClass()}
              onChange={(event) => update({ assignedAgent: event.target.value })}
              value={data.assignedAgent ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "agent_handoff" ? (
        <>
          <div className="rounded-lg border border-purple-300/25 bg-purple-300/10 px-3 py-2 text-xs leading-5 text-purple-50">
            读取已有 Agent Task 并创建 Handoff。人工目标进入 Inbox，指定智能体目标可自动执行并回传结果。
          </div>
          <Field label="任务 ID 变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ taskIdVariable: event.target.value })}
              value={data.taskIdVariable ?? ""}
            />
          </Field>
          <Field label="来源智能体">
            <input
              className={textInputClass()}
              onChange={(event) => update({ sourceAgent: event.target.value })}
              value={data.sourceAgent ?? ""}
            />
          </Field>
          <HandoffExecutionConfig
            data={data}
            publishedXperts={publishedXperts}
            publishedXpertsError={publishedXpertsError}
            update={update}
          />
          <Field label="移交理由（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-28 resize-none leading-6`}
              onChange={(event) => update({ reason: event.target.value })}
              value={data.reason ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "handoff_router" ? (
        <>
          <div className="rounded-lg border border-fuchsia-300/25 bg-fuchsia-300/10 px-3 py-2 text-xs leading-5 text-fuchsia-50">
            读取上游智能体输出并创建任务。可投递到人工 Inbox，也可调用已发布智能体完成协作。
          </div>
          <Field label="来源变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ sourceVariable: event.target.value })}
              value={data.sourceVariable ?? ""}
            />
          </Field>
          <Field label="任务标题（支持 {{变量}}）">
            <input
              className={textInputClass()}
              onChange={(event) => update({ taskTitle: event.target.value })}
              value={data.taskTitle ?? ""}
            />
          </Field>
          <Field label="来源智能体">
            <input
              className={textInputClass()}
              onChange={(event) => update({ sourceAgent: event.target.value })}
              value={data.sourceAgent ?? ""}
            />
          </Field>
          <HandoffExecutionConfig
            data={data}
            publishedXperts={publishedXperts}
            publishedXpertsError={publishedXpertsError}
            update={update}
          />
          <Field label="移交理由模板（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-28 resize-none leading-6`}
              onChange={(event) => update({ reasonTemplate: event.target.value })}
              value={data.reasonTemplate ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "mcp_tool" ? (
        <>
          <div className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-50">
            先在 MCP 工具采购页连接 Server，工具会自动进入全局注册表。
          </div>
          <Field label="MCP 工具">
            <select
              className={textInputClass()}
              onChange={(event) => update({ toolName: event.target.value })}
              value={data.toolName ?? ""}
            >
              <option className="bg-slate-950" value="">
                {registryTools.length ? "请选择工具" : "暂无已注册工具"}
              </option>
              {registryTools.map((tool) => (
                <option className="bg-slate-950" key={tool.name} value={tool.name}>
                  {tool.name}
                </option>
              ))}
            </select>
            {registryToolsError ? (
              <p className="mt-2 text-xs text-rose-200">{registryToolsError}</p>
            ) : null}
          </Field>
          <Field label="参数 JSON（支持 {{变量}}）">
            <textarea
              className={`${textInputClass()} min-h-32 resize-none font-mono text-xs leading-5`}
              onChange={(event) => update({ argumentsJson: event.target.value })}
              placeholder='{"url":"{{user_input}}"}'
              value={data.argumentsJson ?? "{}"}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "time_tool" ? (
        <>
          <Field label="时间操作">
            <select
              className={textInputClass()}
              onChange={(event) => update({ operation: event.target.value })}
              value={data.operation ?? "now_iso"}
            >
              <option className="bg-slate-950" value="now_iso">
                当前时间 ISO
              </option>
              <option className="bg-slate-950" value="now_epoch">
                当前时间戳
              </option>
              <option className="bg-slate-950" value="format">
                按格式输出
              </option>
            </select>
          </Field>
          <Field label="格式字符串">
            <input
              className={textInputClass()}
              disabled={data.operation !== "format"}
              onChange={(event) => update({ formatString: event.target.value })}
              placeholder="%Y-%m-%d %H:%M:%S"
              value={data.formatString ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "http_request" ? (
        <>
          <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
            安全提示：默认运行器不会真实发起出站请求；管理员开启后才会调用外部 URL。
          </div>
          <Field label="请求方法">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({ method: event.target.value as HttpRequestMethod })
              }
              value={data.method ?? "GET"}
            >
              <option className="bg-slate-950" value="GET">
                GET
              </option>
              <option className="bg-slate-950" value="POST">
                POST
              </option>
            </select>
          </Field>
          <Field label="URL（支持 {{变量}}）">
            <input
              className={textInputClass()}
              onChange={(event) => update({ url: event.target.value })}
              value={data.url ?? ""}
            />
          </Field>
          <Field label="请求头 JSON（可选）">
            <textarea
              className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
              onChange={(event) => update({ headersJson: event.target.value })}
              placeholder='{"Content-Type":"application/json"}'
              value={data.headersJson ?? ""}
            />
          </Field>
          <Field label="请求正文变量（POST 可选）">
            <input
              className={textInputClass()}
              onChange={(event) => update({ bodyVariable: event.target.value })}
              value={data.bodyVariable ?? ""}
            />
          </Field>
          <Field label="响应输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "list_operation" ? (
        <>
          <Field label="输入列表变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ inputVariable: event.target.value })}
              value={data.inputVariable ?? ""}
            />
          </Field>
          <Field label="列表操作">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({ operator: event.target.value as ListOperationOperator })
              }
              value={data.operator ?? "length"}
            >
              <option className="bg-slate-950" value="length">
                计算长度
              </option>
              <option className="bg-slate-950" value="join">
                拼接文本
              </option>
              <option className="bg-slate-950" value="first">
                取第一项
              </option>
              <option className="bg-slate-950" value="last">
                取最后一项
              </option>
            </select>
          </Field>
          {data.operator === "join" ? (
            <Field label="拼接分隔符">
              <input
                className={textInputClass()}
                onChange={(event) => update({ joinSeparator: event.target.value })}
                value={data.joinSeparator ?? ""}
              />
            </Field>
          ) : null}
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "iteration" ? (
        <>
          <Field label="输入列表变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ inputVariable: event.target.value })}
              value={data.inputVariable ?? ""}
            />
          </Field>
          <Field label="单项变量名">
            <input
              className={textInputClass()}
              onChange={(event) =>
                update({ iterationVariable: event.target.value })
              }
              value={data.iterationVariable ?? ""}
            />
          </Field>
          <Field label="单项模板（支持 {{单项变量}}）">
            <textarea
              className={`${textInputClass()} min-h-28 resize-none leading-6`}
              onChange={(event) => update({ itemTemplate: event.target.value })}
              value={data.itemTemplate ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <input
              className={textInputClass()}
              onChange={(event) => update({ outputVariable: event.target.value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "runtime_middleware" ? (
        <div className="space-y-4">
          <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
            使用紫色端口绑定到一个 workflow_agent，或使用普通端口作为线性中间件。两种连接方式不可混用。
          </div>
          <Field label="执行优先级（0-1000）">
            <input
              className={textInputClass()}
              max={1000}
              min={0}
              onChange={(event) =>
                update({ middlewarePriority: event.target.value })
              }
              type="number"
              value={data.middlewarePriority ?? "100"}
            />
          </Field>
          <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
            绑定模式仅作用于目标 workflow_agent；线性模式会影响其后执行的智能体。核心中间件已接入真实 MiddlewarePipeline。
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2">
            <p className="text-xs font-semibold text-slate-200">
              {data.runtimeMiddlewareKind ?? "runtime_middleware.unknown"}
            </p>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              ID：{data.runtimeMiddlewareId ?? "unknown"}
            </p>
          </div>
          {(data.runtimeMiddlewareFields ?? []).length === 0 ? (
            <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-4 text-sm leading-6 text-slate-400">
              此中间件暂无可配置字段。
            </p>
          ) : (
            <>
              <p className="text-xs font-semibold text-slate-300">中间件配置</p>
              {(data.runtimeMiddlewareFields ?? []).map((field) => (
                <Field
                  key={field.name}
                  label={`${field.label}${field.required ? " *" : ""}`}
                >
                  {field.type === "text" && field.name === "clientHostId" ? (
                    <select
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(field.name, event.target.value)
                      }
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    >
                      <option className="bg-slate-950" value="">
                        {data.runtimeMiddlewareId === "office_automation"
                          ? "选择已配对 Office 宿主"
                          : "选择已配对 Chrome 宿主"}
                      </option>
                      {clientHosts
                        .filter(
                          (host) =>
                            !host.revoked &&
                            (data.runtimeMiddlewareId === "office_automation"
                              ? host.host_type === "office"
                              : (host.host_type ?? "chrome") === "chrome"),
                        )
                        .map((host) => (
                        <option className="bg-slate-950" key={host.host_id} value={host.host_id}>
                          {host.name} · {host.status}
                          {host.host_type === "office"
                            ? host.document_binding?.bound
                              ? ` · ${host.office_app ?? "office"} 已绑定`
                              : " · 文档未绑定"
                            : host.bound_tab?.bound
                              ? " · 已绑定标签页"
                              : " · 未绑定标签页"}
                        </option>
                      ))}
                    </select>
                  ) : null}

                  {field.type === "text" && field.name !== "clientHostId" ? (
                    <input
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "textarea" && field.name === "skill_ids" ? (
                    <div className="space-y-2">
                      <TrustedSkillSelect
                        ariaLabel="添加已安装 Skill"
                        onChange={appendTrustedSkill}
                        placeholder="选择一个可激活 Skill 添加"
                        skills={installedSkills}
                        value=""
                      />
                      <textarea
                        className={`${textInputClass()} min-h-24 resize-none leading-6`}
                        onChange={(event) =>
                          updateRuntimeMiddlewareConfig(
                            field.name,
                            event.target.value,
                          )
                        }
                        placeholder={field.placeholder}
                        rows={field.rows ?? 3}
                        value={runtimeMiddlewareStringValue(
                          runtimeMiddlewareConfig,
                          field,
                        )}
                      />
                      <p className="text-[11px] leading-5 text-slate-500">
                        手工输入仍会在保存和运行时由 Server 重新校验；禁用项不能通过选择器加入。
                      </p>
                    </div>
                  ) : null}

                  {field.type === "textarea" && field.name !== "skill_ids" ? (
                    <textarea
                      className={`${textInputClass()} min-h-24 resize-none leading-6`}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder}
                      rows={field.rows ?? 3}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "boolean" ? (
                    <>
                      <label
                        className={`flex items-start gap-3 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm text-slate-200 ${
                          field.name === "catalog_search" &&
                          skillCatalogApprovalState?.enabled
                            ? "opacity-65"
                            : ""
                        }`}
                      >
                        <input
                          checked={runtimeMiddlewareBooleanValue(
                            runtimeMiddlewareConfig,
                            field,
                          )}
                          className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-brand-300"
                          disabled={
                            field.name === "catalog_search" &&
                            skillCatalogApprovalState?.enabled
                          }
                          onChange={(event) =>
                            updateRuntimeMiddlewareConfig(
                              field.name,
                              event.target.checked,
                            )
                          }
                          type="checkbox"
                        />
                        <span className="leading-6">
                          {field.description ?? field.label}
                          {field.name === "catalog_search" &&
                          skillCatalogApprovalState?.enabled ? (
                            <span className="mt-1 block text-xs text-indigo-200">
                              目录安装已开启，检索会保持启用。
                            </span>
                          ) : null}
                        </span>
                      </label>
                      {field.name === "catalog_install" ? (
                        <div
                          aria-live="polite"
                          className={`mt-2 rounded-lg border px-3 py-2 text-xs leading-5 ${
                            !skillCatalogApprovalState?.enabled
                              ? "border-white/10 bg-white/[0.035] text-slate-400"
                              : skillCatalogApprovalState.covered
                                ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                          }`}
                        >
                          {!skillCatalogApprovalState?.enabled
                            ? "开启后会同时启用目录检索，并自动添加或更新人机审批中间件。每次安装仍需你明确批准。"
                            : skillCatalogApprovalState.covered
                              ? "审批保护已就绪：skill_install 执行前会暂停，等待你批准。Skill 会全局安装，但只授权当前运行使用。"
                              : skillCatalogApprovalState.approvalNodeId
                                ? "已添加并配置人机审批。请将本节点与“人机审批”都通过紫色端口绑定到同一个 workflow_agent。"
                                : "暂未能自动添加人机审批，请等待中间件注册表加载后重试。目录安装在审批保护就绪前不会通过校验。"}
                        </div>
                      ) : null}
                    </>
                  ) : null}

                  {field.type === "number" ? (
                    <input
                      className={textInputClass()}
                      max={field.maxValue ?? field.max_value}
                      min={field.minValue ?? field.min_value}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value === ""
                            ? ""
                            : Number(event.target.value),
                        )
                      }
                      placeholder={field.placeholder}
                      type="number"
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "select" ? (
                    <select
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    >
                      <option className="bg-slate-950" value="">
                        请选择
                      </option>
                      {(field.options ?? []).map((option) => (
                        <option
                          className="bg-slate-950"
                          key={option}
                          value={option}
                        >
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : null}

                  {field.type === "json" ? (
                    <textarea
                      className={`${textInputClass()} min-h-28 resize-none font-mono text-xs leading-5`}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder ?? '{"key":"value"}'}
                      rows={field.rows ?? 4}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.description && field.type !== "boolean" ? (
                    <p className="mt-2 text-xs leading-5 text-slate-500">
                      {field.description}
                    </p>
                  ) : null}
                </Field>
              ))}
            </>
          )}
        </div>
      ) : null}

      {data.kind === "output" ? (
        <Field label="最终输出变量">
          <input
            className={textInputClass()}
            onChange={(event) => update({ outputVariable: event.target.value })}
            value={data.outputVariable ?? ""}
          />
        </Field>
      ) : null}
    </div>
  );
}

interface WorkflowCanvasProps {
  workflowId: string;
  initialDefinition?: WorkflowDefinition;
  onSave?: (definition: WorkflowDefinition) => Promise<void> | void;
  saveLabel?: string;
}

type WorkflowWorkspaceTab = "config" | "run";

function WorkflowCanvas({
  workflowId,
  initialDefinition: controlledDefinition,
  onSave,
  saveLabel = "保存草稿",
}: WorkflowCanvasProps) {
  const loadedDefinition = useMemo(
    () => loadDefinition(workflowId, controlledDefinition),
    [controlledDefinition, workflowId],
  );
  const [title, setTitle] = useState(loadedDefinition.title);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNode>(
    loadedDefinition.nodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<WorkflowEdge>(
    loadedDefinition.edges,
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isConverting, setIsConverting] = useState(false);
  const [isNodePaletteOpen, setIsNodePaletteOpen] = useState(false);
  const [workspaceTab, setWorkspaceTab] =
    useState<WorkflowWorkspaceTab>("config");
  const [runtimeMiddlewareRegistry, setRuntimeMiddlewareRegistry] = useState<
    RuntimeMiddlewareNode[]
  >([]);
  const { screenToFlowPosition } = useReactFlow();
  const navigate = useNavigate();

  useEffect(() => {
    let isMounted = true;
    fetchRuntimeMiddlewareNodes()
      .then((registryNodes) => {
        if (!isMounted) return;
        setRuntimeMiddlewareRegistry(registryNodes);
        setNodes((currentNodes) =>
          reconcileRuntimeMiddlewareNodes(currentNodes, registryNodes),
        );
      })
      .catch((error) => {
        console.error("Failed to refresh existing middleware nodes:", error);
      });
    return () => {
      isMounted = false;
    };
  }, [setNodes]);

  const humanApprovalDefinition = useMemo(
    () =>
      runtimeMiddlewareRegistry.find(
        (definition) =>
          definition.id === "human_in_the_loop" && definition.enabled,
      ),
    [runtimeMiddlewareRegistry],
  );

  useEffect(() => {
    if (!humanApprovalDefinition) return;
    const reconciled = reconcileSkillCatalogApprovals(
      nodes,
      edges,
      humanApprovalDefinition,
    );
    if (reconciled.nodes !== nodes) setNodes(reconciled.nodes);
    if (reconciled.edges !== edges) setEdges(reconciled.edges);
  }, [edges, humanApprovalDefinition, nodes, setEdges, setNodes]);

  const definition = useMemo<WorkflowDefinition>(
    () => ({
      id: workflowId,
      title,
      nodes,
      edges,
      updatedAt: new Date().toISOString(),
    }),
    [edges, nodes, title, workflowId],
  );

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  const renderedEdges = useMemo(
    () =>
      edges.map((edge) =>
        edge.id === selectedEdgeId
          ? {
              ...edge,
              className: `${edge.className ?? ""} modelmirror-workflow-edge-selected`.trim(),
              style: {
                ...edge.style,
                stroke: "#fb923c",
                strokeWidth: 3,
              },
            }
          : edge,
      ),
    [edges, selectedEdgeId],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const sourceNode = nodes.find((node) => node.id === connection.source);
      const targetNode = nodes.find((node) => node.id === connection.target);
      const middlewareBinding = connection.targetHandle === "middleware";
      const resourceBinding =
        connection.targetHandle === "expert" ||
        connection.targetHandle === "knowledge" ||
        connection.targetHandle === "toolset" ||
        connection.targetHandle === "plugin";
      if (resourceBinding) {
        const expected =
          connection.targetHandle === "expert"
            ? {
                sourceKind: "external_xpert",
                sourceHandle: "expert-binding",
                color: "#93c5fd",
              }
            : connection.targetHandle === "knowledge"
              ? {
                sourceKind: "knowledge_base",
                sourceHandle: "knowledge-binding",
                color: "#5eead4",
                }
              : connection.targetHandle === "toolset"
                ? {
                  sourceKind: "toolset_resource",
                  sourceHandle: "toolset-binding",
                  color: "#fcd34d",
                  }
                : {
                    sourceKind: "plugin_resource",
                    sourceHandle: "plugin-binding",
                    color: "#c4b5fd",
                  };
        if (
          connection.sourceHandle !== expected.sourceHandle ||
          sourceNode?.data.kind !== expected.sourceKind ||
          targetNode?.data.kind !== "workflow_agent"
        ) {
          setSaveNotice("资源节点必须连接到 workflow_agent 对应的资源入口。");
          return;
        }
        if (edges.some((edge) => edge.source === connection.source)) {
          setSaveNotice("一个资源节点只能绑定一个工作流智能体。");
          return;
        }
        setEdges((currentEdges) =>
          addEdge(
            {
              ...connection,
              animated: true,
              className:
                "modelmirror-workflow-edge modelmirror-resource-binding-edge",
              style: {
                stroke: expected.color,
                strokeDasharray: "7 5",
                strokeWidth: 2,
              },
            },
            currentEdges,
          ),
        );
        if (targetNode.data.toolMode !== "mcp_tools") {
          setNodes((currentNodes) =>
            currentNodes.map((item) =>
              item.id === targetNode.id
                ? {
                    ...item,
                    data: { ...item.data, toolMode: "mcp_tools" },
                  }
                : item,
            ),
          );
        }
        setSaveNotice("");
        return;
      }
      if (
        ["expert-binding", "knowledge-binding", "toolset-binding", "plugin-binding"].includes(
          connection.sourceHandle ?? "",
        ) ||
        sourceNode?.data.kind === "external_xpert" ||
        sourceNode?.data.kind === "knowledge_base" ||
        sourceNode?.data.kind === "toolset_resource" ||
        sourceNode?.data.kind === "plugin_resource"
      ) {
        setSaveNotice("资源节点只能通过专用端口绑定到 workflow_agent。");
        return;
      }
      if (middlewareBinding) {
        if (
          connection.sourceHandle !== "middleware-binding" ||
          sourceNode?.data.kind !== "runtime_middleware" ||
          targetNode?.data.kind !== "workflow_agent"
        ) {
          setSaveNotice("中间件绑定必须从 runtime_middleware 的紫色端口连接到 workflow_agent。")
          return;
        }
        if (edges.some((edge) => edge.source === connection.source)) {
          setSaveNotice("一个中间件节点只能绑定一个 Agent，且不能同时连接控制流。")
          return;
        }
      } else if (connection.sourceHandle === "middleware-binding") {
        setSaveNotice("紫色中间件端口只能连接 workflow_agent 的 middleware 入口。")
        return;
      } else if (
        sourceNode?.data.kind === "runtime_middleware" &&
        edges.some(
          (edge) =>
            edge.source === connection.source && edge.targetHandle === "middleware",
        )
      ) {
        setSaveNotice("已绑定 Agent 的中间件不能同时连接控制流。")
        return;
      }
      setEdges((currentEdges) =>
        addEdge(
          {
            ...connection,
            animated: true,
            className: middlewareBinding
              ? "modelmirror-workflow-edge modelmirror-middleware-binding-edge"
              : "modelmirror-workflow-edge",
            style: middlewareBinding
              ? { stroke: "#a5b4fc", strokeDasharray: "7 5", strokeWidth: 2 }
              : undefined,
          },
          currentEdges,
        ),
      );
      setSaveNotice("");
    },
    [edges, nodes, setEdges],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      onNodesChange(changes);
    },
    [onNodesChange],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WorkflowEdge>[]) => {
      onEdgesChange(changes);
      if (
        selectedEdgeId &&
        changes.some((change) => change.type === "remove" && change.id === selectedEdgeId)
      ) {
        setSelectedEdgeId(null);
      }
    },
    [onEdgesChange, selectedEdgeId],
  );

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    setEdges((currentEdges) =>
      currentEdges.filter((edge) => edge.id !== selectedEdgeId),
    );
    setSelectedEdgeId(null);
  }, [selectedEdgeId, setEdges]);

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((currentNodes) =>
      currentNodes.filter((node) => node.id !== selectedNodeId),
    );
    setEdges((currentEdges) =>
      currentEdges.filter(
        (edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId,
      ),
    );
    setSelectedNodeId(null);
  }, [selectedNodeId, setEdges, setNodes]);

  function updateNodeData(nodeId: string, patch: Partial<WorkflowNodeData>) {
    setNodes((currentNodes) =>
      currentNodes.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                ...patch,
              },
            }
          : node,
      ),
    );
  }

  function updateRuntimeMiddlewareConfig(
    nodeId: string,
    fieldName: string,
    value: unknown,
  ) {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) return node;
        const existingConfig = isRecord(node.data.runtimeMiddlewareConfig)
          ? node.data.runtimeMiddlewareConfig
          : {};
        const enablesCatalogInstall =
          node.data.runtimeMiddlewareId === "skills_runtime" &&
          fieldName === "catalog_install" &&
          value === true;
        return {
          ...node,
          data: {
            ...node.data,
            runtimeMiddlewareConfig: {
              ...existingConfig,
              ...(enablesCatalogInstall ? { catalog_search: true } : {}),
              [fieldName]: value,
            },
          },
        };
      }),
    );
    if (fieldName === "catalog_install" && value === true) {
      setSaveNotice("已自动配置 skill_install 人工审批");
      window.setTimeout(() => setSaveNotice(""), 2600);
    }
  }

  async function saveWorkflow() {
    const savedDefinition = {
      ...definition,
      updatedAt: new Date().toISOString(),
    };
    setIsSaving(true);
    try {
      if (onSave) {
        await onSave(savedDefinition);
        setSaveNotice("智能体草稿已保存");
      } else {
        saveStoredWorkflow(savedDefinition);
        setSaveNotice("已保存到本地草稿箱");
      }
    } catch (error) {
      setSaveNotice(
        error instanceof Error ? error.message : "草稿保存失败，请稍后重试",
      );
    } finally {
      setIsSaving(false);
    }
    window.setTimeout(() => setSaveNotice(""), 1800);
  }

  async function convertToXpertDraft() {
    if (onSave || isConverting) return;
    const currentDefinition = {
      ...definition,
      updatedAt: new Date().toISOString(),
    };
    setIsConverting(true);
    try {
      const inputVariable =
        currentDefinition.nodes
          .find((node) => node.data.kind === "input")
          ?.data.variableName?.trim() || "user_input";
      const outputVariable =
        currentDefinition.nodes
          .find((node) => node.data.kind === "output")
          ?.data.outputVariable?.trim() || "agent_output";
      const created = await createXpert({
        name: title.trim() || "未命名智能体",
        description: "由经典工作流草稿转换。",
        tags: ["workflow-import"],
      });
      await updateXpert(created.id, {
        draft: {
          ...created.draft,
          workflow: toXpertDraftWorkflow(currentDefinition),
          input_variable: inputVariable,
          output_variable: outputVariable,
        },
      });
      saveStoredWorkflow(currentDefinition);
      navigate(`/agents/studio/${created.id}`);
    } catch (error) {
      setSaveNotice(
        error instanceof Error ? error.message : "转换智能体草稿失败，请稍后重试",
      );
      window.setTimeout(() => setSaveNotice(""), 2600);
    } finally {
      setIsConverting(false);
    }
  }

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const position = screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });

    const runtimeMiddlewareRaw = event.dataTransfer.getData(
      "application/modelmirror-runtime-middleware",
    );
    const runtimeMiddlewarePayload = runtimeMiddlewareRaw
      ? parseRuntimeMiddlewarePayload(runtimeMiddlewareRaw)
      : null;
    if (runtimeMiddlewarePayload) {
      const nextNode = createNode(
        "runtime_middleware",
        position.x,
        position.y,
        runtimeMiddlewarePayload,
      );
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      return;
    }

    const rawKind = event.dataTransfer.getData("application/modelmirror-node");
    const fallbackPayload = parseRuntimeMiddlewarePayload(rawKind);
    if (fallbackPayload) {
      const nextNode = createNode(
        "runtime_middleware",
        position.x,
        position.y,
        fallbackPayload,
      );
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      return;
    }

    const kind = rawKind as WorkflowNodeKind;
    if (!kind) return;

    try {
      const nextNode = createNode(kind, position.x, position.y);
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      setSaveNotice("");
    } catch (error) {
      setSaveNotice(
        error instanceof Error ? error.message : "无法创建该工作流节点。",
      );
    }
  }

  useEffect(() => {
    setSelectedNodeId((current) =>
      current && nodes.some((node) => node.id === current) ? current : null,
    );
  }, [nodes]);

  useEffect(() => {
    function handleDeleteKey(event: KeyboardEvent) {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT"
      ) {
        return;
      }
      if (selectedNodeId) {
        event.preventDefault();
        deleteSelectedNode();
      } else if (selectedEdgeId) {
        event.preventDefault();
        deleteSelectedEdge();
      }
    }
    window.addEventListener("keydown", handleDeleteKey);
    return () => window.removeEventListener("keydown", handleDeleteKey);
  }, [deleteSelectedEdge, deleteSelectedNode, selectedEdgeId, selectedNodeId]);

  return (
    <div className="grid min-h-[calc(100vh-8rem)] gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
      <aside className="hidden">
        <p className="text-sm font-semibold text-white">工位库</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">
          拖拽节点到画布，像安排招聘会工位一样搭建 AI 流水线。
        </p>
        <div className="mt-4">
          <NodePalette />
        </div>
      </aside>

      <section className="relative min-w-0 rounded-lg border border-white/10 bg-ink-950/80 shadow-prism">
        <div className="flex flex-col gap-3 border-b border-white/10 bg-surface-900/90 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <input
              className="w-full bg-transparent text-xl font-semibold text-white outline-none"
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
            <p className="mt-1 text-sm text-slate-400">
              线性 + 条件分支 MVP，支持本地保存和后端流式试运行。
            </p>
          </div>
          <div className="relative flex flex-wrap items-center gap-2">
            <button
              className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-4 py-2 text-sm font-semibold text-emerald-100 transition hover:border-emerald-200/45 hover:bg-emerald-300/20"
              onClick={() => navigate("/data-tables")}
              type="button"
            >
              数据表
            </button>
            <button
              className="rounded-full border border-hire-300/30 bg-hire-300/10 px-4 py-2 text-sm font-semibold text-hire-100 transition hover:border-hire-200/50 hover:bg-hire-300/20"
              onClick={() => setIsNodePaletteOpen((current) => !current)}
              type="button"
            >
              节点库
            </button>
            {isNodePaletteOpen ? (
              <div className="absolute right-0 top-full z-30 mt-3 max-h-[72vh] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-white/10 bg-surface-900/95 p-4 shadow-2xl shadow-ink-950/50 backdrop-blur">
                <NodePalette />
              </div>
            ) : null}
            {saveNotice ? (
              <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
                {saveNotice}
              </span>
            ) : null}
            {!onSave ? (
              <button
                className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/20 disabled:opacity-50"
                disabled={isConverting}
                onClick={() => void convertToXpertDraft()}
                type="button"
              >
                {isConverting ? "转换中..." : "转为智能体草稿"}
              </button>
            ) : null}
            <button
              className="rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-sm font-semibold text-slate-100 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
              disabled={isSaving}
              onClick={() => void saveWorkflow()}
              type="button"
            >
              {isSaving ? "保存中..." : saveLabel}
            </button>
          </div>
        </div>

        <div
          className="h-[640px] min-h-[520px] overflow-hidden rounded-b-lg"
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={onDrop}
        >
          <ReactFlow
            edges={renderedEdges}
            fitView
            nodeTypes={nodeTypes}
            nodes={nodes}
            onConnect={handleConnect}
            onEdgeClick={(event, edge) => {
              event.stopPropagation();
              setSelectedNodeId(null);
              setSelectedEdgeId(edge.id);
            }}
            onEdgesChange={handleEdgesChange}
            onNodeClick={(_, node) => {
              setSelectedEdgeId(null);
              setSelectedNodeId(node.id);
            }}
            onNodesChange={handleNodesChange}
            onPaneClick={() => {
              setSelectedEdgeId(null);
              setSelectedNodeId(null);
            }}
          >
            <Background
              color="rgba(253, 186, 116, 0.22)"
              gap={24}
              variant={BackgroundVariant.Dots}
            />
            <Controls className="modelmirror-flow-controls" />
            {selectedEdgeId ? (
              <Panel position="bottom-left">
                <button
                  className="mb-16 rounded-full border border-rose-300/35 bg-rose-400/15 px-3 py-1.5 text-xs font-semibold text-rose-100 shadow-lg shadow-rose-950/30 transition hover:bg-rose-400/25"
                  onClick={deleteSelectedEdge}
                  type="button"
                >
                  × 删除连线
                </button>
              </Panel>
            ) : null}
            {selectedNodeId ? (
              <Panel position="bottom-left">
                <button
                  className="mb-16 rounded-full border border-rose-300/35 bg-rose-400/15 px-3 py-1.5 text-xs font-semibold text-rose-100 shadow-lg shadow-rose-950/30 transition hover:bg-rose-400/25"
                  onClick={deleteSelectedNode}
                  type="button"
                >
                  × 删除节点
                </button>
              </Panel>
            ) : null}
            <MiniMap
              maskColor="rgba(6, 9, 22, 0.68)"
              nodeColor={() => "rgba(251, 146, 60, 0.9)"}
              pannable
              zoomable
            />
          </ReactFlow>
        </div>
      </section>

      <aside className="surface-panel flex min-h-[520px] flex-col rounded-lg p-4">
        <div className="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
          <div>
            <p className="text-sm font-semibold text-white">工作台</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              在同一侧栏内切换节点配置与试运行，减少页面纵向滚动。
            </p>
          </div>
          <div className="flex shrink-0 rounded-full border border-white/10 bg-white/[0.04] p-1">
            <button
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                workspaceTab === "config"
                  ? "bg-hire-300 text-ink-950"
                  : "text-slate-400 hover:text-slate-100"
              }`}
              onClick={() => setWorkspaceTab("config")}
              type="button"
            >
              配置
            </button>
            <button
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                workspaceTab === "run"
                  ? "bg-hire-300 text-ink-950"
                  : "text-slate-400 hover:text-slate-100"
              }`}
              onClick={() => setWorkspaceTab("run")}
              type="button"
            >
              运行
            </button>
          </div>
        </div>

        <section
          className={
            workspaceTab === "config"
              ? "min-h-0 flex-1 overflow-y-auto pt-4"
              : "hidden"
          }
        >
          <p className="text-sm font-semibold text-white">工位配置</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            节点配置会立即写入画布，下次运行直接生效。
          </p>
          <div className="mt-4">
            <NodeConfig
              edges={edges}
              node={selectedNode}
              nodes={nodes}
              onChange={updateNodeData}
              onRuntimeMiddlewareConfigChange={updateRuntimeMiddlewareConfig}
              onSelectNode={setSelectedNodeId}
            />
          </div>
        </section>

        <div
          className={
            workspaceTab === "run"
              ? "min-h-0 flex flex-1 flex-col pt-4"
              : "hidden"
          }
        >
          <WorkflowRun
            definition={definition}
            embedded
            onRunStart={() => setWorkspaceTab("run")}
          />
        </div>
      </aside>
    </div>
  );
}

export default function WorkflowEditor(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvas {...props} />
    </ReactFlowProvider>
  );
}
