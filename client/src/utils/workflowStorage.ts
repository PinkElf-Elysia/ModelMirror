import { type WorkflowDefinition } from "../types/workflow";

export const workflowStoragePrefix = "modelmirror-workflow:";

const LEGACY_STARTER_MODEL_IDS = new Set([
  "openai/gpt-4o-mini",
  "openai/gpt-5.6-sol",
  "deepseek/deepseek-chat",
  "deepseek/deepseek-chat-v3-0324",
  "deepseek/deepseek-v3.2",
]);

function hasExactData(
  actual: Record<string, unknown>,
  expected: Record<string, unknown>,
) {
  const actualKeys = Object.keys(actual).sort();
  const expectedKeys = Object.keys(expected).sort();
  return (
    actualKeys.length === expectedKeys.length &&
    actualKeys.every((key, index) => key === expectedKeys[index]) &&
    expectedKeys.every((key) => actual[key] === expected[key])
  );
}

export function isLegacyStarterWorkflow(definition: WorkflowDefinition) {
  if (
    definition.title !== "新建 AI 流水线" ||
    definition.nodes.length !== 3 ||
    definition.edges.length !== 2
  ) {
    return false;
  }

  const [inputNode, llmNode, outputNode] = definition.nodes;
  const [inputEdge, outputEdge] = definition.edges;
  if (!inputNode || !llmNode || !outputNode || !inputEdge || !outputEdge) {
    return false;
  }

  const matchesLayout =
    inputNode.id === "input-1" &&
    inputNode.type === "workflowNode" &&
    inputNode.position.x === 0 &&
    inputNode.position.y === 80 &&
    llmNode.id === "llm-1" &&
    llmNode.type === "workflowNode" &&
    llmNode.position.x === 340 &&
    llmNode.position.y === 80 &&
    outputNode.id === "output-1" &&
    outputNode.type === "workflowNode" &&
    outputNode.position.x === 700 &&
    outputNode.position.y === 80;
  const matchesEdges =
    inputEdge.id === "edge-input-llm" &&
    inputEdge.source === "input-1" &&
    inputEdge.target === "llm-1" &&
    outputEdge.id === "edge-llm-output" &&
    outputEdge.source === "llm-1" &&
    outputEdge.target === "output-1";
  const matchesInput = hasExactData(inputNode.data, {
    kind: "input",
    title: "接待处输入",
    description: "收集用户给流水线的原始任务。",
    variableName: "user_input",
  });
  const matchesLlm =
    typeof llmNode.data.modelId === "string" &&
    LEGACY_STARTER_MODEL_IDS.has(llmNode.data.modelId) &&
    hasExactData(llmNode.data, {
      kind: "llm",
      title: "模型工位",
      description: "调用模型，把上游变量加工成新结果。",
      modelId: llmNode.data.modelId,
      prompt: "请基于以下输入给出清晰回答：\n\n{{user_input}}",
      outputVariable: "llm_output",
    });
  const matchesOutput = hasExactData(outputNode.data, {
    kind: "output",
    title: "最终交付",
    description: "把指定变量作为工作流结果交付。",
    outputVariable: "llm_output",
  });

  return matchesLayout && matchesEdges && matchesInput && matchesLlm && matchesOutput;
}

export function createWorkflowStorageKey(workflowId: string) {
  return `${workflowStoragePrefix}${workflowId}`;
}

export function readStoredWorkflow(workflowId: string): WorkflowDefinition | null {
  if (typeof window === "undefined") return null;

  const raw = window.localStorage.getItem(createWorkflowStorageKey(workflowId));
  if (!raw) return null;

  try {
    return JSON.parse(raw) as WorkflowDefinition;
  } catch {
    return null;
  }
}

export function saveStoredWorkflow(definition: WorkflowDefinition) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    createWorkflowStorageKey(definition.id),
    JSON.stringify(definition),
  );
}
