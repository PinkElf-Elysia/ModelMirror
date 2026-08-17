import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import FederationRouterCard from "../components/FederationRouterCard";
import ModelCompareTray from "../components/ModelCompareTray";
import ModelCompareView from "../components/ModelCompareView";
import ModelCard, {
  type AudioCapabilityStatus,
} from "../components/ModelCard";
import ModelWorkbenchSidebar from "../components/ModelWorkbenchSidebar";
import PageContainer from "../components/PageContainer";
import FilterPanel from "../components/filters/FilterPanel";
import {
  defaultFilterState,
  type ModelFilterState,
} from "../data/filterState";
import {
  ARTIFICIAL_ANALYSIS_RANGE_LIMIT,
  DESIGN_ARENA_RANGE_LIMIT,
  MODEL_AGE_DAYS_LIMIT,
  OUTPUT_PRICE_USD_LIMIT,
  PROMPT_PRICE_USD_LIMIT,
  modelAuthorOptions,
  seriesOptions as openRouterSeriesOptions,
} from "../data/filterOptions";
import {
  deriveFileSurfaceSummary,
  fetchFileCapabilities,
  type FileCapabilitiesResponse,
} from "../data/fileCapabilities";
import {
  parseModelCompareState,
  updateModelCompareParams,
} from "../data/modelCompareState";
import {
  models,
  type InputModality,
  type Model,
  type ModelOperation,
} from "../data/models";
import {
  OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS,
  OPENROUTER_DESIGN_ARENA_METRICS,
} from "../data/openRouterMarket";
import { recruitmentTheme } from "../theme/recruitmentTheme";
import {
  deriveProviderFromModel,
} from "../utils/userFriendlyText";

function formatCompactContext(contextLength: number) {
  if (contextLength >= 1_000_000) {
    return `${(contextLength / 1_000_000).toFixed(0)}M`;
  }
  return `${Math.round(contextLength / 1000)}K`;
}

function includesEvery<T>(values: T[], selected: T[]) {
  return selected.every((value) => values.includes(value));
}

function matchesWorkSkills(
  model: Model,
  selectedSkills: InputModality[],
) {
  return selectedSkills.every((skill) =>
    model.input_modalities.includes(skill),
  );
}

function matchesAny<T>(value: T, selected: T[]) {
  return selected.length === 0 || selected.includes(value);
}

function matchesFacet<T>(values: T[], selected: T[]) {
  return selected.length === 0 || selected.some((value) => values.includes(value));
}

function isExplicitRange(
  value: { min: number; max: number },
  limit: { min: number; max: number },
) {
  return value.min !== limit.min || value.max !== limit.max;
}

function matchesRange(
  value: number | null | undefined,
  selected: { min: number; max: number },
  limit: { min: number; max: number },
) {
  if (!isExplicitRange(selected, limit)) return true;
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return false;
  }
  if (value < selected.min) return false;
  return selected.max >= limit.max || value <= selected.max;
}

function createDefaultFilters(): ModelFilterState {
  return {
    ...defaultFilterState,
    inputModalities: [],
    promptPriceUsdRange: { ...defaultFilterState.promptPriceUsdRange },
    outputPriceUsdRange: { ...defaultFilterState.outputPriceUsdRange },
    modelAgeDaysRange: { ...defaultFilterState.modelAgeDaysRange },
    series: [],
    jobCapabilities: [],
    openRouterCategories: [],
    supportedParameters: [],
    providers: [],
    modelAuthors: [],
    artificialAnalysisRanges: Object.fromEntries(
      Object.entries(defaultFilterState.artificialAnalysisRanges).map(
        ([metric, range]) => [metric, { ...range }],
      ),
    ) as ModelFilterState["artificialAnalysisRanges"],
    designArenaRanges: Object.fromEntries(
      Object.entries(defaultFilterState.designArenaRanges).map(
        ([metric, range]) => [metric, { ...range }],
      ),
    ) as ModelFilterState["designArenaRanges"],
  };
}

export function shouldShowFeaturedRecommendations(
  filters: ModelFilterState,
  searchTerm: string,
) {
  const isDefaultRange = (
    selected: { min: number; max: number },
    baseline: { min: number; max: number },
  ) => selected.min === baseline.min && selected.max === baseline.max;
  const hasDefaultMetricRanges = <T extends string>(
    selected: Record<T, { min: number; max: number }>,
    baseline: Record<T, { min: number; max: number }>,
  ) =>
    Object.entries(baseline).every(([metric, range]) => {
      const selectedRange = selected[metric as T];
      const baselineRange = range as { min: number; max: number };
      return selectedRange && isDefaultRange(selectedRange, baselineRange);
    });

  return (
    searchTerm.trim() === "" &&
    filters.inputModalities.length === 0 &&
    filters.series.length === 0 &&
    filters.jobCapabilities.length === 0 &&
    filters.openRouterCategories.length === 0 &&
    filters.supportedParameters.length === 0 &&
    filters.providers.length === 0 &&
    filters.modelAuthors.length === 0 &&
    filters.regions.length === 0 &&
    filters.discounted === defaultFilterState.discounted &&
    filters.distillable === defaultFilterState.distillable &&
    filters.zeroDataRetention === defaultFilterState.zeroDataRetention &&
    filters.minContextLength === defaultFilterState.minContextLength &&
    filters.minToolSuccessRate === defaultFilterState.minToolSuccessRate &&
    filters.showInactive === defaultFilterState.showInactive &&
    isDefaultRange(
      filters.promptPriceUsdRange,
      defaultFilterState.promptPriceUsdRange,
    ) &&
    isDefaultRange(
      filters.outputPriceUsdRange,
      defaultFilterState.outputPriceUsdRange,
    ) &&
    isDefaultRange(
      filters.modelAgeDaysRange,
      defaultFilterState.modelAgeDaysRange,
    ) &&
    hasDefaultMetricRanges(
      filters.artificialAnalysisRanges,
      defaultFilterState.artificialAnalysisRanges,
    ) &&
    hasDefaultMetricRanges(
      filters.designArenaRanges,
      defaultFilterState.designArenaRanges,
    )
  );
}

interface VideoModelProfile {
  model_id: string;
  operation: "analyze_video" | "generate_video";
  interaction_status: "ready" | "planned" | "unsupported";
  verification_entry_enabled?: boolean;
  operation_readiness?: OperationReadiness[];
}

interface VideoCatalogPayload {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: VideoModelProfile[];
}

interface AudioModelProfile {
  model_id: string;
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
  operations: ModelOperation[];
  price_per_generation_usd: number | null;
  fixed_duration_seconds: number | null;
  chat_modes: (
    | "direct_audio_input"
    | "native_streaming_audio_output"
    | "transcribe"
    | "synthesize_speech"
  )[];
  operation_readiness?: OperationReadiness[];
}

interface OperationReadiness {
  operation: ModelOperation;
  interaction_status: "ready" | "planned" | "disabled";
  availability_status:
    | "available"
    | "needs_configuration"
    | "verification_required"
    | "upstream_unavailable"
    | "disabled";
  verification_status:
    | "verified"
    | "contract_verified"
    | "manual_required"
    | "failed"
    | "not_applicable";
  support_level: "native" | "converted" | "combined" | "fallback";
  status_reason: string | null;
}

interface AudioCatalogPayload {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: AudioModelProfile[];
}

interface ImageModelProfile {
  model_id: string;
  operation: "analyze_image" | "generate_image";
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  operation_readiness?: OperationReadiness[];
}

interface ImageCatalogPayload {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: ImageModelProfile[];
}

interface GeneralCatalogPayload {
  models: Array<{
    profile_id: string;
    invocation_id: string;
    root: string | null;
    invocable: boolean;
  }>;
  routes: Array<{
    id: string;
    invocable: boolean;
  }>;
}

interface RuntimeEnvironmentSummary {
  model_gateway_ready: boolean;
}

interface ModelMarketHeroProps {
  onsiteCount: number;
  searchTerm: string;
  usableCount: number | null;
  onSearchChange: (value: string) => void;
}

export function ModelMarketHero({
  onsiteCount,
  searchTerm,
  usableCount,
  onSearchChange,
}: ModelMarketHeroProps) {
  return (
    <header className="relative border-y border-white/10 py-4 sm:py-5">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.7),rgba(103,232,249,0.38),transparent)]" />
      <div className="pointer-events-none absolute right-4 top-4 hidden h-14 w-28 opacity-40 sm:block">
        <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-cyan-300" />
        <span className="absolute right-8 top-5 h-1 w-1 rounded-full bg-hire-300" />
        <span className="absolute right-16 top-1 h-1 w-1 rounded-full bg-cyan-300/70" />
        <span className="absolute right-1 top-1 h-px w-16 origin-right -rotate-[18deg] bg-cyan-300/30" />
        <span className="absolute right-8 top-5 h-px w-12 origin-right rotate-[28deg] bg-hire-300/25" />
      </div>

      <div className="relative flex min-w-0 flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span aria-hidden="true" className="h-3 w-3 rounded-[2px] bg-hire-300 shadow-[0_0_18px_rgba(251,146,60,0.35)]" />
            <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              {recruitmentTheme.eventTitle}
            </h1>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
            按输入能力与任务筛选模型，确认状态后直接调用。
          </p>
        </div>
        <p aria-label="模型市场状态" className="shrink-0 text-xs text-slate-400">
          <span className="font-semibold text-slate-100">{onsiteCount}</span> 个模型
          <span aria-hidden="true" className="mx-2 text-hire-300/70">/</span>
          {usableCount === null ? (
            <span className="font-medium text-cyan-100">可调用数待确认</span>
          ) : (
            <>
              <span className="font-semibold text-cyan-100">{usableCount}</span> 可直接调用
            </>
          )}
        </p>
      </div>

      <div className="relative mt-4">
        <label className="group relative block">
          <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-sm font-medium text-slate-400 transition group-focus-within:text-brand-100">
            搜索
          </span>
          <input
            className="h-12 w-full rounded-lg border border-white/10 bg-ink-950/70 pl-20 pr-5 text-sm text-white outline-none shadow-dock backdrop-blur-xl transition duration-200 placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/70 focus:ring-4 focus:ring-brand-300/10"
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder={recruitmentTheme.listSearchPlaceholder}
            type="search"
            value={searchTerm}
          />
        </label>
      </div>
    </header>
  );
}

export default function ModelListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] =
    useState<ModelFilterState>(createDefaultFilters);
  const [searchTerm, setSearchTerm] = useState("");
  const [viewMode, setViewMode] = useState<"card" | "list">(
    searchParams.get("view") === "list" ? "list" : "card",
  );
  const [visibleCount, setVisibleCount] = useState(12);
  const [videoCatalog, setVideoCatalog] =
    useState<VideoCatalogPayload | null>(null);
  const [audioCatalog, setAudioCatalog] =
    useState<AudioCatalogPayload | null>(null);
  const [audioCatalogLoading, setAudioCatalogLoading] = useState(true);
  const [imageCatalog, setImageCatalog] =
    useState<ImageCatalogPayload | null>(null);
  const [generalCatalog, setGeneralCatalog] =
    useState<GeneralCatalogPayload | null>(null);
  const [fileCapabilities, setFileCapabilities] =
    useState<FileCapabilitiesResponse | null>(null);
  const [runtimeEnvironment, setRuntimeEnvironment] =
    useState<RuntimeEnvironmentSummary | null>(null);

  useEffect(() => {
    document.title = "模镜 - AI 牛马招聘会";
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void fetch("/api/multimodal/video/models", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("video catalog unavailable");
        }
        return (await response.json()) as VideoCatalogPayload;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setVideoCatalog(payload);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setVideoCatalog(null);
        }
      });

    void fetch("/api/multimodal/audio/models", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("audio catalog unavailable");
        }
        return (await response.json()) as AudioCatalogPayload;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setAudioCatalog(payload);
          setAudioCatalogLoading(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAudioCatalog(null);
          setAudioCatalogLoading(false);
        }
      });

    void fetch("/api/multimodal/image/models", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("image catalog unavailable");
        }
        return (await response.json()) as ImageCatalogPayload;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setImageCatalog(payload);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setImageCatalog(null);
        }
      });

    void fetch("/api/models/catalog", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("model catalog unavailable");
        }
        return (await response.json()) as GeneralCatalogPayload;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setGeneralCatalog(payload);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setGeneralCatalog(null);
        }
      });

    void fetch("/api/runtime/environment-summary", {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("runtime environment unavailable");
        }
        return (await response.json()) as RuntimeEnvironmentSummary;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setRuntimeEnvironment(payload);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setRuntimeEnvironment(null);
        }
      });

    void fetchFileCapabilities(controller.signal).then((payload) => {
      if (!controller.signal.aborted) {
        setFileCapabilities(payload);
      }
    });

    return () => controller.abort();
  }, []);

  const fileSurfaceSummary = useMemo(
    () => deriveFileSurfaceSummary(fileCapabilities),
    [fileCapabilities],
  );

  const confirmedVideoOperations = useMemo(() => {
    const result = new Map<string, ModelOperation[]>();
    for (const profile of videoCatalog?.profiles ?? []) {
      const readiness = profile.operation_readiness?.find(
        (item) => item.operation === profile.operation,
      );
      if (
        readiness
          ? readiness.interaction_status !== "ready" ||
            readiness.availability_status !== "available"
          : profile.interaction_status !== "ready"
      ) {
        continue;
      }
      const current = result.get(profile.model_id) ?? [];
      if (!current.includes(profile.operation)) {
        current.push(profile.operation);
      }
      result.set(profile.model_id, current);
    }
    return result;
  }, [videoCatalog]);

  const verificationVideoOperations = useMemo(() => {
    const result = new Map<string, ModelOperation[]>();
    for (const profile of videoCatalog?.profiles ?? []) {
      if (
        !profile.verification_entry_enabled ||
        profile.operation !== "generate_video"
      ) {
        continue;
      }
      result.set(profile.model_id, [profile.operation]);
    }
    return result;
  }, [videoCatalog]);

  const confirmedAudioOperations = useMemo(() => {
    const result = new Map<string, ModelOperation[]>();
    for (const profile of audioCatalog?.profiles ?? []) {
      const readiness = profile.operation_readiness ?? [];
      const operations = readiness
        .filter(
          (item) =>
            item.interaction_status === "ready" &&
            item.availability_status === "available" &&
            profile.invocable,
        )
        .map((item) => item.operation);
      if (
        readiness.length === 0 &&
        profile.invocable &&
        profile.interaction_status === "ready"
      ) {
        if (profile.chat_modes.includes("direct_audio_input")) {
          operations.push("analyze_audio");
        }
        if (profile.chat_modes.includes("transcribe")) {
          operations.push("transcribe");
        }
        if (profile.chat_modes.includes("synthesize_speech")) {
          operations.push("synthesize_speech");
        }
        operations.push(
          ...profile.operations.filter(
            (operation) =>
              operation === "generate_audio" ||
              operation === "realtime_voice",
          ),
        );
      }
      if (operations.length > 0) {
        const current = result.get(profile.model_id) ?? [];
        result.set(
          profile.model_id,
          Array.from(new Set([...current, ...operations])),
        );
      }
    }
    return result;
  }, [audioCatalog]);

  const adaptedAudioOperations = useMemo(() => {
    const result = new Map<string, ModelOperation[]>();
    for (const profile of audioCatalog?.profiles ?? []) {
      const readiness = profile.operation_readiness ?? [];
      const operations = readiness
        .filter((item) => item.interaction_status === "ready")
        .map((item) => item.operation);
      if (readiness.length === 0 && profile.interaction_status === "ready") {
        operations.push(...profile.operations);
      }
      if (operations.length > 0) {
        result.set(
          profile.model_id,
          Array.from(
            new Set([...(result.get(profile.model_id) ?? []), ...operations]),
          ),
        );
      }
    }
    return result;
  }, [audioCatalog]);

  const confirmedImageOperations = useMemo(() => {
    const result = new Map<string, ModelOperation[]>();
    for (const profile of imageCatalog?.profiles ?? []) {
      const readiness = profile.operation_readiness?.find(
        (item) => item.operation === profile.operation,
      );
      if (
        !profile.invocable ||
        (readiness
          ? readiness.interaction_status !== "ready" ||
            readiness.availability_status !== "available"
          : profile.interaction_status !== "ready")
      ) {
        continue;
      }
      const current = result.get(profile.model_id) ?? [];
      if (!current.includes(profile.operation)) {
        current.push(profile.operation);
      }
      result.set(profile.model_id, current);
    }
    return result;
  }, [imageCatalog]);

  const audioCapabilityStatuses = useMemo(() => {
    const result = new Map<string, AudioCapabilityStatus>();
    for (const profile of audioCatalog?.profiles ?? []) {
      const operations = profile.operations.filter(
        (operation) =>
          operation === "analyze_audio" ||
          operation === "transcribe" ||
          operation === "synthesize_speech" ||
          operation === "generate_audio" ||
          operation === "realtime_voice",
      );
      if (operations.length === 0) {
        continue;
      }
      result.set(profile.model_id, {
        status: profile.interaction_status,
        operations,
        adaptedOperations:
          (profile.operation_readiness ?? []).length > 0
            ? (profile.operation_readiness ?? [])
                .filter((item) => item.interaction_status === "ready")
                .map((item) => item.operation)
            : profile.interaction_status === "ready"
              ? profile.operations
              : [],
        availabilityStatus:
          (profile.operation_readiness ?? []).find(
            (item) => item.operation === operations[0],
          )?.availability_status ?? null,
        reason: profile.status_reason,
        pricePerGenerationUsd: profile.price_per_generation_usd,
        fixedDurationSeconds: profile.fixed_duration_seconds,
      });
    }
    return result;
  }, [audioCatalog]);

  const invocableModelIds = useMemo(() => {
    const result = new Set<string>();
    for (const candidate of generalCatalog?.models ?? []) {
      if (!candidate.invocable) continue;
      result.add(candidate.profile_id);
      result.add(candidate.invocation_id);
      if (candidate.root) result.add(candidate.root);
    }
    for (const route of generalCatalog?.routes ?? []) {
      if (route.invocable) result.add(route.id);
    }
    return result;
  }, [generalCatalog]);

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
          model.openrouter_market.series,
          model.openrouter_market.author,
          model.description,
          ...model.tags,
          ...model.openrouter_market.providers,
          ...model.openrouter_market.categories,
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedSearch);

      if (!matchesSearch) {
        return false;
      }

      if (
        !filters.showInactive &&
        model.catalog_status === "expired"
      ) {
        return false;
      }
      if (!matchesWorkSkills(model, filters.inputModalities)) {
        return false;
      }
      if (!matchesAny(model.openrouter_market.series, filters.series)) {
        return false;
      }
      if (
        !includesEvery(
          model.job_capabilities,
          filters.jobCapabilities,
        )
      ) return false;
      if (
        !matchesFacet(
          model.openrouter_market.categories,
          filters.openRouterCategories,
        )
      ) return false;
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
        !filters.modelAuthors.includes(model.openrouter_market.author)
      ) {
        return false;
      }
      if (
        !matchesFacet(model.openrouter_market.providers, filters.providers)
      ) {
        return false;
      }
      if (filters.discounted && !model.openrouter_market.discounted) {
        return false;
      }
      if (
        filters.distillable === "yes" &&
        !model.openrouter_market.distillable
      ) {
        return false;
      }
      if (
        filters.distillable === "no" &&
        model.openrouter_market.distillable
      ) {
        return false;
      }
      if (
        filters.zeroDataRetention &&
        !model.openrouter_market.zero_data_retention
      ) return false;
      if (!matchesFacet(model.openrouter_market.regions, filters.regions)) {
        return false;
      }
      if (model.context_length < filters.minContextLength) return false;

      if (
        isExplicitRange(filters.promptPriceUsdRange, PROMPT_PRICE_USD_LIMIT) ||
        isExplicitRange(filters.outputPriceUsdRange, OUTPUT_PRICE_USD_LIMIT)
      ) {
        if (model.pricing_status === "dynamic") return false;
        if (
          !matchesRange(
            model.pricing.input,
            filters.promptPriceUsdRange,
            PROMPT_PRICE_USD_LIMIT,
          ) ||
          !matchesRange(
            model.pricing.output,
            filters.outputPriceUsdRange,
            OUTPUT_PRICE_USD_LIMIT,
          )
        ) return false;
      }

      const createdAt = model.openrouter_market.created_at;
      const ageDays =
        createdAt === null
          ? null
          : Math.max(0, (Date.now() / 1000 - createdAt) / 86_400);
      if (!matchesRange(ageDays, filters.modelAgeDaysRange, MODEL_AGE_DAYS_LIMIT)) {
        return false;
      }

      if (filters.minToolSuccessRate > 0) {
        const successRate = model.openrouter_market.tool_call_success_rate;
        if (successRate === null || successRate < filters.minToolSuccessRate) {
          return false;
        }
      }

      for (const metric of OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS) {
        if (
          !matchesRange(
            model.openrouter_market.artificial_analysis[metric],
            filters.artificialAnalysisRanges[metric],
            ARTIFICIAL_ANALYSIS_RANGE_LIMIT,
          )
        ) return false;
      }
      for (const metric of OPENROUTER_DESIGN_ARENA_METRICS) {
        if (
          !matchesRange(
            model.openrouter_market.design_arena[metric],
            filters.designArenaRanges[metric],
            DESIGN_ARENA_RANGE_LIMIT,
          )
        ) return false;
      }

      return true;
    });
  }, [filters, searchTerm]);

  function clearFilters() {
    setFilters(createDefaultFilters());
    setSearchTerm("");
  }

  const activeFilterCount = useMemo(() => {
    let count = 0;
    const f = filters;
    count += f.inputModalities.length;
    count += f.series.length;
    count += f.jobCapabilities.length;
    count += f.openRouterCategories.length;
    count += f.supportedParameters.length;
    count += f.providers.length;
    count += f.modelAuthors.length;
    count += f.regions.length;
    if (f.discounted !== defaultFilterState.discounted) count += 1;
    if (f.distillable !== defaultFilterState.distillable) count += 1;
    if (f.zeroDataRetention !== defaultFilterState.zeroDataRetention) count += 1;
    if (f.showInactive !== defaultFilterState.showInactive) count += 1;
    if (f.minContextLength !== defaultFilterState.minContextLength) count += 1;
    if (f.minToolSuccessRate !== defaultFilterState.minToolSuccessRate) count += 1;
    const rangeActive = (
      selected: { min: number; max: number },
      baseline: { min: number; max: number },
    ) => selected.min !== baseline.min || selected.max !== baseline.max;
    if (rangeActive(f.promptPriceUsdRange, defaultFilterState.promptPriceUsdRange)) count += 1;
    if (rangeActive(f.outputPriceUsdRange, defaultFilterState.outputPriceUsdRange)) count += 1;
    if (rangeActive(f.modelAgeDaysRange, defaultFilterState.modelAgeDaysRange)) count += 1;
    if (searchTerm.trim() !== "") count += 1;
    return count;
  }, [filters, searchTerm]);

  const onsiteModels = models.filter(
    (model) => model.catalog_counted,
  );
  const onsiteFilteredModels = filteredModels.filter(
    (model) =>
      model.catalog_counted && model.catalog_status !== "expired",
  );
  const usableFilteredCount = onsiteFilteredModels.filter(
    (model) =>
      invocableModelIds.has(model.id) ||
      (
        model.active &&
        runtimeEnvironment?.model_gateway_ready === true &&
        model.interaction_status === "ready" &&
        (model.ui_entrypoint === "chat" || model.ui_entrypoint === "rag")
      ) ||
      Boolean(confirmedAudioOperations.get(model.id)?.length) ||
      Boolean(confirmedImageOperations.get(model.id)?.length) ||
      Boolean(confirmedVideoOperations.get(model.id)?.length),
  ).length;
  const usableCountKnown =
    generalCatalog !== null ||
    runtimeEnvironment !== null ||
    videoCatalog !== null ||
    audioCatalog !== null ||
    imageCatalog !== null;
  const showFeaturedRecommendations = shouldShowFeaturedRecommendations(
    filters,
    searchTerm,
  );
  const featuredModels = showFeaturedRecommendations
    ? filteredModels.slice(0, 2)
    : [];
  const galleryModels = showFeaturedRecommendations
    ? filteredModels.slice(featuredModels.length)
    : filteredModels;
  const visibleGalleryModels = galleryModels.slice(0, visibleCount);
  const hasMoreGalleryModels = visibleGalleryModels.length < galleryModels.length;

  function switchView(next: "card" | "list") {
    setViewMode(next);
    setSearchParams(
      next === "list" ? { view: "list" } : {},
      { replace: true },
    );
  }

  const compareState = useMemo(
    () => parseModelCompareState(searchParams),
    [searchParams],
  );
  const selectedCompareModels = useMemo(
    () => compareState.ids
      .map((modelId) => models.find((model) => model.id === modelId))
      .filter((model): model is Model => Boolean(model)),
    [compareState.ids],
  );

  function setCompareSelection(ids: string[], active = false) {
    setSearchParams(updateModelCompareParams(searchParams, ids, active), {
      replace: true,
    });
  }

  function toggleCompare(modelId: string, selected: boolean) {
    const ids = selected
      ? [...compareState.ids, modelId].slice(0, 4)
      : compareState.ids.filter((candidate) => candidate !== modelId);
    setCompareSelection(ids, compareState.active && ids.length >= 2);
  }

  return (
    <PageContainer
      activeResource="models"
      mobileSidebar={<ModelWorkbenchSidebar compact />}
      showSystemCapabilityBar={false}
      sidebar={<ModelWorkbenchSidebar />}
      sidebarGridClassName="xl:grid-cols-[230px_minmax(0,1fr)] xl:gap-x-[54px]"
    >
        <ModelMarketHero
          onsiteCount={onsiteModels.length}
          onSearchChange={setSearchTerm}
          searchTerm={searchTerm}
          usableCount={usableCountKnown ? usableFilteredCount : null}
        />

        <section className="mt-4">
          <FilterPanel
            filters={filters}
            modelAuthorOptions={modelAuthorOptions}
            onChange={setFilters}
            onClear={clearFilters}
            seriesOptions={openRouterSeriesOptions}
          />
        </section>

        {activeFilterCount > 0 ? (
          <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2">
            <span className="text-xs font-semibold text-slate-300">
              已应用 {activeFilterCount} 个条件
            </span>
            <button
              className="rounded-full border border-hire-300/35 bg-hire-300/10 px-2.5 py-0.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
              onClick={clearFilters}
              type="button"
            >
              × 清空全部
            </button>
          </div>
        ) : null}

        <section className="mt-5">
          {compareState.active ? (
            <ModelCompareView
              models={selectedCompareModels}
              onBack={() => setCompareSelection(compareState.ids)}
              onRemove={(modelId) => toggleCompare(modelId, false)}
            />
          ) : (
          <>
          {showFeaturedRecommendations ? (
            <div className="mb-6 grid gap-4 lg:grid-cols-3">
              <FederationRouterCard />
              {featuredModels.length > 0
                ? featuredModels.map((model) => (
                  <div
                    className="animate-soft-rise"
                    key={`featured-${model.id}`}
                  >
                    <ModelCard
                      audioCatalogStale={audioCatalog?.stale ?? false}
                      audioCatalogState={
                        audioCatalogLoading
                          ? "loading"
                          : audioCatalog
                            ? "available"
                            : "unavailable"
                      }
                      audioCapabilityStatus={
                        audioCapabilityStatuses.get(model.id)
                      }
                      confirmedAudioOperations={
                        confirmedAudioOperations.get(model.id)
                      }
                      adaptedAudioOperations={
                        adaptedAudioOperations.get(model.id)
                      }
                      catalogInvocable={invocableModelIds.has(model.id)}
                      confirmedImageOperations={
                        confirmedImageOperations.get(model.id)
                      }
                      confirmedVideoOperations={
                        confirmedVideoOperations.get(model.id)
                      }
                      verificationVideoOperations={
                        verificationVideoOperations.get(model.id)
                      }
                      fileSurfaceSummary={fileSurfaceSummary}
                      featured
                      model={model}
                      compareDisabled={compareState.ids.length >= 4}
                      compareSelected={compareState.ids.includes(model.id)}
                      onCompareChange={toggleCompare}
                      imageCatalogStale={imageCatalog?.stale ?? false}
                      videoCatalogStale={videoCatalog?.stale ?? false}
                    />
                  </div>
                  ))
                : null}
            </div>
          ) : null}

          {filteredModels.length > 0 ? (
            <>
            <div className="mb-4 flex items-center justify-end gap-2">
              <div className="flex rounded-md border border-white/10 bg-white/[0.03] p-0.5" role="group" aria-label="切换视图">
                <button
                  aria-pressed={viewMode === "card"}
                  className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                    viewMode === "card"
                      ? "bg-hire-300 text-ink-950"
                      : "text-slate-400 hover:text-white"
                  }`}
                  onClick={() => switchView("card")}
                  type="button"
                >
                  卡片
                </button>
                <button
                  aria-pressed={viewMode === "list"}
                  className={`rounded px-2.5 py-1 text-xs font-semibold transition ${
                    viewMode === "list"
                      ? "bg-hire-300 text-ink-950"
                      : "text-slate-400 hover:text-white"
                  }`}
                  onClick={() => switchView("list")}
                  type="button"
                >
                  列表
                </button>
              </div>
            </div>

            {viewMode === "list" ? (
              <div className="surface-panel overflow-hidden rounded-lg">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-white/10 bg-white/[0.03] text-xs text-slate-400">
                    <tr>
                      <th className="px-4 py-2.5 font-semibold">模型</th>
                      <th className="px-4 py-2.5 font-semibold">提供商</th>
                      <th className="hidden px-4 py-2.5 font-semibold sm:table-cell">输入薪资</th>
                      <th className="hidden px-4 py-2.5 font-semibold sm:table-cell">输出薪资</th>
                      <th className="hidden px-4 py-2.5 font-semibold md:table-cell">上下文</th>
                      <th className="px-4 py-2.5 font-semibold">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06]">
                    {visibleGalleryModels.map((model) => {
                      const provider = deriveProviderFromModel(model);
                      const canChat =
                        (model.active || invocableModelIds.has(model.id)) &&
                        model.interaction_status === "ready" &&
                        model.ui_entrypoint === "chat";
                      return (
                        <tr className="transition hover:bg-white/[0.03]" key={model.id}>
                          <td className="px-4 py-3">
                            <div className="min-w-0">
                              <Link
                                className={`block truncate font-semibold text-white hover:text-hire-100 ${
                                  canChat ? "" : "pointer-events-none cursor-default text-slate-400"
                                }`}
                                to={canChat ? `/chat/${encodeURIComponent(model.id)}` : "#"}
                              >
                                {model.name}
                              </Link>
                              <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{model.id}</p>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-xs text-slate-300">{provider}</td>
                          <td className="hidden px-4 py-3 text-xs text-slate-300 sm:table-cell">
                            {model.pricing_status === "free"
                              ? "免费"
                              : `¥${model.price_cny.input.toFixed(2)}`}
                          </td>
                          <td className="hidden px-4 py-3 text-xs text-slate-300 sm:table-cell">
                            {model.pricing_status === "free"
                              ? "免费"
                              : `¥${model.price_cny.output.toFixed(2)}`}
                          </td>
                          <td className="hidden px-4 py-3 text-xs text-slate-300 md:table-cell">
                            {formatCompactContext(model.context_length)}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <button
                                aria-label={`${compareState.ids.includes(model.id) ? "移出" : "加入"} ${model.name} 对比`}
                                className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition ${
                                  compareState.ids.includes(model.id)
                                    ? "border-hire-300/50 bg-hire-300/15 text-hire-100"
                                    : "border-white/10 bg-white/[0.04] text-slate-400 hover:border-hire-300/30 hover:text-hire-100"
                                }`}
                                onClick={() => toggleCompare(model.id, !compareState.ids.includes(model.id))}
                                type="button"
                              >
                                {compareState.ids.includes(model.id) ? "已加入" : "加入对比"}
                              </button>
                              {canChat ? (
                                <Link
                                  className="rounded-md bg-hire-300 px-2.5 py-1 text-xs font-semibold text-ink-950 transition hover:bg-hire-200"
                                  to={`/chat/${encodeURIComponent(model.id)}`}
                                >
                                  面试
                                </Link>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {visibleGalleryModels.map((model) => (
                <ModelCard
                  audioCatalogStale={audioCatalog?.stale ?? false}
                  audioCatalogState={
                    audioCatalogLoading
                      ? "loading"
                      : audioCatalog
                        ? "available"
                        : "unavailable"
                  }
                  audioCapabilityStatus={
                    audioCapabilityStatuses.get(model.id)
                  }
                  confirmedAudioOperations={
                    confirmedAudioOperations.get(model.id)
                  }
                  adaptedAudioOperations={
                    adaptedAudioOperations.get(model.id)
                  }
                  catalogInvocable={invocableModelIds.has(model.id)}
                  confirmedImageOperations={
                    confirmedImageOperations.get(model.id)
                  }
                  confirmedVideoOperations={
                    confirmedVideoOperations.get(model.id)
                  }
                  verificationVideoOperations={
                    verificationVideoOperations.get(model.id)
                  }
                  fileSurfaceSummary={fileSurfaceSummary}
                  key={model.id}
                  model={model}
                  compareDisabled={compareState.ids.length >= 4}
                  compareSelected={compareState.ids.includes(model.id)}
                  onCompareChange={toggleCompare}
                  imageCatalogStale={imageCatalog?.stale ?? false}
                  videoCatalogStale={videoCatalog?.stale ?? false}
                />
              ))}
            </div>
            )}
            {hasMoreGalleryModels ? (
              <div className="mt-6 flex justify-center">
                <button
                  className="min-h-11 rounded-lg border border-hire-300/30 bg-hire-300/10 px-5 py-2.5 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20"
                  onClick={() => setVisibleCount((count) => count + 12)}
                  type="button"
                >
                  加载更多（剩余 {galleryModels.length - visibleGalleryModels.length} 个）
                </button>
              </div>
            ) : null}
            </>
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
                {activeFilterCount > 0
                  ? `已应用 ${activeFilterCount} 个筛选条件，没有匹配的模型。`
                  : recruitmentTheme.noResultBody}
              </p>
              <button
                className="mt-5 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
                onClick={clearFilters}
                type="button"
              >
                {activeFilterCount > 0 ? "清空条件，查看全部" : "重新逛展"}
              </button>
            </div>
          )}
          </>
          )}
        </section>
        {!compareState.active ? (
          <ModelCompareTray
            models={selectedCompareModels}
            onClear={() => setCompareSelection([])}
            onCompare={() => setCompareSelection(compareState.ids, true)}
            onRemove={(modelId) => toggleCompare(modelId, false)}
          />
        ) : null}

        <footer className="mt-10 border-t border-white/10 py-6 text-sm text-slate-500">
          © 2026 模镜 ModelMirror
        </footer>
    </PageContainer>
  );
}
