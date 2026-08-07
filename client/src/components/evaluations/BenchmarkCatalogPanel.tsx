import { useEffect, useState } from "react";
import {
  BookOpenCheck,
  CheckCircle2,
  Languages,
  Layers3,
  LoaderCircle,
  Plus,
  ShieldCheck,
} from "lucide-react";

interface BenchmarkManifestSummary {
  pack_id: string;
  version: number;
  kind: "agent_response" | "knowledge_retrieval";
  name: string;
  description: string;
  locales: string[];
  coverage: string[];
  difficulty: "basic" | "intermediate" | "advanced" | "mixed";
  metric_policy: {
    core?: string[];
    llm_judge?: string;
  };
  source: string;
  license: string;
  case_count: number;
  checksum: string;
}

interface InstantiatedDataset {
  dataset_id: string;
  name: string;
  published_version: number;
  case_count: number;
}

interface BenchmarkCatalogPanelProps {
  onInstantiated: (dataset: InstantiatedDataset) => Promise<void> | void;
}

function readError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

function difficultyLabel(value: BenchmarkManifestSummary["difficulty"]) {
  if (value === "basic") return "基础";
  if (value === "intermediate") return "进阶";
  if (value === "advanced") return "高级";
  return "混合";
}

export default function BenchmarkCatalogPanel({
  onInstantiated,
}: BenchmarkCatalogPanelProps) {
  const [packs, setPacks] = useState<BenchmarkManifestSummary[]>([]);
  const [busyPackId, setBusyPackId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadCatalog() {
      setError("");
      try {
        const response = await fetch("/api/benchmarks/catalog?kind=agent_response");
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(readError(payload, `目录加载失败：${response.status}`));
        }
        if (!cancelled) {
          setPacks((payload as { items?: BenchmarkManifestSummary[] })?.items ?? []);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "标准基准目录暂不可用。");
        }
      }
    }
    void loadCatalog();
    return () => {
      cancelled = true;
    };
  }, []);

  async function instantiate(pack: BenchmarkManifestSummary) {
    setBusyPackId(pack.pack_id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(
        `/api/benchmarks/catalog/${encodeURIComponent(pack.pack_id)}/instantiate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(readError(payload, `实例化失败：${response.status}`));
      }
      const dataset = payload as InstantiatedDataset;
      setNotice(`${pack.name} 已添加并发布为数据集 v${dataset.published_version}。`);
      await onInstantiated(dataset);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "添加标准基准失败。");
    } finally {
      setBusyPackId("");
    }
  }

  return (
    <section className="min-w-0">
      <div className="flex flex-col gap-3 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <BookOpenCheck className="h-4 w-4 text-cyan-200" />
            标准 Benchmark
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">
            ModelMirror 自有中英双语合成基准。实例化会创建可编辑数据集，并自动发布与目录 Pack 完全一致的 v1。
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-slate-500">
          <ShieldCheck className="h-4 w-4 text-emerald-300" />
          核心门禁仅使用确定性指标
        </div>
      </div>

      {error || notice ? (
        <div
          className={`mt-4 rounded-md border px-3 py-2 text-sm ${
            error
              ? "border-rose-300/25 bg-rose-300/10 text-rose-100"
              : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
          }`}
        >
          {error || notice}
        </div>
      ) : null}

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        {packs.map((pack) => (
          <article
            className="flex min-h-[260px] flex-col rounded-md border border-white/10 bg-white/[0.025] p-5"
            key={pack.pack_id}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-white">{pack.name}</h2>
                <p className="mt-2 text-xs leading-5 text-slate-400">{pack.description}</p>
              </div>
              <span className="shrink-0 rounded border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 text-[10px] font-semibold text-cyan-100">
                v{pack.version}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2 text-[11px]">
              <div className="rounded bg-white/[0.035] p-2 text-slate-500">
                <Layers3 className="mb-1 h-3.5 w-3.5 text-slate-300" />
                <strong className="block text-slate-200">{pack.case_count} 条</strong>
                用例
              </div>
              <div className="rounded bg-white/[0.035] p-2 text-slate-500">
                <Languages className="mb-1 h-3.5 w-3.5 text-slate-300" />
                <strong className="block text-slate-200">中 / EN</strong>
                语言
              </div>
              <div className="rounded bg-white/[0.035] p-2 text-slate-500">
                <CheckCircle2 className="mb-1 h-3.5 w-3.5 text-slate-300" />
                <strong className="block text-slate-200">{difficultyLabel(pack.difficulty)}</strong>
                难度
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-1.5">
              {pack.coverage.map((item) => (
                <span
                  className="rounded border border-white/10 bg-black/10 px-2 py-1 text-[10px] text-slate-400"
                  key={item}
                >
                  {item}
                </span>
              ))}
            </div>

            <div className="mt-auto flex items-end justify-between gap-4 pt-5">
              <div className="min-w-0 text-[10px] leading-4 text-slate-500">
                <p>{(pack.metric_policy.core ?? []).join(" · ")}</p>
                <p className="truncate" title={pack.source}>{pack.pack_id} · {pack.license}</p>
                <p className="truncate" title={pack.checksum}>SHA-256 {pack.checksum.slice(0, 12)}</p>
              </div>
              <button
                className="inline-flex shrink-0 items-center gap-2 rounded-md bg-cyan-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:cursor-wait disabled:opacity-50"
                disabled={Boolean(busyPackId)}
                onClick={() => void instantiate(pack)}
                type="button"
              >
                {busyPackId === pack.pack_id ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plus className="h-3.5 w-3.5" />
                )}
                添加到工作区
              </button>
            </div>
          </article>
        ))}
      </div>

      {!error && packs.length === 0 ? (
        <div className="grid min-h-[320px] place-items-center text-sm text-slate-500">
          <LoaderCircle className="h-5 w-5 animate-spin" />
        </div>
      ) : null}
    </section>
  );
}
