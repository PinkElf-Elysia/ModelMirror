import {
  ARTIFICIAL_ANALYSIS_RANGE_LIMIT,
  DESIGN_ARENA_RANGE_LIMIT,
  MODEL_AGE_DAYS_LIMIT,
  OUTPUT_PRICE_USD_LIMIT,
  PROMPT_PRICE_USD_LIMIT,
  type RangeValue,
} from "./filterOptions";
import {
  type InputModality,
  type JobCapability,
  type SupportedParameter,
} from "./models";
import {
  OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS,
  OPENROUTER_DESIGN_ARENA_METRICS,
  type OpenRouterArtificialAnalysisMetric,
  type OpenRouterDesignArenaMetric,
  type OpenRouterMarketCategory,
  type OpenRouterMarketSeries,
  type OpenRouterRegion,
} from "./openRouterMarket";

export type DistillableFilter = "all" | "yes" | "no";

export interface ModelFilterState {
  inputModalities: InputModality[];
  discounted: boolean;
  minContextLength: number;
  promptPriceUsdRange: RangeValue;
  outputPriceUsdRange: RangeValue;
  series: OpenRouterMarketSeries[];
  jobCapabilities: JobCapability[];
  openRouterCategories: OpenRouterMarketCategory[];
  supportedParameters: SupportedParameter[];
  distillable: DistillableFilter;
  zeroDataRetention: boolean;
  regions: OpenRouterRegion[];
  providers: string[];
  modelAuthors: string[];
  modelAgeDaysRange: RangeValue;
  minToolSuccessRate: number;
  artificialAnalysisRanges: Record<
    OpenRouterArtificialAnalysisMetric,
    RangeValue
  >;
  designArenaRanges: Record<OpenRouterDesignArenaMetric, RangeValue>;
  showInactive: boolean;
}

function createMetricRanges<T extends string>(
  metrics: readonly T[],
  limit: RangeValue,
): Record<T, RangeValue> {
  return Object.fromEntries(
    metrics.map((metric) => [metric, { ...limit }]),
  ) as Record<T, RangeValue>;
}

export const defaultFilterState: ModelFilterState = {
  inputModalities: [],
  discounted: false,
  minContextLength: 0,
  promptPriceUsdRange: { ...PROMPT_PRICE_USD_LIMIT },
  outputPriceUsdRange: { ...OUTPUT_PRICE_USD_LIMIT },
  series: [],
  jobCapabilities: [],
  openRouterCategories: [],
  supportedParameters: [],
  distillable: "all",
  zeroDataRetention: false,
  regions: [],
  providers: [],
  modelAuthors: [],
  modelAgeDaysRange: { ...MODEL_AGE_DAYS_LIMIT },
  minToolSuccessRate: 0,
  artificialAnalysisRanges: createMetricRanges(
    OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS,
    ARTIFICIAL_ANALYSIS_RANGE_LIMIT,
  ),
  designArenaRanges: createMetricRanges(
    OPENROUTER_DESIGN_ARENA_METRICS,
    DESIGN_ARENA_RANGE_LIMIT,
  ),
  showInactive: false,
};

function rangeIsDefault(
  selected: RangeValue,
  baseline: RangeValue,
): boolean {
  return selected.min === baseline.min && selected.max === baseline.max;
}

function rangeToString(range: RangeValue): string {
  return `${range.min}-${range.max}`;
}

function parseRange(value: string, fallback: RangeValue): RangeValue {
  const [minRaw, maxRaw] = value.split("-");
  const min = Number(minRaw);
  const max = Number(maxRaw);
  if (Number.isFinite(min) && Number.isFinite(max)) {
    return { min, max };
  }
  return { ...fallback };
}

/**
 * 把 ModelFilterState 序列化为紧凑 URL 参数对象，只保留非默认值。
 * 返回的键直接作为查询参数（如 `f=...`），避免 URL 过长。
 */
export function serializeFilters(filters: ModelFilterState): Record<string, string> {
  const out: Record<string, string> = {};
  const d = defaultFilterState;

  if (filters.inputModalities.length > 0) {
    out.mod = filters.inputModalities.join(",");
  }
  if (filters.series.length > 0) out.series = filters.series.join(",");
  if (filters.jobCapabilities.length > 0) {
    out.jobs = filters.jobCapabilities.join(",");
  }
  if (filters.openRouterCategories.length > 0) {
    out.cat = filters.openRouterCategories.join(",");
  }
  if (filters.supportedParameters.length > 0) {
    out.params = filters.supportedParameters.join(",");
  }
  if (filters.providers.length > 0) out.providers = filters.providers.join(",");
  if (filters.modelAuthors.length > 0) out.authors = filters.modelAuthors.join(",");
  if (filters.regions.length > 0) out.regions = filters.regions.join(",");
  if (filters.discounted) out.discounted = "1";
  if (filters.zeroDataRetention) out.retention = "1";
  if (filters.showInactive) out.inactive = "1";
  if (filters.distillable !== d.distillable) out.distillable = filters.distillable;
  if (filters.minContextLength !== d.minContextLength) {
    out.minctx = String(filters.minContextLength);
  }
  if (filters.minToolSuccessRate !== d.minToolSuccessRate) {
    out.toolsr = String(filters.minToolSuccessRate);
  }
  if (!rangeIsDefault(filters.promptPriceUsdRange, d.promptPriceUsdRange)) {
    out.pricein = rangeToString(filters.promptPriceUsdRange);
  }
  if (!rangeIsDefault(filters.outputPriceUsdRange, d.outputPriceUsdRange)) {
    out.priceout = rangeToString(filters.outputPriceUsdRange);
  }
  if (!rangeIsDefault(filters.modelAgeDaysRange, d.modelAgeDaysRange)) {
    out.age = rangeToString(filters.modelAgeDaysRange);
  }
  // 基准指标区间：只序列化非默认的 metric，格式 metric:min-max 逗号连接。
  const serializeMetricRanges = <T extends string>(
    selected: Record<T, RangeValue>,
    baseline: Record<T, RangeValue>,
    keys: readonly T[],
  ): string => {
    const parts = keys
      .filter((key) => !rangeIsDefault(selected[key], baseline[key]))
      .map((key) => `${key}:${rangeToString(selected[key])}`);
    return parts.join(",");
  };
  const aaParts = serializeMetricRanges(
    filters.artificialAnalysisRanges,
    d.artificialAnalysisRanges,
    OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS,
  );
  if (aaParts) out.aa = aaParts;
  const daParts = serializeMetricRanges(
    filters.designArenaRanges,
    d.designArenaRanges,
    OPENROUTER_DESIGN_ARENA_METRICS,
  );
  if (daParts) out.da = daParts;
  return out;
}

/**
 * 从 URL 查询参数还原 ModelFilterState。缺失的字段回落到默认值。
 */
export function deserializeFilters(
  params: URLSearchParams,
  fallback: ModelFilterState,
): ModelFilterState {
  const d = defaultFilterState;
  const split = (key: string): string[] =>
    (params.get(key) ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

  return {
    ...fallback,
    inputModalities: split("mod") as ModelFilterState["inputModalities"],
    series: split("series") as ModelFilterState["series"],
    jobCapabilities: split("jobs") as ModelFilterState["jobCapabilities"],
    openRouterCategories: split("cat") as ModelFilterState["openRouterCategories"],
    supportedParameters: split("params") as ModelFilterState["supportedParameters"],
    providers: split("providers") as ModelFilterState["providers"],
    modelAuthors: split("authors") as ModelFilterState["modelAuthors"],
    regions: split("regions") as ModelFilterState["regions"],
    discounted: params.get("discounted") === "1",
    zeroDataRetention: params.get("retention") === "1",
    showInactive: params.get("inactive") === "1",
    distillable:
      (params.get("distillable") as ModelFilterState["distillable"]) ?? d.distillable,
    minContextLength: Number(params.get("minctx")) || d.minContextLength,
    minToolSuccessRate: Number(params.get("toolsr")) || d.minToolSuccessRate,
    promptPriceUsdRange: params.get("pricein")
      ? parseRange(params.get("pricein")!, d.promptPriceUsdRange)
      : { ...fallback.promptPriceUsdRange },
    outputPriceUsdRange: params.get("priceout")
      ? parseRange(params.get("priceout")!, d.outputPriceUsdRange)
      : { ...fallback.outputPriceUsdRange },
    modelAgeDaysRange: params.get("age")
      ? parseRange(params.get("age")!, d.modelAgeDaysRange)
      : { ...fallback.modelAgeDaysRange },
    artificialAnalysisRanges: deserializeMetricRanges(
      params.get("aa"),
      fallback.artificialAnalysisRanges,
      d.artificialAnalysisRanges,
      OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS,
    ),
    designArenaRanges: deserializeMetricRanges(
      params.get("da"),
      fallback.designArenaRanges,
      d.designArenaRanges,
      OPENROUTER_DESIGN_ARENA_METRICS,
    ),
  };
}

function deserializeMetricRanges<T extends string>(
  raw: string | null,
  fallbackRanges: Record<T, RangeValue>,
  baselineRanges: Record<T, RangeValue>,
  keys: readonly T[],
): Record<T, RangeValue> {
  if (!raw) return { ...fallbackRanges };
  const result: Record<T, RangeValue> = { ...fallbackRanges };
  for (const part of raw.split(",")) {
    const [metric, range] = part.split(":");
    if (!metric || !range) continue;
    if (!keys.includes(metric as T)) continue;
    result[metric as T] = parseRange(range, baselineRanges[metric as T]);
  }
  return result;
}
