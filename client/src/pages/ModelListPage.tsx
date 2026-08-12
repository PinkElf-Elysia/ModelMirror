import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import FederationRouterCard from "../components/FederationRouterCard";
import ModelCompareTray from "../components/ModelCompareTray";
import ModelCompareView from "../components/ModelCompareView";
import ModelCard, {
  type AudioCapabilityStatus,
} from "../components/ModelCard";
import PageContainer from "../components/PageContainer";
import FilterPanel from "../components/filters/FilterPanel";
import {
  defaultFilterState,
  type ModelFilterState,
} from "../data/filterState";
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
import { recruitmentTheme } from "../theme/recruitmentTheme";
import {
  deriveProviderFromModel,
  providerFilterMatches,
} from "../utils/userFriendlyText";

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

function createDefaultFilters(): ModelFilterState {
  return {
    ...defaultFilterState,
    inputModalities: [],
    contextRange: { ...defaultFilterState.contextRange },
    promptPriceCnyRange: { ...defaultFilterState.promptPriceCnyRange },
    series: [],
    jobCapabilities: [],
    supportedParameters: [],
    modelAuthors: [],
  };
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
  const [videoCatalog, setVideoCatalog] =
    useState<VideoCatalogPayload | null>(null);
  const [audioCatalog, setAudioCatalog] =
    useState<AudioCatalogPayload | null>(null);
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
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAudioCatalog(null);
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

      if (
        !filters.showInactive &&
        model.catalog_status === "expired"
      ) {
        return false;
      }
      if (!providerFilterMatches(model, filters.provider)) {
        return false;
      }
      if (!matchesWorkSkills(model, filters.inputModalities)) {
        return false;
      }
      if (!matchesAny(model.series, filters.series)) return false;
      if (
        !includesEvery(
          model.job_capabilities,
          filters.jobCapabilities,
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

      const usesExplicitPriceFilter =
        filters.promptPriceCnyRange.min !==
          defaultFilterState.promptPriceCnyRange.min ||
        filters.promptPriceCnyRange.max !==
          defaultFilterState.promptPriceCnyRange.max;
      if (model.pricing_status === "dynamic") {
        if (usesExplicitPriceFilter) return false;
      } else {
        const inputPriceCny = model.price_cny.input;
        if (inputPriceCny < filters.promptPriceCnyRange.min) return false;
        if (
          filters.promptPriceCnyRange.max <
            defaultFilterState.promptPriceCnyRange.max &&
          inputPriceCny > filters.promptPriceCnyRange.max
        ) {
          return false;
        }
      }

      return true;
    });
  }, [filters, searchTerm]);

  function clearFilters() {
    setFilters(createDefaultFilters());
    setSearchTerm("");
  }

  const onsiteModels = models.filter(
    (model) => model.catalog_counted,
  );
  const onsiteFilteredModels = filteredModels.filter(
    (model) =>
      model.catalog_counted && model.catalog_status !== "expired",
  );
  const adaptedFilteredCount = onsiteFilteredModels.filter(
    (model) => {
      const audioStatus = audioCapabilityStatuses.get(model.id);
      const primaryAudioOperation =
        model.primary_operation === "transcribe" ||
        model.primary_operation === "synthesize_speech" ||
        model.primary_operation === "generate_audio" ||
        model.primary_operation === "realtime_voice";
      if (primaryAudioOperation && audioStatus) {
        return Boolean(adaptedAudioOperations.get(model.id)?.length);
      }
      return (
        model.interaction_status === "ready" ||
        Boolean(confirmedAudioOperations.get(model.id)?.length) ||
        Boolean(confirmedImageOperations.get(model.id)?.length) ||
        Boolean(
          confirmedVideoOperations
            .get(model.id)
            ?.some(
              (operation) =>
                operation === "analyze_video" ||
                operation === "generate_video",
            ),
        )
      );
    },
  ).length;
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
  const featuredModels = filteredModels.slice(0, 2);
  const galleryModels = filteredModels.slice(featuredModels.length);
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
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">资源分区</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            浏览模型能力，并进入当前已适配的对话或资料库入口。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">已完成适配</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {adaptedFilteredCount} / {onsiteFilteredModels.length}
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              是否已启用，请以模型卡片状态为准。
            </p>
          </div>
        </div>
      }
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
            seriesOptions={seriesOptions}
          />
        </section>

        <section className="mt-5">
          {compareState.active ? (
            <ModelCompareView
              models={selectedCompareModels}
              onBack={() => setCompareSelection(compareState.ids)}
              onRemove={(modelId) => toggleCompare(modelId, false)}
            />
          ) : (
          <>
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

          {filteredModels.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {galleryModels.map((model) => (
                <ModelCard
                  audioCatalogStale={audioCatalog?.stale ?? false}
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
