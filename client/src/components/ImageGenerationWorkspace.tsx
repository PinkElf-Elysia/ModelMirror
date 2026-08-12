import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { Link } from "react-router-dom";
import type { Model } from "../data/models";
import {
  estimateImageCost,
  GROK_IMAGINE_IMAGE_2_PRICING,
  type ImagePricingItem,
} from "../utils/imageCostEstimate";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const MAX_PROMPT_CHARS = 4_000;
const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;

interface ParameterProfile {
  type: "enum" | "range" | "boolean" | "string";
  values: string[];
  min: number | null;
  max: number | null;
}

interface ImageModelProfile {
  model_id: string;
  display_name: string;
  operation: "analyze_image" | "generate_image";
  invocable: boolean;
  supported_parameters: Record<string, ParameterProfile>;
  pricing: ImagePricingItem[];
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
}

interface ImageCatalogResponse {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: ImageModelProfile[];
}

interface ImageGenerationResponse {
  actual_model: string;
  request_id: string;
  images: Array<{
    data_url: string;
    media_type: string;
    output_bytes: number;
  }>;
  usage: {
    cost_usd: number | null;
    cost_kind: "actual" | "estimated" | "unavailable";
  };
}

interface ImageGenerationWorkspaceProps {
  model: Model;
}

function errorMessage(value: unknown) {
  if (!value || typeof value !== "object") return "图片生成失败，请稍后重试。";
  const payload = value as { detail?: unknown; error?: unknown };
  if (typeof payload.error === "string") return payload.error;
  if (typeof payload.detail === "string") return payload.detail;
  if (payload.detail && typeof payload.detail === "object") {
    const message = (payload.detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return "图片生成失败，请稍后重试。";
}

function extensionFor(mediaType: string) {
  if (mediaType === "image/jpeg") return "jpg";
  if (mediaType === "image/webp") return "webp";
  if (mediaType === "image/svg+xml") return "svg";
  return "png";
}

function formatUsd(value: number) {
  return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
}

export default function ImageGenerationWorkspace({
  model,
}: ImageGenerationWorkspaceProps) {
  const [catalog, setCatalog] = useState<ImageCatalogResponse | null>(null);
  const [prompt, setPrompt] = useState("");
  const [parameters, setParameters] = useState<Record<string, string>>( {} );
  const [references, setReferences] = useState<File[]>([]);
  const [result, setResult] = useState<ImageGenerationResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/multimodal/image/models", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("catalog unavailable");
        return (await response.json()) as ImageCatalogResponse;
      })
      .then((payload) => {
        if (!controller.signal.aborted) setCatalog(payload);
      })
      .catch(() => {
        if (!controller.signal.aborted) setCatalog(null);
      });
    return () => controller.abort();
  }, []);

  const profile = useMemo(
    () =>
      catalog?.profiles.find(
        (item) =>
          item.model_id === model.id &&
          item.operation === "generate_image",
      ) ?? null,
    [catalog, model.id],
  );
  const supported = profile?.supported_parameters ?? {};
  const referenceLimit = Math.max(
    0,
    Number(supported.input_references?.max ?? 0),
  );
  const costEstimate = useMemo(
    () => {
      if (!profile) return null;
      const pricing = profile.pricing?.length
        ? profile.pricing
        : model.id === "x-ai/grok-imagine-image-2.0"
          ? GROK_IMAGINE_IMAGE_2_PRICING
          : [];
      return estimateImageCost(pricing, {
        outputCount: Number(parameters.n || 1),
        referenceCount: references.length,
        resolution: parameters.resolution,
        quality: parameters.quality,
      });
    },
    [
      model.id,
      parameters.n,
      parameters.quality,
      parameters.resolution,
      profile,
      references.length,
    ],
  );
  const costLabel = costEstimate
    ? costEstimate.exact
      ? formatUsd(costEstimate.minUsd)
      : `${formatUsd(costEstimate.minUsd)}–${formatUsd(costEstimate.maxUsd)}`
    : null;

  function selectValue(key: string, value: string) {
    setParameters((current) => ({ ...current, [key]: value }));
  }

  function addReferences(event: ChangeEvent<HTMLInputElement>) {
    const next = Array.from(event.target.files ?? []);
    event.target.value = "";
    setError(null);
    if (next.some((file) => file.size > MAX_REFERENCE_BYTES)) {
      setError("每张参考图需小于 10 MiB。");
      return;
    }
    setReferences((current) =>
      [...current, ...next].slice(0, referenceLimit),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!profile || profile.interaction_status !== "ready") {
      setError("该模型的图片生成能力尚未获得实时确认，请刷新后重试。");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("model_id", model.id);
    form.append("prompt", prompt.trim());
    for (const [key, value] of Object.entries(parameters)) {
      if (value) form.append(key, value);
    }
    for (const file of references) form.append("reference_images", file);
    try {
      const response = await fetch("/api/multimodal/image/generations", {
        method: "POST",
        body: form,
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) throw new Error(errorMessage(payload));
      setResult(payload as ImageGenerationResponse);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const enumControls = [
    ["resolution", "分辨率"],
    ["aspect_ratio", "画面比例"],
    ["quality", "质量"],
    ["output_format", "输出格式"],
    ["background", "背景"],
  ] as const;

  return (
    <div className="min-h-screen bg-ink-950 text-slate-100">
      <header className="border-b border-white/10 bg-ink-950/90 px-4 py-4 backdrop-blur-xl sm:px-6">
        <div className="mx-auto flex max-w-[1680px] items-center justify-between gap-4">
          <BrandLogo compact />
          <ResourceNav activeResource="models" />
        </div>
      </header>
      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-8 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section className="rounded-xl border border-fuchsia-300/20 bg-white/[0.045] p-5 sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-fuchsia-200">图片生成与编辑</p>
              <h1 className="mt-2 text-3xl font-semibold text-white">{model.name}</h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">
                参数只显示实时目录确认支持的选项；生成完成并校验后才会展示结果。
              </p>
            </div>
            <Link className="text-sm text-slate-300 hover:text-white" to="/models">
              返回模型招聘会
            </Link>
          </div>

          <form className="mt-7 space-y-5" onSubmit={submit}>
            <label className="block">
              <span className="text-sm font-semibold text-white">创作描述</span>
              <textarea
                className="mt-2 min-h-36 w-full rounded-lg border border-white/10 bg-ink-950/70 px-4 py-3 text-sm leading-6 outline-none focus:border-fuchsia-300/60"
                maxLength={MAX_PROMPT_CHARS}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="描述主体、场景、构图、光线和风格…"
                value={prompt}
              />
              <span className="mt-1 block text-right text-xs text-slate-500">
                {prompt.length}/{MAX_PROMPT_CHARS}
              </span>
            </label>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {enumControls.map(([key, label]) => {
                const descriptor = supported[key];
                if (!descriptor?.values.length) return null;
                return (
                  <label className="block" key={key}>
                    <span className="text-xs font-semibold text-slate-300">{label}</span>
                    <select
                      className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2.5 text-sm"
                      onChange={(event) => selectValue(key, event.target.value)}
                      value={parameters[key] ?? ""}
                    >
                      <option value="">模型默认</option>
                      {descriptor.values.map((value) => (
                        <option key={value} value={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                );
              })}
              {supported.n ? (
                <label className="block">
                  <span className="text-xs font-semibold text-slate-300">生成数量</span>
                  <input
                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2.5 text-sm"
                    max={supported.n.max ?? 1}
                    min={supported.n.min ?? 1}
                    onChange={(event) => selectValue("n", event.target.value)}
                    type="number"
                    value={parameters.n ?? "1"}
                  />
                </label>
              ) : null}
            </div>

            {referenceLimit > 0 ? (
              <div className="rounded-lg border border-white/10 bg-ink-950/45 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-white">参考图</p>
                    <p className="mt-1 text-xs text-slate-400">用于图片编辑或保持主体风格，最多 {referenceLimit} 张。</p>
                  </div>
                  <label className="cursor-pointer rounded-full border border-white/15 px-3 py-2 text-xs font-semibold hover:bg-white/[0.06]">
                    选择图片
                    <input
                      accept="image/jpeg,image/png,image/webp"
                      className="sr-only"
                      multiple
                      onChange={addReferences}
                      type="file"
                    />
                  </label>
                </div>
                {references.length ? (
                  <ul className="mt-3 space-y-2 text-xs text-slate-300">
                    {references.map((file, index) => (
                      <li className="flex items-center justify-between gap-3" key={`${file.name}-${file.lastModified}`}>
                        <span className="truncate">{index + 1}. {file.name}</span>
                        <button className="text-rose-200" onClick={() => setReferences((current) => current.filter((_, itemIndex) => itemIndex !== index))} type="button">移除</button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {costEstimate && costLabel ? (
              <div
                aria-live="polite"
                className="flex flex-wrap items-center justify-between gap-3 border-y border-white/10 py-4"
              >
                <div>
                  <p className="text-sm font-semibold text-white">
                    预估费用 {costLabel}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    {costEstimate.inputUsd > 0
                      ? `已包含 ${references.length} 张参考图 ${formatUsd(costEstimate.inputUsd)}。`
                      : costEstimate.exact
                        ? "按当前分辨率、质量和生成数量估算。"
                        : "选择分辨率和质量后可缩小估算区间。"}
                    最终费用以 OpenRouter 网关结算为准。
                  </p>
                </div>
                <span className="rounded-full border border-fuchsia-300/25 px-3 py-1.5 text-xs font-semibold text-fuchsia-100">
                  目录估算
                </span>
              </div>
            ) : null}

            {error ? <p className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">{error}</p> : null}
            <button
              className="w-full rounded-lg bg-fuchsia-300 px-4 py-3 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={busy || !prompt.trim() || profile?.interaction_status !== "ready"}
              type="submit"
            >
              {busy
                ? "正在生成并校验完整图片…"
                : costLabel
                  ? `生成图片 · 预计 ${costLabel}`
                  : "生成图片 · 费用以网关结算为准"}
            </button>
          </form>
        </section>

        <aside className="space-y-4">
          <div className="rounded-xl border border-white/10 bg-white/[0.045] p-5">
            <p className="text-sm font-semibold text-white">能力状态</p>
            <p className="mt-2 text-sm text-slate-300">
              {profile?.interaction_status === "ready"
                ? `实时目录已确认${catalog?.stale ? "（缓存）" : ""}`
                : catalog === null
                  ? "正在读取图片能力…"
                  : "当前能力未确认"}
            </p>
            <p className="mt-3 text-xs leading-5 text-amber-100">
              图片可能由上游供应商临时处理；请勿上传不希望交给模型服务的敏感图像。
            </p>
          </div>
          {result ? (
            <div className="space-y-4 rounded-xl border border-emerald-300/20 bg-emerald-300/[0.05] p-4">
              <div>
                <p className="text-sm font-semibold text-white">生成完成</p>
                <p className="mt-1 break-all text-xs text-slate-400">{result.actual_model} · {result.request_id}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {result.usage.cost_usd === null ? "费用以网关结算为准" : `本次费用 $${result.usage.cost_usd.toFixed(4)}`}
                </p>
              </div>
              {result.images.map((image, index) => (
                <figure className="overflow-hidden rounded-lg border border-white/10 bg-ink-950" key={`${image.media_type}-${index}`}>
                  <img alt={`生成结果 ${index + 1}`} className="h-auto w-full" src={image.data_url} />
                  <figcaption className="flex items-center justify-between gap-3 p-3 text-xs text-slate-400">
                    <span>{image.media_type} · {(image.output_bytes / 1024).toFixed(0)} KiB</span>
                    <a download={`modelmirror-image-${index + 1}.${extensionFor(image.media_type)}`} href={image.data_url}>下载</a>
                  </figcaption>
                </figure>
              ))}
            </div>
          ) : null}
        </aside>
      </main>
    </div>
  );
}
