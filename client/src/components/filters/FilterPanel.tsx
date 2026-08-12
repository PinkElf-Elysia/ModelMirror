import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  CONTEXT_RANGE_LIMIT,
  PROMPT_PRICE_CNY_LIMIT,
  contextQuickOptions,
  jobCapabilityOptions,
  type Option,
  priceQuickOptions,
  providerOptions,
  supportedParameterOptions,
} from "../../data/filterOptions";
import {
  defaultFilterState,
  type ModelFilterState,
} from "../../data/filterState";
import { recruitmentFilterTitles } from "../../theme/recruitmentTheme";
import CheckboxFilter from "./CheckboxFilter";
import RangeSlider from "./RangeSlider";
import TagFilter from "./TagFilter";
import ToggleFilter from "./ToggleFilter";

interface FilterPanelProps {
  filters: ModelFilterState;
  modelAuthorOptions: Option<string>[];
  seriesOptions: Option<string>[];
  onChange: (filters: ModelFilterState) => void;
  onClear: () => void;
}

type AdvancedTab = "provider" | "pricing" | "series" | "advanced";

const advancedTabs: Array<{ id: AdvancedTab; label: string }> = [
  { id: "provider", label: "用人单位" },
  { id: "pricing", label: "价格与上下文" },
  { id: "series", label: "模型系列" },
  { id: "advanced", label: "高级条件" },
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

function formatCny(value: number) {
  return `¥${value.toFixed(value % 1 === 0 ? 0 : 1)}`;
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

function FilterGroup({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="mr-1 shrink-0 text-sm font-semibold text-slate-100">
        {label}
      </span>
      {children}
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
      const availableHeight = Math.max(
        240,
        window.innerHeight - rootBottom - 28,
      );
      panelRef.current?.style.setProperty(
        "--advanced-filter-max-height",
        `${availableHeight}px`,
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
      const target = event.target;
      if (target instanceof Node && !rootRef.current?.contains(target)) {
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

  const providerPriority = [
    "OpenAI",
    "Anthropic",
    "深度求索",
    "Google",
    "通义千问",
    "Meta",
    "Mistral AI",
    "Microsoft",
  ];
  const prioritizedProviderOptions = useMemo(() => {
    const providerPriorityIndex = (label: string) => {
      const index = providerPriority.indexOf(label);
      return index === -1 ? providerPriority.length : index;
    };
    const seenProviderLabels = new Set<string>();
    return [...providerOptions]
      .sort((left, right) => {
        const priorityDifference =
          providerPriorityIndex(left.label) -
          providerPriorityIndex(right.label);
        return (
          priorityDifference || left.label.localeCompare(right.label, "zh-CN")
        );
      })
      .filter((option) => {
        if (seenProviderLabels.has(option.label)) return false;
        seenProviderLabels.add(option.label);
        return true;
      });
  }, []);

  const normalizedProviderQuery = providerQuery.trim().toLocaleLowerCase();
  const visibleProviderOptions = prioritizedProviderOptions.filter((option) =>
    normalizedProviderQuery.length === 0
      ? true
      : option.label.toLocaleLowerCase().includes(normalizedProviderQuery),
  );

  const hiddenJobCapabilityCount = filters.jobCapabilities.filter(
    (capability) => !primaryJobCapabilities.includes(capability),
  ).length;
  const advancedFilterCount =
    (filters.provider === "all" ? 0 : 1) +
    filters.series.length +
    hiddenJobCapabilityCount +
    filters.supportedParameters.length +
    filters.modelAuthors.length +
    (filters.contextRange.min === defaultFilterState.contextRange.min &&
    filters.contextRange.max === defaultFilterState.contextRange.max
      ? 0
      : 1) +
    (filters.promptPriceCnyRange.min ===
      defaultFilterState.promptPriceCnyRange.min &&
    filters.promptPriceCnyRange.max ===
      defaultFilterState.promptPriceCnyRange.max
      ? 0
      : 1) +
    Number(filters.distillable) +
    Number(filters.zeroDataRetention) +
    Number(filters.inRegionRouting) +
    Number(filters.showInactive);

  const advancedContent = (() => {
    if (activeTab === "provider") {
      return (
        <div>
          <label className="block">
            <span className="sr-only">搜索用人单位</span>
            <input
              className="h-11 w-full rounded-md border border-white/10 bg-ink-950/70 px-4 text-sm text-white outline-none placeholder:text-slate-500 focus:border-hire-300/55 focus:ring-2 focus:ring-hire-300/10"
              onChange={(event) => setProviderQuery(event.target.value)}
              placeholder="搜索用人单位"
              type="search"
              value={providerQuery}
            />
          </label>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {[{ value: "all" as const, label: "全部" }, ...visibleProviderOptions].map(
              (option) => {
                const selected = filters.provider === option.value;
                return (
                  <label
                    className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm transition ${
                      selected
                        ? "border-hire-300/40 bg-hire-300/10 text-hire-100"
                        : "border-transparent text-slate-300 hover:border-white/10 hover:bg-white/[0.04] hover:text-white"
                    }`}
                    key={option.value}
                  >
                    <input
                      checked={selected}
                      className="h-4 w-4 accent-hire-300"
                      name="providers"
                      onChange={() => update("provider", option.value)}
                      type="radio"
                    />
                    <span className="truncate">{option.label}</span>
                  </label>
                );
              },
            )}
          </div>
        </div>
      );
    }

    if (activeTab === "pricing") {
      return (
        <div className="space-y-5">
          <section aria-labelledby="context-filter-heading">
            <h3
              className="mb-3 text-sm font-semibold text-slate-100"
              id="context-filter-heading"
            >
              {recruitmentFilterTitles.context}
            </h3>
            <RangeSlider
              formatValue={formatContext}
              max={CONTEXT_RANGE_LIMIT.max}
              min={CONTEXT_RANGE_LIMIT.min}
              onChange={(value) => update("contextRange", value)}
              quickOptions={contextQuickOptions}
              step={1000}
              value={filters.contextRange}
            />
          </section>
          <section
            aria-labelledby="price-filter-heading"
            className="border-t border-white/10 pt-5"
          >
            <h3
              className="mb-3 text-sm font-semibold text-slate-100"
              id="price-filter-heading"
            >
              {recruitmentFilterTitles.pricing}
            </h3>
            <RangeSlider
              formatValue={formatCny}
              max={PROMPT_PRICE_CNY_LIMIT.max}
              min={PROMPT_PRICE_CNY_LIMIT.min}
              onChange={(value) => update("promptPriceCnyRange", value)}
              quickOptions={priceQuickOptions}
              step={0.1}
              value={filters.promptPriceCnyRange}
            />
          </section>
        </div>
      );
    }

    if (activeTab === "series") {
      return (
        <section aria-labelledby="series-filter-heading">
          <h3
            className="mb-3 text-sm font-semibold text-slate-100"
            id="series-filter-heading"
          >
            {recruitmentFilterTitles.series}
          </h3>
          <TagFilter
            onToggle={(value) =>
              update("series", toggleValue(filters.series, value))
            }
            options={seriesOptions}
            selected={filters.series}
          />
        </section>
      );
    }

    return (
      <div className="space-y-5">
        <section aria-labelledby="all-capabilities-heading">
          <h3
            className="mb-3 text-sm font-semibold text-slate-100"
            id="all-capabilities-heading"
          >
            全部岗位能力
          </h3>
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
        </section>
        <section
          aria-labelledby="parameters-filter-heading"
          className="border-t border-white/10 pt-5"
        >
          <h3
            className="mb-3 text-sm font-semibold text-slate-100"
            id="parameters-filter-heading"
          >
            {recruitmentFilterTitles.parameters}
          </h3>
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
        </section>
        <section
          aria-labelledby="authors-filter-heading"
          className="border-t border-white/10 pt-5"
        >
          <h3
            className="mb-3 text-sm font-semibold text-slate-100"
            id="authors-filter-heading"
          >
            {recruitmentFilterTitles.authors}
          </h3>
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
        </section>
        <div className="grid gap-3 border-t border-white/10 pt-5 sm:grid-cols-2">
          <ToggleFilter
            checked={filters.distillable}
            description="仅显示适合训练和传帮带的候选人"
            label="可带徒弟"
            onChange={(checked) => update("distillable", checked)}
          />
          <ToggleFilter
            checked={filters.zeroDataRetention}
            description="仅显示更注重数据保密的候选人"
            label="保密意识强"
            onChange={(checked) => update("zeroDataRetention", checked)}
          />
          <ToggleFilter
            checked={filters.inRegionRouting}
            description="仅显示支持指定区域路由的候选人"
            label="区域路由"
            onChange={(checked) => update("inRegionRouting", checked)}
          />
          <ToggleFilter
            checked={filters.showInactive}
            description="额外显示目录中明确到期的模型"
            label="显示到期候选人"
            onChange={(checked) => update("showInactive", checked)}
          />
        </div>
      </div>
    );
  })();

  return (
    <div className="relative z-30" ref={rootRef}>
      <div className="relative overflow-hidden rounded-lg border border-white/10 bg-ink-950/72 px-4 py-3 shadow-[0_8px_8px_rgba(0,0,0,0.18)]">
        <div className="pointer-events-none absolute right-5 top-2 hidden h-12 w-36 opacity-35 lg:block">
          <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-cyan-300/70" />
          <span className="absolute right-8 top-5 h-1 w-1 rounded-full bg-hire-300/70" />
          <span className="absolute right-16 top-1 h-1 w-1 rounded-full bg-cyan-300/60" />
          <span className="absolute right-1 top-1 h-px w-16 origin-right -rotate-[18deg] bg-cyan-300/25" />
          <span className="absolute right-8 top-5 h-px w-12 origin-right rotate-[28deg] bg-hire-300/20" />
        </div>

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
                aria-expanded={advancedOpen && activeTab === "advanced"}
                className="min-h-9 rounded-md px-2 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/60"
                onClick={() =>
                  advancedOpen && activeTab === "advanced"
                    ? closeAdvanced()
                    : openAdvanced("advanced", capabilityTriggerRef.current)
                }
                ref={capabilityTriggerRef}
                type="button"
              >
                {advancedOpen && activeTab === "advanced"
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
            <span
              aria-hidden="true"
              className={`text-hire-200 transition ${
                advancedOpen ? "rotate-180" : ""
              }`}
            >
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
            className="fixed inset-x-0 bottom-0 z-[150] max-h-[82vh] overflow-y-auto rounded-t-lg border-t border-hire-300/35 bg-ink-950 pb-20 shadow-[0_-8px_8px_rgba(0,0,0,0.28)] lg:absolute lg:inset-x-auto lg:bottom-auto lg:right-0 lg:top-[calc(100%+0.75rem)] lg:max-h-[var(--advanced-filter-max-height,38rem)] lg:w-[min(42rem,calc(100vw-3rem))] lg:rounded-lg lg:border lg:border-white/10 lg:border-t-hire-300/45 lg:bg-[#091427] lg:pb-0 lg:shadow-[0_8px_8px_rgba(0,0,0,0.32)]"
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

            <div className="lg:grid lg:grid-cols-[9.5rem_minmax(0,1fr)]">
              <div
                aria-label="筛选分类"
                className="grid grid-cols-2 gap-1 border-b border-white/10 p-2 sm:flex lg:flex-col lg:border-b-0 lg:border-r lg:p-3"
                role="tablist"
              >
                {advancedTabs.map((tab) => (
                  <button
                    aria-controls={`advanced-filter-panel-${tab.id}`}
                    aria-selected={activeTab === tab.id}
                    className={`relative min-h-11 flex-1 rounded-md px-3 text-left text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200/60 lg:flex-none ${
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
