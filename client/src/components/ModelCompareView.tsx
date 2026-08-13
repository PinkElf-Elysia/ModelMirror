import { type Model } from "../data/models";
import {
  deriveProviderFromModel,
  getFriendlyJobCapabilityLabel,
} from "../utils/userFriendlyText";
import { formatPricingOverridesCny } from "../utils/tokenPricing";

interface ModelCompareViewProps {
  models: Model[];
  onBack: () => void;
  onRemove: (modelId: string) => void;
}

function formatContext(value: number) {
  if (value <= 0) return "—";
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M`;
  return `${Math.round(value / 1000)}K`;
}

function formatPrice(model: Model, side: "input" | "output") {
  if (model.pricing_status === "free") return "免费";
  if (model.pricing_basis === "media") return "按媒体计费";
  if (model.pricing_basis === "request") return "按请求计费";
  if (model.pricing_status === "dynamic") return "动态";
  return `${model.pricing_overrides.length ? "起 " : ""}¥${model.price_cny[side].toFixed(2)}/M`;
}

export default function ModelCompareView({
  models,
  onBack,
  onRemove,
}: ModelCompareViewProps) {
  const rows = [
    { label: "提供商", value: (model: Model) => deriveProviderFromModel(model) },
    {
      label: "接入状态",
      value: (model: Model) =>
        model.interaction_status === "ready" && model.active
          ? "目录可用"
          : "入口受限",
    },
    {
      label: "输入类型",
      value: (model: Model) => model.input_modalities.join("、") || "—",
    },
    {
      label: "核心能力",
      value: (model: Model) =>
        model.job_capabilities
          .slice(0, 6)
          .map(getFriendlyJobCapabilityLabel)
          .join("、") || "—",
    },
    { label: "上下文", value: (model: Model) => formatContext(model.context_length) },
    { label: "输入价格", value: (model: Model) => formatPrice(model, "input") },
    { label: "输出价格", value: (model: Model) => formatPrice(model, "output") },
    {
      label: "分段价格",
      value: (model: Model) => formatPricingOverridesCny(model) || "—",
    },
    {
      label: "地区路由",
      value: (model: Model) => (model.in_region_routing ? "支持" : "未声明"),
    },
    { label: "模型 ID", value: (model: Model) => model.id },
  ];

  return (
    <section aria-labelledby="model-compare-title">
      <div className="flex flex-col gap-4 border-b border-white/10 pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-[#ffc57d]">模型对比</p>
          <h2 className="mt-1 text-2xl font-semibold text-white" id="model-compare-title">
            比较真实目录字段
          </h2>
          <p className="mt-2 text-sm text-slate-400">
            不生成评分或排名，缺失字段统一显示为“—”。
          </p>
        </div>
        <button
          className="min-h-11 rounded-lg border border-white/10 px-4 text-sm font-semibold text-slate-200 hover:bg-white/[0.06]"
          onClick={onBack}
          type="button"
        >
          返回模型列表
        </button>
      </div>

      <div className="mt-5 hidden overflow-x-auto md:block">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-white/10">
              <th className="w-40 px-4 py-4 font-medium text-slate-500">字段</th>
              {models.map((model) => (
                <th className="min-w-56 px-4 py-4 align-top" key={model.id}>
                  <p className="font-semibold text-white">{model.name}</p>
                  <button
                    className="mt-2 text-xs font-medium text-amber-200 underline underline-offset-4"
                    onClick={() => onRemove(model.id)}
                    type="button"
                  >
                    移出对比
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr className="border-b border-white/[0.07]" key={row.label}>
                <th className="px-4 py-4 font-medium text-slate-500">{row.label}</th>
                {models.map((model) => (
                  <td className="px-4 py-4 align-top leading-6 text-slate-200" key={model.id}>
                    {row.value(model)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-5 space-y-4 md:hidden">
        {models.map((model) => (
          <article className="rounded-xl border border-white/10 bg-[#0e1929] p-4" key={model.id}>
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-semibold text-white">{model.name}</h3>
              <button
                className="min-h-11 shrink-0 text-xs font-medium text-amber-200"
                onClick={() => onRemove(model.id)}
                type="button"
              >
                移出
              </button>
            </div>
            <dl className="mt-3 divide-y divide-white/[0.07]">
              {rows.map((row) => (
                <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-3 py-3 text-sm" key={row.label}>
                  <dt className="text-slate-500">{row.label}</dt>
                  <dd className="break-words text-slate-200">{row.value(model)}</dd>
                </div>
              ))}
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
