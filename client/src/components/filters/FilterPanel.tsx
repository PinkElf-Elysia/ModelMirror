import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ARTIFICIAL_ANALYSIS_RANGE_LIMIT,
  CONTEXT_MIN_LIMIT,
  DESIGN_ARENA_RANGE_LIMIT,
  MODEL_AGE_DAYS_LIMIT,
  OUTPUT_PRICE_USD_LIMIT,
  PROMPT_PRICE_USD_LIMIT,
  artificialAnalysisMetricOptions,
  contextQuickOptions,
  designArenaMetricOptions,
  jobCapabilityOptions,
  openRouterCategoryOptions,
  promptPriceQuickOptions,
  providerOptions,
  regionOptions,
  supportedParameterOptions,
  type Option,
  type RangeValue,
} from "../../data/filterOptions";
import {
  defaultFilterState,
  type ModelFilterState,
} from "../../data/filterState";
import type { OpenRouterMarketSeries } from "../../data/openRouterMarket";
import CheckboxFilter from "./CheckboxFilter";
import RangeSlider from "./RangeSlider";
import TagFilter from "./TagFilter";
import ToggleFilter from "./ToggleFilter";

interface FilterPanelProps {
  filters: ModelFilterState;
  modelAuthorOptions: Option<string>[];
  seriesOptions: Option<OpenRouterMarketSeries>[];
  onChange: (filters: ModelFilterState) => void;
  onClear: () => void;
}

type AdvancedTab =
  | "provider"
  | "pricing"
  | "series"
  | "categories"
  | "parameters"
  | "benchmarks"
  | "capability";

const advancedTabs: Array<{ id: AdvancedTab; label: string }> = [
  { id: "provider", label: "提供商与模型商" },
  { id: "pricing", label: "价格与上下文" },
  { id: "series", label: "模型系列" },
  { id: "categories", label: "应用分类" },
  { id: "parameters", label: "参数与状态" },
  { id: "benchmarks", label: "基准指标" },
  { id: "capability", label: "可完成任务" },
];

const primaryJobCapabilities: ModelFilterState["jobCapabilities"] = [
  "image_understanding",
  "image_generation",
  "video_generation",
  "realtime_voice",
  "transcription",
  "speech_synthesis",
  "music_generation",
];

const compactInputLabels: Record<
  ModelFilterState["inputModalities"][number],
  string
> = {
  text: "文本",
  image: "图片",
  file: "文件",
  audio: "音频",
  video: "视频",
};

const compactInputOrder: ModelFilterState["inputModalities"] = [
  "text",
  "image",
  "file",
  "audio",
  "video",
];

function toggleValue<T extends string>(values: T[], value: T) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function formatContext(value: number) {
  if (value >= 1_000_000) return "1M";
  if (value >= 1000) return `${Math.round(value / 1000)}K`;
  return `${value}`;
}

function formatUsd(value: number) {
  return `$${value.toFixed(value % 1 === 0 ? 0 : 2)}/M`;
}

function formatAge(value: number) {
  return value >= MODEL_AGE_DAYS_LIMIT.max ? "12+ 月" : `${value} 天`;
}

function isDefaultRange(value: RangeValue, limit: RangeValue) {
  return value.min === limit.min && value.max === limit.max;
}

function CoreFilterChip({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      aria-pressed={active}
      className={`relative inline-flex min-h-9 items-center rounded-md border px-3 text-xs font-medium transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/60 ${
        active
          ? "border-cyan-300/35 bg-cyan-300/10 text-cyan-50 after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:bg-cyan-300"
          : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-hire-300/30 hover:bg-hire-300/[0.08] hover:text-hire-100"
      }`}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

function FilterGroup({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="mr-1 shrink-0 text-sm font-semibold text-slate-100">
        {label}
      </span>
      {children}
    </div>
  );
}

function FilterSection({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <section className="border-b border-white/10 pb-5 last:border-b-0 last:pb-0">
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      {description ? (
        <p className="mb-3 mt-1 text-xs leading-5 text-slate-400">
          {description}
        </p>
      ) : (
        <div className="mb-3" />
      )}
      {children}
    </section>
  );
}

function MinimumSlider({
  label,
  max,
  step,
  value,
  formatValue,
  onChange,
}: {
  label: string;
  max: number;
  step: number;
  value: number;
  formatValue: (value: number) => string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-100">
        {formatValue(value)}+
      </p>
      <label className="block text-xs text-slate-400">
        {label}
        <input
          aria-label={label}
          className="mt-2 h-2 w-full cursor-pointer appearance-none rounded-full bg-white/10 accent-hire-300"
          max={max}
          min={0}
          onChange={(event) => onChange(Number(event.target.value))}
          step={step}
          type="range"
          value={value}
        />
      </label>
    </div>
  );
}

export default function FilterPanel({
  filters,
  modelAuthorOptions,
  seriesOptions,
  onChange,
  onClear,
}: FilterPanelProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<AdvancedTab>("provider");
  const [providerQuery, setProviderQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const capabilityTriggerRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLButtonElement | null>(null);

  function update<K extends keyof ModelFilterState>(
    key: K,
    value: ModelFilterState[K],
  ) {
    onChange({ ...filters, [key]: value });
  }

  function closeAdvanced(restoreFocus = true) {
    setAdvancedOpen(false);
    if (restoreFocus) {
      window.requestAnimationFrame(() => returnFocusRef.current?.focus());
    }
  }

  function openAdvanced(
    tab: AdvancedTab = activeTab,
    returnFocusTarget: HTMLButtonElement | null = triggerRef.current,
  ) {
    returnFocusRef.current = returnFocusTarget;
    setActiveTab(tab);
    setAdvancedOpen(true);
  }

  useEffect(() => {
    if (!advancedOpen) return;
    const frame = window.requestAnimationFrame(() => {
      const rootBottom = rootRef.current?.getBoundingClientRect().bottom ?? 0;
      panelRef.current?.style.setProperty(
        "--advanced-filter-max-height",
        `${Math.max(240, window.innerHeight - rootBottom - 28)}px`,
      );
      panelRef.current
        ?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
        ?.focus();
    });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeAdvanced();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        closeAdvanced();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [advancedOpen]);

  const normalizedProviderQuery = providerQuery.trim().toLowerCase();
  const visibleProviderOptions = useMemo(
    () =>
      providerOptions.filter((option) =>
        normalizedProviderQuery
          ? `${option.label} ${option.value}`
              .toLowerCase()
              .includes(normalizedProviderQuery)
          : true,
      ),
    [normalizedProviderQuery],
  );

  const hiddenJobCapabilityCount = filters.jobCapabilities.filter(
    (capability) => !primaryJobCapabilities.includes(capability),
  ).length;
  const benchmarkCount =
    Number(filters.minToolSuccessRate > 0) +
    Object.values(filters.artificialAnalysisRanges).filter(
      (range) => !isDefaultRange(range, ARTIFICIAL_ANALYSIS_RANGE_LIMIT),
    ).length +
    Object.values(filters.designArenaRanges).filter(
      (range) => !isDefaultRange(range, DESIGN_ARENA_RANGE_LIMIT),
    ).length;
  const advancedFilterCount =
    filters.providers.length +
    filters.series.length +
    hiddenJobCapabilityCount +
    filters.openRouterCategories.length +
    filters.supportedParameters.length +
    filters.modelAuthors.length +
    filters.regions.length +
    Number(filters.discounted) +
    Number(filters.minContextLength > 0) +
    Number(!isDefaultRange(filters.promptPriceUsdRange, PROMPT_PRICE_USD_LIMIT)) +
    Number(!isDefaultRange(filters.outputPriceUsdRange, OUTPUT_PRICE_USD_LIMIT)) +
    Number(!isDefaultRange(filters.modelAgeDaysRange, MODEL_AGE_DAYS_LIMIT)) +
    Number(filters.distillable !== "all") +
    Number(filters.zeroDataRetention) +
    Number(filters.showInactive) +
    benchmarkCount;

  const advancedContent = (() => {
    if (activeTab === "provider") {
      return (
        <div className="space-y-5">
          <FilterSection
            description="选择当前可用的服务端点，可同时选择多个。"
            title="服务提供商"
          >
            <label className="mb-3 block">
              <span className="sr-only">搜索服务提供商</span>
              <input
                className="h-11 w-full rounded-md border border-white/10 bg-ink-950/70 px-4 text-sm text-white outline-none placeholder:text-slate-500 focus:border-hire-300/55 focus:ring-2 focus:ring-hire-300/10"
                onChange={(event) => setProviderQuery(event.target.value)}
                placeholder="搜索服务提供商"
                type="search"
                value={providerQuery}
              />
            </label>
            <CheckboxFilter
              onToggle={(value) =>
                update("providers", toggleValue(filters.providers, value))
              }
              options={visibleProviderOptions}
              selected={filters.providers}
            />
          </FilterSection>
          <FilterSection title="模型商">
            <TagFilter
              onToggle={(value) =>
                update(
                  "modelAuthors",
                  toggleValue(filters.modelAuthors, value),
                )
              }
              options={modelAuthorOptions}
              selected={filters.modelAuthors}
            />
          </FilterSection>
        </div>
      );
    }

    if (activeTab === "pricing") {
      return (
        <div className="space-y-5">
          <FilterSection title="折扣模型">
            <ToggleFilter
              checked={filters.discounted}
              description="仅显示当前存在折扣端点的模型。"
              label="仅看折扣"
              onChange={(checked) => update("discounted", checked)}
            />
          </FilterSection>
          <FilterSection title="上下文长度">
            <MinimumSlider
              formatValue={formatContext}
              label="最小上下文长度"
              max={CONTEXT_MIN_LIMIT}
              onChange={(value) => update("minContextLength", value)}
              step={1000}
              value={filters.minContextLength}
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {contextQuickOptions.map((option) => (
                <button
                  className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-xs text-slate-300 hover:border-hire-300/30 hover:text-hire-100"
                  key={option.label}
                  onClick={() => update("minContextLength", option.value)}
                  type="button"
                >
                  {option.label}
                </button>
              ))}
            </div>
          </FilterSection>
          <FilterSection
            description="按美元/百万 token 的输入价格筛选。"
            title="输入价格"
          >
            <RangeSlider
              formatValue={formatUsd}
              max={PROMPT_PRICE_USD_LIMIT.max}
              min={PROMPT_PRICE_USD_LIMIT.min}
              onChange={(value) => update("promptPriceUsdRange", value)}
              quickOptions={promptPriceQuickOptions}
              step={0.1}
              value={filters.promptPriceUsdRange}
            />
          </FilterSection>
          <FilterSection title="输出价格">
            <RangeSlider
              formatValue={formatUsd}
              max={OUTPUT_PRICE_USD_LIMIT.max}
              min={OUTPUT_PRICE_USD_LIMIT.min}
              onChange={(value) => update("outputPriceUsdRange", value)}
              step={0.5}
              value={filters.outputPriceUsdRange}
            />
          </FilterSection>
          <FilterSection title="模型年龄">
            <RangeSlider
              formatValue={formatAge}
              max={MODEL_AGE_DAYS_LIMIT.max}
              min={MODEL_AGE_DAYS_LIMIT.min}
              onChange={(value) => update("modelAgeDaysRange", value)}
              step={1}
              value={filters.modelAgeDaysRange}
            />
          </FilterSection>
        </div>
      );
    }

    if (activeTab === "series") {
      return (
        <FilterSection title="模型系列">
          <TagFilter
            onToggle={(value) =>
              update("series", toggleValue(filters.series, value))
            }
            options={seriesOptions}
            selected={filters.series}
          />
        </FilterSection>
      );
    }

    if (activeTab === "categories") {
      return (
        <FilterSection
          description="按模型适用的领域筛选。"
          title="应用分类"
        >
          <TagFilter
            onToggle={(value) =>
              update(
                "openRouterCategories",
                toggleValue(filters.openRouterCategories, value),
              )
            }
            options={openRouterCategoryOptions}
            selected={filters.openRouterCategories}
          />
        </FilterSection>
      );
    }

    if (activeTab === "parameters") {
      return (
        <div className="space-y-5">
          <FilterSection title="支持参数">
            <CheckboxFilter
              onToggle={(value) =>
                update(
                  "supportedParameters",
                  toggleValue(filters.supportedParameters, value),
                )
              }
              options={supportedParameterOptions}
              selected={filters.supportedParameters}
            />
          </FilterSection>
          <FilterSection title="可蒸馏">
            <div className="grid grid-cols-3 gap-2">
              {[
                { value: "all" as const, label: "全部" },
                { value: "yes" as const, label: "是" },
                { value: "no" as const, label: "否" },
              ].map((option) => (
                <label
                  className="flex min-h-11 cursor-pointer items-center gap-2 rounded-md border border-white/10 px-3 text-sm text-slate-200"
                  key={option.value}
                >
                  <input
                    checked={filters.distillable === option.value}
                    className="h-4 w-4 accent-hire-300"
                    name="distillable"
                    onChange={() => update("distillable", option.value)}
                    type="radio"
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </FilterSection>
          <FilterSection title="区域路由">
            <TagFilter
              onToggle={(value) =>
                update("regions", toggleValue(filters.regions, value))
              }
              options={regionOptions}
              selected={filters.regions}
            />
          </FilterSection>
          <div className="grid gap-3 sm:grid-cols-2">
            <ToggleFilter
              checked={filters.zeroDataRetention}
              description="仅显示至少有一个零数据保留端点的模型。"
              label="零数据保留"
              onChange={(checked) => update("zeroDataRetention", checked)}
            />
            <ToggleFilter
              checked={filters.showInactive}
              description="额外显示目录中明确到期的模型。"
              label="显示已下架模型"
              onChange={(checked) => update("showInactive", checked)}
            />
          </div>
        </div>
      );
    }

    if (activeTab === "benchmarks") {
      return (
        <div className="space-y-5">
          <FilterSection title="工具调用">
            <MinimumSlider
              formatValue={(value) => `${value}%`}
              label="最低成功率"
              max={100}
              onChange={(value) => update("minToolSuccessRate", value)}
              step={1}
              value={filters.minToolSuccessRate}
            />
          </FilterSection>
          <FilterSection title="综合能力评测">
            <div className="space-y-5">
              {artificialAnalysisMetricOptions.map((option) => (
                <div key={option.value}>
                  <p className="mb-2 text-xs font-medium text-slate-300">
                    {option.label}
                  </p>
                  <RangeSlider
                    formatValue={(value) => `${value}`}
                    max={ARTIFICIAL_ANALYSIS_RANGE_LIMIT.max}
                    min={ARTIFICIAL_ANALYSIS_RANGE_LIMIT.min}
                    onChange={(value) =>
                      update("artificialAnalysisRanges", {
                        ...filters.artificialAnalysisRanges,
                        [option.value]: value,
                      })
                    }
                    step={1}
                    value={filters.artificialAnalysisRanges[option.value]}
                  />
                </div>
              ))}
            </div>
          </FilterSection>
          <FilterSection title="设计能力评测">
            <div className="space-y-5">
              {designArenaMetricOptions.map((option) => (
                <div key={option.value}>
                  <p className="mb-2 text-xs font-medium text-slate-300">
                    {option.label}
                  </p>
                  <RangeSlider
                    formatValue={(value) => `${value} ELO`}
                    max={DESIGN_ARENA_RANGE_LIMIT.max}
                    min={DESIGN_ARENA_RANGE_LIMIT.min}
                    onChange={(value) =>
                      update("designArenaRanges", {
                        ...filters.designArenaRanges,
                        [option.value]: value,
                      })
                    }
                    step={10}
                    value={filters.designArenaRanges[option.value]}
                  />
                </div>
              ))}
            </div>
          </FilterSection>
        </div>
      );
    }

    return (
      <FilterSection
        description="按已接入的调用入口与任务能力筛选。"
        title="全部任务能力"
      >
        <TagFilter
          onToggle={(value) =>
            update(
              "jobCapabilities",
              toggleValue(filters.jobCapabilities, value),
            )
          }
          options={jobCapabilityOptions}
          selected={filters.jobCapabilities}
        />
      </FilterSection>
    );
  })();

  return (
    <div className="relative z-30" ref={rootRef}>
      <div className="relative overflow-hidden rounded-lg border border-white/10 bg-ink-950/72 px-4 py-3 shadow-[0_8px_8px_rgba(0,0,0,0.18)]">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0 max-w-[56rem] flex-1 space-y-2.5">
            <FilterGroup label="可接收输入">
              {compactInputOrder.map((modality) => (
                <CoreFilterChip
                  active={filters.inputModalities.includes(modality)}
                  key={modality}
                  onClick={() =>
                    update(
                      "inputModalities",
                      toggleValue(filters.inputModalities, modality),
                    )
                  }
                >
                  {compactInputLabels[modality]}
                </CoreFilterChip>
              ))}
            </FilterGroup>
            <FilterGroup label="可完成任务">
              {primaryJobCapabilities.map((capability) => {
                const option = jobCapabilityOptions.find(
                  (candidate) => candidate.value === capability,
                );
                return (
                  <CoreFilterChip
                    active={filters.jobCapabilities.includes(capability)}
                    key={capability}
                    onClick={() =>
                      update(
                        "jobCapabilities",
                        toggleValue(filters.jobCapabilities, capability),
                      )
                    }
                  >
                    {option?.label ?? capability}
                  </CoreFilterChip>
                );
              })}
              <button
                aria-controls="model-advanced-filter-panel"
                aria-expanded={advancedOpen && activeTab === "capability"}
                className="min-h-9 rounded-md px-2 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/60"
                onClick={() =>
                  advancedOpen && activeTab === "capability"
                    ? closeAdvanced()
                    : openAdvanced("capability", capabilityTriggerRef.current)
                }
                ref={capabilityTriggerRef}
                type="button"
              >
                {advancedOpen && activeTab === "capability"
                  ? "收起全部"
                  : "查看全部"}
                {hiddenJobCapabilityCount > 0
                  ? ` · 已选 ${hiddenJobCapabilityCount}`
                  : ""}
              </button>
            </FilterGroup>
          </div>
          <button
            aria-controls="model-advanced-filter-panel"
            aria-expanded={advancedOpen}
            className="relative z-10 flex min-h-11 shrink-0 items-center justify-between gap-3 self-stretch rounded-md border border-hire-300/45 bg-hire-300/[0.07] px-4 text-sm font-semibold text-hire-100 transition hover:border-hire-300/70 hover:bg-hire-300/12 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/60 lg:self-center"
            onClick={() =>
              advancedOpen
                ? closeAdvanced(false)
                : openAdvanced(activeTab, triggerRef.current)
            }
            ref={triggerRef}
            type="button"
          >
            <span>更多筛选</span>
            {advancedFilterCount > 0 ? (
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-hire-300 px-1.5 text-[11px] font-bold text-ink-950">
                {advancedFilterCount}
              </span>
            ) : null}
            <span aria-hidden="true" className={advancedOpen ? "rotate-180" : ""}>
              ⌄
            </span>
          </button>
        </div>
      </div>

      {advancedOpen ? (
        <>
          <button
            aria-label="关闭更多筛选"
            className="fixed inset-0 z-[140] bg-ink-950/70 lg:hidden"
            onClick={() => closeAdvanced()}
            type="button"
          />
          <div
            aria-label="更多筛选"
            className="fixed inset-x-0 bottom-0 z-[150] max-h-[82vh] overflow-y-auto rounded-t-lg border-t border-hire-300/35 bg-ink-950 pb-20 shadow-[0_-8px_8px_rgba(0,0,0,0.28)] lg:absolute lg:inset-x-auto lg:bottom-auto lg:right-0 lg:top-[calc(100%+0.75rem)] lg:max-h-[var(--advanced-filter-max-height,38rem)] lg:w-[min(50rem,calc(100vw-3rem))] lg:rounded-lg lg:border lg:border-white/10 lg:border-t-hire-300/45 lg:bg-[#091427] lg:pb-0 lg:shadow-[0_8px_8px_rgba(0,0,0,0.32)]"
            id="model-advanced-filter-panel"
            ref={panelRef}
            role="dialog"
          >
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3 lg:hidden">
              <p className="text-sm font-semibold text-white">更多筛选</p>
              <button
                className="min-h-11 rounded-md px-3 text-sm font-medium text-slate-300 hover:bg-white/[0.06] hover:text-white"
                onClick={() => closeAdvanced()}
                type="button"
              >
                关闭
              </button>
            </div>
            <div className="lg:grid lg:grid-cols-[11rem_minmax(0,1fr)]">
              <div
                aria-label="筛选分类"
                className="grid grid-cols-2 gap-1 border-b border-white/10 p-2 sm:grid-cols-3 lg:flex lg:flex-col lg:border-b-0 lg:border-r lg:p-3"
                role="tablist"
              >
                {advancedTabs.map((tab) => (
                  <button
                    aria-controls={`advanced-filter-panel-${tab.id}`}
                    aria-selected={activeTab === tab.id}
                    className={`relative min-h-11 rounded-md px-3 text-left text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/60 ${
                      activeTab === tab.id
                        ? "bg-hire-300/10 text-hire-100 before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:bg-hire-300"
                        : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
                    }`}
                    id={`advanced-filter-tab-${tab.id}`}
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    role="tab"
                    tabIndex={activeTab === tab.id ? 0 : -1}
                    type="button"
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <div
                aria-labelledby={`advanced-filter-tab-${activeTab}`}
                className="p-4"
                id={`advanced-filter-panel-${activeTab}`}
                role="tabpanel"
              >
                {advancedContent}
                <div className="mt-5 flex items-center justify-end gap-2 border-t border-white/10 pt-4">
                  <button
                    className="min-h-11 rounded-md border border-white/10 px-4 text-sm font-medium text-slate-300 transition hover:bg-white/[0.05] hover:text-white"
                    onClick={onClear}
                    type="button"
                  >
                    清除
                  </button>
                  <button
                    className="min-h-11 rounded-md bg-hire-300 px-5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100/70"
                    onClick={() => closeAdvanced()}
                    type="button"
                  >
                    应用筛选
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
