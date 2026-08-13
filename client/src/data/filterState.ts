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
