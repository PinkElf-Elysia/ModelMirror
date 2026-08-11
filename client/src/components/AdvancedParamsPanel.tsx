export interface ChatAdvancedParams {
  temperature: number;
  topP: number;
  maxTokens: number;
  seed: string;
  stopSequences: string;
}

interface AdvancedParamsPanelProps {
  isOpen: boolean;
  maxTokenLimit: number;
  params: ChatAdvancedParams;
  onChange: (params: ChatAdvancedParams) => void;
  onReset: () => void;
  onToggle: () => void;
  embedded?: boolean;
}

function numberInputClass() {
  return "w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm text-white outline-none transition focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";
}

function SliderRow({
  label,
  description,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string;
  description: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block rounded-lg border border-white/10 bg-white/[0.04] p-3">
      <span className="flex items-center justify-between gap-3">
        <span>
          <span className="block text-sm font-semibold text-white">{label}</span>
          <span className="mt-0.5 block text-xs leading-5 text-slate-400">
            {description}
          </span>
        </span>
        <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-2.5 py-1 text-xs font-semibold text-brand-100">
          {value}
        </span>
      </span>
      <input
        className="mt-3 h-2 w-full accent-cyan-300"
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        step={step}
        type="range"
        value={value}
      />
    </label>
  );
}

export default function AdvancedParamsPanel({
  isOpen,
  maxTokenLimit,
  params,
  onChange,
  onReset,
  onToggle,
  embedded = false,
}: AdvancedParamsPanelProps) {
  return (
    <div className={embedded ? "px-5 py-4" : "border-b border-white/10 bg-ink-950/36 px-4 py-3 sm:px-5"}>
      {!embedded ? <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          aria-expanded={isOpen}
          className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
          onClick={onToggle}
          type="button"
        >
          高级参数
          <span aria-hidden className={`transition ${isOpen ? "rotate-180" : ""}`}>⌄</span>
        </button>
        <p className="text-xs text-slate-400">
          当前：温度 {params.temperature}，Top P {params.topP}，最大 {params.maxTokens}
        </p>
      </div> : null}

      <div
        className={`grid transition-all duration-200 ${
          embedded || isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <div className={`${embedded ? "" : "mt-3"} grid gap-3 lg:grid-cols-2`}>
            <SliderRow
              description="越高越有创意，越低越保守。"
              label="Temperature（温度）"
              max={2}
              min={0}
              onChange={(temperature) => onChange({ ...params, temperature })}
              step={0.1}
              value={params.temperature}
            />
            <SliderRow
              description="控制候选词采样范围，1.0 表示不额外收窄。"
              label="Top P（核采样）"
              max={1}
              min={0}
              onChange={(topP) => onChange({ ...params, topP })}
              step={0.05}
              value={params.topP}
            />

            <label className="rounded-lg border border-white/10 bg-white/[0.04] p-3">
              <span className="text-sm font-semibold text-white">
                Max Tokens（最大输出长度）
              </span>
              <input
                className={`${numberInputClass()} mt-2`}
                max={maxTokenLimit}
                min={1}
                onChange={(event) =>
                  onChange({
                    ...params,
                    maxTokens: Math.min(
                      maxTokenLimit,
                      Math.max(1, Number(event.target.value) || 1),
                    ),
                  })
                }
                type="number"
                value={params.maxTokens}
              />
              <span className="mt-1 block text-xs text-slate-400">
                当前模型上限：{maxTokenLimit.toLocaleString("zh-CN")}
              </span>
            </label>

            <label className="rounded-lg border border-white/10 bg-white/[0.04] p-3">
              <span className="text-sm font-semibold text-white">
                Seed（随机种子）
              </span>
              <input
                className={`${numberInputClass()} mt-2`}
                onChange={(event) =>
                  onChange({ ...params, seed: event.target.value })
                }
                placeholder="留空为随机"
                type="number"
                value={params.seed}
              />
            </label>

            <label className="rounded-lg border border-white/10 bg-white/[0.04] p-3 lg:col-span-2">
              <span className="text-sm font-semibold text-white">
                Stop Sequences（停止序列）
              </span>
              <input
                className={`${numberInputClass()} mt-2`}
                onChange={(event) =>
                  onChange({ ...params, stopSequences: event.target.value })
                }
                placeholder="多个序列用英文逗号分隔"
                value={params.stopSequences}
              />
            </label>

            <button
              className="w-fit rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-rose-300/35 hover:bg-rose-300/10 hover:text-rose-100"
              onClick={onReset}
              type="button"
            >
              恢复默认
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
