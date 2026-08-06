import { useState, type ReactNode } from "react";
import {
  CONTEXT_RANGE_LIMIT,
  PROMPT_PRICE_CNY_LIMIT,
  contextQuickOptions,
  inputModalityOptions,
  jobCapabilityOptions,
  type Option,
  priceQuickOptions,
  providerOptions,
  supportedParameterOptions,
} from "../../data/filterOptions";
import { type ModelFilterState } from "../../data/filterState";
import { recruitmentFilterTitles, recruitmentTheme } from "../../theme/recruitmentTheme";
import CheckboxFilter from "./CheckboxFilter";
import RadioFilter from "./RadioFilter";
import RangeSlider from "./RangeSlider";
import TagFilter from "./TagFilter";
import ToggleFilter from "./ToggleFilter";

interface FilterPanelProps {
  filters: ModelFilterState;
  matchingCount: number;
  modelAuthorOptions: Option<string>[];
  seriesOptions: Option<string>[];
  totalCount: number;
  onChange: (filters: ModelFilterState) => void;
}

interface AccordionSectionProps {
  children: ReactNode;
  defaultOpen?: boolean;
  title: string;
}

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

function AccordionSection({
  children,
  defaultOpen = false,
  title,
}: AccordionSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]">
      <button
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between px-3 py-3 text-left text-sm font-semibold text-slate-100 transition duration-200 hover:bg-white/[0.06]"
        onClick={() => setIsOpen((current) => !current)}
        type="button"
      >
        <span>{title}</span>
        <span className={`text-slate-500 transition duration-200 ${isOpen ? "rotate-180 text-hire-200" : ""}`}>⌄</span>
      </button>
      <div
        className={`grid transition-all duration-200 ${
          isOpen ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="overflow-hidden">
          <div className="max-h-72 overflow-y-auto border-t border-white/10 px-3 pb-3 pt-3">
            {children}
          </div>
        </div>
      </div>
    </section>
  );
}

export default function FilterPanel({
  filters,
  matchingCount,
  modelAuthorOptions,
  seriesOptions,
  totalCount,
  onChange,
}: FilterPanelProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [desktopOpen, setDesktopOpen] = useState(true);

  function update<K extends keyof ModelFilterState>(
    key: K,
    value: ModelFilterState[K],
  ) {
    onChange({ ...filters, [key]: value });
  }

  const providerRadioOptions = [
    { value: "all" as const, label: "全部" },
    ...providerOptions,
  ];

  const panelContent = (onDismiss: () => void) => (
    <div className="surface-panel overflow-hidden rounded-lg">
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-white">
            {recruitmentTheme.filterPanelTitle}
          </p>
          <p className="mt-0.5 text-xs text-slate-400">
            {recruitmentTheme.filterPanelDescription}
          </p>
        </div>
        <button
          className="w-fit rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 active:scale-[0.98]"
          onClick={onDismiss}
          type="button"
        >
          撤下招工牌
        </button>
      </div>

      <div className="max-h-[72vh] overflow-y-auto px-3 py-3 lg:max-h-[calc(100vh-18rem)]">
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
        <AccordionSection defaultOpen title={recruitmentFilterTitles.provider}>
          <RadioFilter
            name="providers"
            onChange={(value) => update("provider", value)}
            options={providerRadioOptions}
            value={filters.provider}
          />
        </AccordionSection>

        <AccordionSection defaultOpen title={recruitmentFilterTitles.inputModalities}>
          <TagFilter
            onToggle={(value) =>
              update(
                "inputModalities",
                toggleValue(filters.inputModalities, value),
              )
            }
            options={inputModalityOptions}
            selected={filters.inputModalities}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.jobCapabilities}>
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
        </AccordionSection>

        <AccordionSection defaultOpen title={recruitmentFilterTitles.context}>
          <RangeSlider
            formatValue={formatContext}
            max={CONTEXT_RANGE_LIMIT.max}
            min={CONTEXT_RANGE_LIMIT.min}
            onChange={(value) => update("contextRange", value)}
            quickOptions={contextQuickOptions}
            step={1000}
            value={filters.contextRange}
          />
        </AccordionSection>

        <AccordionSection defaultOpen title={recruitmentFilterTitles.pricing}>
          <RangeSlider
            formatValue={formatCny}
            max={PROMPT_PRICE_CNY_LIMIT.max}
            min={PROMPT_PRICE_CNY_LIMIT.min}
            onChange={(value) => update("promptPriceCnyRange", value)}
            quickOptions={priceQuickOptions}
            step={0.1}
            value={filters.promptPriceCnyRange}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.series}>
          <TagFilter
            onToggle={(value) => update("series", toggleValue(filters.series, value))}
            options={seriesOptions}
            selected={filters.series}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.parameters}>
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
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.distillable}>
          <ToggleFilter
            checked={filters.distillable}
            description="仅显示适合训练和传帮带的候选人"
            label="可带徒弟"
            onChange={(checked) => update("distillable", checked)}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.zdr}>
          <ToggleFilter
            checked={filters.zeroDataRetention}
            description="仅显示更注重数据保密的候选人"
            label="保密意识强"
            onChange={(checked) => update("zeroDataRetention", checked)}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.routing}>
          <ToggleFilter
            checked={filters.inRegionRouting}
            description="仅显示支持指定区域部署和调用的候选人"
            label="支持本地驻场"
            onChange={(checked) => update("inRegionRouting", checked)}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.authors}>
          <TagFilter
            onToggle={(value) =>
              update("modelAuthors", toggleValue(filters.modelAuthors, value))
            }
            options={modelAuthorOptions}
            selected={filters.modelAuthors}
          />
        </AccordionSection>

        <AccordionSection title={recruitmentFilterTitles.inactive}>
          <ToggleFilter
            checked={filters.showInactive}
            description="包含已退场但仍可参考的历史候选人"
            label="显示历史候选人"
            onChange={(checked) => update("showInactive", checked)}
          />
        </AccordionSection>
        </div>
      </div>

      <div className="border-t border-white/10 px-4 py-3 text-xs text-slate-400">
        当前可面试{" "}
        <span className="font-semibold text-white">{matchingCount}</span> /{" "}
        {totalCount} 个模型
      </div>
    </div>
  );

  return (
    <div>
      <div className="lg:hidden">
        <button
          aria-expanded={mobileOpen}
          className="mb-3 flex w-full items-center justify-between rounded-full border border-white/10 bg-white/[0.07] px-4 py-3 text-sm font-medium text-slate-100 shadow-panel backdrop-blur-xl transition hover:border-hire-300/30 hover:bg-white/[0.09]"
          onClick={() => setMobileOpen((current) => !current)}
          type="button"
        >
          <span>{mobileOpen ? "收起岗位筛选" : "展开岗位筛选"}</span>
          <span className="text-hire-200">{matchingCount} 位候选人</span>
        </button>
        {mobileOpen ? (
          <div className="fixed inset-0 z-[70] bg-ink-950/72 backdrop-blur-sm">
            <button
              aria-label="关闭岗位筛选"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={() => setMobileOpen(false)}
              type="button"
            />
            <div className="absolute inset-x-0 bottom-0 max-h-[84vh] overflow-hidden rounded-t-lg border-t border-hire-300/30 bg-ink-950 shadow-[0_-24px_80px_rgba(0,0,0,0.55)]">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
                <span className="text-sm font-semibold text-white">
                  岗位筛选抽屉
                </span>
                <button
                  className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-white/10"
                  onClick={() => setMobileOpen(false)}
                  type="button"
                >
                  收起
                </button>
              </div>
              <div className="max-h-[calc(84vh-3.5rem)] overflow-y-auto p-3">
                {panelContent(() => setMobileOpen(false))}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="hidden lg:block">
        {desktopOpen ? (
          panelContent(() => setDesktopOpen(false))
        ) : (
          <button
            aria-expanded="false"
            className="surface-panel flex w-full items-center justify-between gap-3 rounded-lg px-4 py-3 text-left transition hover:border-hire-300/30 hover:bg-white/[0.055]"
            onClick={() => setDesktopOpen(true)}
            type="button"
          >
            <span className="text-sm font-semibold text-slate-300">
              招工牌已撤下
            </span>
            <span className="text-xs font-semibold text-hire-100">
              重新展开岗位筛选
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
