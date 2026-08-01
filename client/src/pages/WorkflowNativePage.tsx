import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import { DEFAULT_CHAT_MODEL_ID } from "../context/ModelPreferenceContext";
import type {
  NativeValidateResponse,
  NativeWorkflowDefinition,
} from "../types/workflow-native";

function createNativeSample(withCycle = false): NativeWorkflowDefinition {
  const now = new Date().toISOString();
  return {
    id: "native-draft",
    title: withCycle ? "Native cycle sample" : "Native linear sample",
    version: "native-draft",
    source: "workflow-native",
    updatedAt: now,
    nodes: [
      {
        id: "input",
        type: "workflowNode",
        position: { x: 0, y: 80 },
        data: {
          kind: "input",
          title: "Input",
          description: "Collect the first variable.",
          variableName: "user_input",
        },
      },
      {
        id: "llm",
        type: "workflowNode",
        position: { x: 320, y: 80 },
        data: {
          kind: "llm",
          title: "LLM",
          description: "Draft a response from the input.",
          modelId: DEFAULT_CHAT_MODEL_ID,
          prompt: "请基于 {{user_input}} 给出清晰回答。",
          outputVariable: "llm_output",
        },
      },
      {
        id: "output",
        type: "workflowNode",
        position: { x: 640, y: 80 },
        data: {
          kind: "output",
          title: "Output",
          description: "Return the final variable.",
          outputVariable: "llm_output",
        },
      },
    ],
    edges: [
      {
        id: "input-llm",
        source: "input",
        target: "llm",
      },
      {
        id: "llm-output",
        source: "llm",
        target: "output",
      },
      ...(withCycle
        ? [
            {
              id: "output-input",
              source: "output",
              target: "input",
            },
          ]
        : []),
    ],
  };
}

async function readApiError(response: Response) {
  try {
    const data = (await response.json()) as { detail?: string; error?: string };
    return data.detail ?? data.error ?? `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

export default function WorkflowNativePage() {
  const { id } = useParams();
  const [workflow, setWorkflow] = useState<NativeWorkflowDefinition>(() =>
    createNativeSample(false),
  );
  const [result, setResult] = useState<NativeValidateResponse | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "模镜 - 自研工作流实验入口";
  }, []);

  async function validateWorkflow() {
    setIsValidating(true);
    setError("");
    try {
      const response = await fetch("/api/workflow-native/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const data = (await response.json()) as NativeValidateResponse;
      setResult(data);
    } catch (validateError) {
      setError(
        validateError instanceof Error ? validateError.message : "工作流校验失败",
      );
    } finally {
      setIsValidating(false);
    }
  }

  function useHealthySample() {
    setWorkflow(createNativeSample(false));
    setResult(null);
    setError("");
  }

  function useCycleSample() {
    setWorkflow(createNativeSample(true));
    setResult(null);
    setError("");
  }

  return (
    <PageContainer
      activeResource="agents"
      maxWidthClassName="max-w-[1480px]"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">workflow-native</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            这是自研工作流的隔离实验入口。稳定工作流由经典自研画布 /workflow 提供。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">当前草稿</p>
            <p className="mt-1 break-all text-sm font-semibold text-hire-100">
              {id ?? workflow.id}
            </p>
          </div>
          <Link
            className="mt-4 inline-flex rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
            to="/workflow"
          >
            返回经典工作流
          </Link>
        </div>
      }
    >
      <header className="mb-6 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.74),rgba(6,9,22,0.92)_52%,rgba(8,51,68,0.48))] p-6 shadow-prism">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-semibold text-hire-100">实验入口，非 iframe</p>
            <h1 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">
              自研工作流 Native 校验台
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
              本页只验证工作流图结构，不执行 LLM、Tool 或 RAG。它和经典画布并行存在，方便逐步增强而不影响主流程。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              className="rounded-full border border-white/10 bg-white/[0.045] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
              to="/workflow/classic"
            >
              打开经典画布
            </Link>
            <Link
              className="rounded-full border border-white/10 bg-white/[0.045] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
              to="/workflow"
            >
              打开经典工作流
            </Link>
          </div>
        </div>
      </header>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="rounded-lg border border-white/10 bg-white/[0.045] p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-white">{workflow.title}</p>
              <p className="mt-1 text-xs text-slate-400">
                {workflow.nodes.length} 个节点，{workflow.edges.length} 条连线
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                className="rounded-full border border-white/10 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100"
                onClick={useHealthySample}
                type="button"
              >
                使用合法样例
              </button>
              <button
                className="rounded-full border border-rose-300/30 bg-rose-300/10 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/20"
                onClick={useCycleSample}
                type="button"
              >
                注入错误样例
              </button>
              <button
                className="rounded-full bg-hire-300 px-4 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                disabled={isValidating}
                onClick={() => void validateWorkflow()}
                type="button"
              >
                {isValidating ? "校验中..." : "校验工作流"}
              </button>
            </div>
          </div>

          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {workflow.nodes.map((node) => (
              <div
                className="rounded-lg border border-white/10 bg-ink-950/60 p-4"
                key={node.id}
              >
                <p className="text-xs font-semibold text-hire-100">
                  {String(node.data.kind)}
                </p>
                <p className="mt-2 font-semibold text-white">
                  {String(node.data.title)}
                </p>
                <p className="mt-2 min-h-12 text-xs leading-5 text-slate-400">
                  {String(node.data.description)}
                </p>
              </div>
            ))}
          </div>

          <pre className="mt-5 max-h-[420px] overflow-auto rounded-lg border border-white/10 bg-ink-950/80 p-4 text-xs leading-5 text-slate-200">
            {JSON.stringify(workflow, null, 2)}
          </pre>
        </div>

        <aside className="rounded-lg border border-white/10 bg-surface-900/80 p-5 shadow-prism">
          <p className="text-sm font-semibold text-white">校验结果</p>
          {error ? (
            <div className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-100">
              {error}
            </div>
          ) : null}
          {result ? (
            <div className="mt-4 space-y-4">
              <div
                className={`rounded-lg border p-4 ${
                  result.valid
                    ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-50"
                    : "border-rose-300/25 bg-rose-300/10 text-rose-50"
                }`}
              >
                <p className="text-sm font-semibold">
                  {result.valid ? "valid=true" : "valid=false"}
                </p>
                <p className="mt-1 text-xs opacity-80">
                  {result.node_count} nodes / {result.edge_count} edges
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-400">拓扑顺序</p>
                <p className="mt-2 break-all rounded-lg border border-white/10 bg-white/[0.045] p-3 text-sm text-slate-200">
                  {result.order.length > 0 ? result.order.join(" -> ") : "暂无"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-400">Issues</p>
                <div className="mt-2 space-y-2">
                  {result.issues.length === 0 ? (
                    <p className="rounded-lg border border-white/10 bg-white/[0.045] p-3 text-sm text-slate-300">
                      没有发现结构问题。
                    </p>
                  ) : (
                    result.issues.map((issue, index) => (
                      <div
                        className="rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-50"
                        key={`${issue.code}-${index}`}
                      >
                        <p className="font-semibold">{issue.code}</p>
                        <p className="mt-1 text-xs leading-5">{issue.message}</p>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="mt-4 text-sm leading-6 text-slate-400">
              点击“校验工作流”查看 native validate 返回的 order 与 issues。
            </p>
          )}
        </aside>
      </section>
    </PageContainer>
  );
}

