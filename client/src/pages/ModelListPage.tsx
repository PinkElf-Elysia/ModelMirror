import { useEffect, useMemo, useState } from "react";
import FederationRouterCard from "../components/FederationRouterCard";
import ModelCard from "../components/ModelCard";
import PageContainer from "../components/PageContainer";
import FilterPanel from "../components/filters/FilterPanel";
import {
  defaultFilterState,
  type ModelFilterState,
} from "../data/filterState";
import { models } from "../data/models";
import { recruitmentTheme } from "../theme/recruitmentTheme";
import {
  deriveProviderFromModel,
  providerFilterMatches,
} from "../utils/userFriendlyText";

function includesEvery<T>(values: T[], selected: T[]) {
  return selected.every((value) => values.includes(value));
}

function matchesAny<T>(value: T, selected: T[]) {
  return selected.length === 0 || selected.includes(value);
}

function isDefaultFilters(filters: ModelFilterState) {
  return JSON.stringify(filters) === JSON.stringify(defaultFilterState);
}

function createDefaultFilters(): ModelFilterState {
  return {
    ...defaultFilterState,
    inputModalities: [],
    contextRange: { ...defaultFilterState.contextRange },
    promptPriceCnyRange: { ...defaultFilterState.promptPriceCnyRange },
    series: [],
    categories: [],
    supportedParameters: [],
    modelAuthors: [],
  };
}

function countActiveFilters(filters: ModelFilterState) {
  let count = 0;

  if (filters.provider !== "all") count += 1;
  count += filters.inputModalities.length;
  count += filters.series.length;
  count += filters.categories.length;
  count += filters.supportedParameters.length;
  count += filters.modelAuthors.length;
  if (filters.distillable) count += 1;
  if (filters.zeroDataRetention) count += 1;
  if (filters.inRegionRouting) count += 1;
  if (filters.showInactive) count += 1;
  if (filters.contextRange.min !== defaultFilterState.contextRange.min) count += 1;
  if (filters.contextRange.max !== defaultFilterState.contextRange.max) count += 1;
  if (filters.promptPriceCnyRange.min !== defaultFilterState.promptPriceCnyRange.min) count += 1;
  if (filters.promptPriceCnyRange.max !== defaultFilterState.promptPriceCnyRange.max) count += 1;

  return count;
}

export default function ModelListPage() {
  const [filters, setFilters] =
    useState<ModelFilterState>(createDefaultFilters);
  const [searchTerm, setSearchTerm] = useState("");

  useEffect(() => {
    document.title = "模镜 - AI 牛马招聘会";
  }, []);

  const seriesOptions = useMemo(
    () =>
      Array.from(new Set(models.map((model) => model.series)))
        .filter((series) => series.length > 0)
        .sort((left, right) => left.localeCompare(right, "zh-CN"))
        .map((series) => ({ value: series, label: series })),
    [],
  );

  const modelAuthorOptions = useMemo(
    () =>
      Array.from(new Set(models.map((model) => model.model_author)))
        .filter((author) => author.length > 0)
        .sort((left, right) => left.localeCompare(right, "zh-CN"))
        .map((author) => ({ value: author, label: author })),
    [],
  );

  const filteredModels = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();

    return models.filter((model) => {
      const matchesSearch =
        normalizedSearch.length === 0 ||
        [
          model.name,
          model.id,
          model.provider,
          deriveProviderFromModel(model),
          model.model_author,
          model.series,
          model.description,
          ...model.tags,
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedSearch);

      if (!matchesSearch) {
        return false;
      }

      if (!filters.showInactive && !model.active) return false;
      if (!providerFilterMatches(model, filters.provider)) {
        return false;
      }
      if (!includesEvery(model.input_modalities, filters.inputModalities)) {
        return false;
      }
      if (!matchesAny(model.series, filters.series)) return false;
      if (!includesEvery(model.categories, filters.categories)) return false;
      if (
        !includesEvery(
          model.supported_parameters,
          filters.supportedParameters,
        )
      ) {
        return false;
      }
      if (
        filters.modelAuthors.length > 0 &&
        !filters.modelAuthors.includes(model.model_author)
      ) {
        return false;
      }
      if (filters.distillable && !model.distillable) return false;
      if (filters.zeroDataRetention && !model.zero_data_retention) return false;
      if (filters.inRegionRouting && !model.in_region_routing) return false;
      if (model.context_length < filters.contextRange.min) return false;
      if (
        filters.contextRange.max < defaultFilterState.contextRange.max &&
        model.context_length > filters.contextRange.max
      ) {
        return false;
      }

      const inputPriceCny = model.price_cny.input;
      if (inputPriceCny < filters.promptPriceCnyRange.min) return false;
      if (
        filters.promptPriceCnyRange.max <
          defaultFilterState.promptPriceCnyRange.max &&
        inputPriceCny > filters.promptPriceCnyRange.max
      ) {
        return false;
      }

      return true;
    });
  }, [filters, searchTerm]);

  function clearFilters() {
    setFilters(createDefaultFilters());
    setSearchTerm("");
  }

  const activeFilterCount = countActiveFilters(filters);
  const readyFilteredCount = filteredModels.filter(
    (model) => model.interaction_status === "ready",
  ).length;
  const featuredModels = filteredModels.slice(0, 2);
  const galleryModels = filteredModels.slice(featuredModels.length);

  return (
    <PageContainer
      activeResource="models"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">资源分区</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            浏览模型能力，并进入当前已适配的对话或资料库入口。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">当前可使用</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {readyFilteredCount} / {filteredModels.length}
            </p>
          </div>
        </div>
      }
    >
        <header className="relative overflow-hidden border-y border-hire-300/20 py-8 sm:py-10 lg:py-12">
          <div className="absolute inset-x-6 top-0 h-16 rounded-b-[50%] border-x border-b border-hire-300/30 bg-[linear-gradient(180deg,rgba(251,146,60,0.18),transparent)]" />
          <div className="absolute left-0 top-0 h-px w-full animate-pulse-line bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />
          <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
            <div className="min-w-0">
              <div className="max-w-4xl">
                <p className="text-sm font-semibold text-hire-200">
                  赛博人才市场正在营业
                </p>
                <h1 className="mt-3 max-w-3xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
                  {recruitmentTheme.eventTitle}
                </h1>
                <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
                  {recruitmentTheme.eventSubtitle}。{recruitmentTheme.eventPitch}
                  薪资按 1 USD ≈ 6.77 CNY 换算为人民币/百万 token。
                </p>
              </div>
            </div>

            <div className="surface-card min-w-0 overflow-hidden rounded-lg p-4">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <span className="text-sm text-slate-400">现场候选人</span>
                <span className="text-2xl font-semibold text-white">
                  {models.length}
                </span>
              </div>
              <div className="mt-4 grid grid-cols-[repeat(3,minmax(0,1fr))] gap-2 text-center text-xs">
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-hire-100">
                    {readyFilteredCount}
                  </p>
                  <p className="mt-1 truncate text-slate-400">可使用</p>
                </div>
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-accent-100">
                    {activeFilterCount}
                  </p>
                  <p className="mt-1 truncate text-slate-400">岗位要求</p>
                </div>
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-emerald-100">
                    {featuredModels.length + 1}
                  </p>
                  <p className="mt-1 truncate text-slate-400">热招展位</p>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-8 grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
            <label className="group relative block">
              <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-sm font-medium text-slate-400 transition group-focus-within:text-brand-100">
                搜索
              </span>
              <input
                className="h-14 w-full rounded-full border border-white/10 bg-ink-950/70 pl-20 pr-5 text-sm text-white outline-none shadow-dock backdrop-blur-xl transition duration-200 placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/70 focus:ring-4 focus:ring-brand-300/10"
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder={recruitmentTheme.listSearchPlaceholder}
                type="search"
                value={searchTerm}
              />
            </label>

            {!isDefaultFilters(filters) ? (
              <button
                className="h-12 rounded-full border border-white/10 bg-white/[0.07] px-5 text-sm font-semibold text-slate-100 transition duration-200 hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100 active:scale-[0.98]"
                onClick={clearFilters}
                type="button"
              >
                清空岗位要求
              </button>
            ) : null}
          </div>
        </header>

        <section className="mt-6">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">招聘岗位分类</h2>
              <p className="mt-1 text-sm text-slate-400">
                像逛招聘会一样按技能、薪资、经验和单位淘候选人。
              </p>
            </div>
            <p className="text-sm text-slate-400">
              当前展示{" "}
              <span className="font-semibold text-white">
                {filteredModels.length}
              </span>{" "}
              位候选人，其中 {readyFilteredCount} 位已有入口
            </p>
          </div>

          <FilterPanel
            filters={filters}
            matchingCount={filteredModels.length}
            modelAuthorOptions={modelAuthorOptions}
            onChange={setFilters}
            onClear={clearFilters}
            seriesOptions={seriesOptions}
            totalCount={models.length}
          />
        </section>

        <section className="mt-8">
          <div className="mb-6 grid gap-4 lg:grid-cols-3">
            <FederationRouterCard />
            {featuredModels.length > 0
              ? featuredModels.map((model) => (
                  <div
                    className="animate-soft-rise"
                    key={`featured-${model.id}`}
                  >
                    <ModelCard model={model} />
                  </div>
                ))
              : null}
          </div>

          {filteredModels.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {galleryModels.map((model) => (
                <ModelCard key={model.id} model={model} />
              ))}
            </div>
          ) : (
            <div className="surface-panel rounded-lg px-6 py-16 text-center">
              <img
                alt="模镜"
                className="mx-auto h-16 w-16 rounded-lg object-cover shadow-neon"
                src="/logo.png"
              />
              <p className="mt-5 text-lg font-semibold text-white">
                {recruitmentTheme.noResultTitle}
              </p>
              <p className="mt-2 text-sm text-slate-400">
                {recruitmentTheme.noResultBody}
              </p>
              <button
                className="mt-5 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
                onClick={clearFilters}
                type="button"
              >
                重新逛展
              </button>
            </div>
          )}
        </section>

        <footer className="mt-10 border-t border-white/10 py-6 text-sm text-slate-500">
          © 2026 模镜 ModelMirror
        </footer>
    </PageContainer>
  );
}
