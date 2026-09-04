import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createServer } from "../client/node_modules/vite/dist/node/index.js";
import {
  auditAudioHourPricingOverlays,
  REQUIRED_AUDIO_HOUR_PRICING_OVERLAYS,
} from "./openrouter-pricing-contracts.mjs";

const OPENROUTER_MARKET_URL =
  "https://openrouter.ai/api/frontend/v1/models/find?active=true&fmt=cards";
const OPENROUTER_MARKET_CATEGORIES = [
  "programming",
  "roleplay",
  "marketing",
  "marketing/seo",
  "technology",
  "science",
  "translation",
  "legal",
  "finance",
  "health",
  "trivia",
  "academia",
];
const OPENROUTER_MARKET_SERIES = [
  "GPT", "Claude", "Gemini", "Gemma", "Grok", "Cohere", "Nova",
  "Qwen", "Yi", "DeepSeek", "Mistral", "Llama2", "Llama3", "Llama4",
  "RWKV", "Qwen3", "Router", "Media", "Other", "PaLM",
];
const OPENROUTER_SUPPORTED_PARAMETERS = [
  "tools", "temperature", "top_p", "top_k", "min_p", "top_a",
  "frequency_penalty", "presence_penalty", "repetition_penalty",
  "max_tokens", "max_completion_tokens", "logit_bias", "logprobs",
  "top_logprobs", "prediction", "seed", "response_format",
  "structured_outputs", "stop", "parallel_tool_calls", "include_reasoning",
  "reasoning", "reasoning_effort", "web_search_options", "verbosity",
];
const DESIGN_ARENA_KEYS = {
  "models-codecategories": "code_categories",
  "models-uicomponent": "ui_component",
  "models-gamedev": "game_development",
  "models-dataviz": "data_visualization",
  "models-3d": "3d",
  "models-image": "image",
  "models-video": "video",
  "models-svg": "svg",
};
const MARKET_STRUCTURAL_FIELDS = [
  "series",
  "author",
  "providers",
  "categories",
  "discounted",
  "distillable",
  "zero_data_retention",
  "regions",
  "created_at",
];
const MARKET_VOLATILE_FIELDS = [
  "tool_call_success_rate",
  "artificial_analysis",
  "design_arena",
];

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--")) continue;
    result[argument.slice(2)] = argv[index + 1];
    index += 1;
  }
  return result;
}

function sorted(values) {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function hasExactOptionSet(actual, expected) {
  return JSON.stringify(sorted(new Set(actual))) ===
    JSON.stringify(sorted(new Set(expected)));
}

function countBy(values, getKey) {
  const result = {};
  for (const value of values) {
    const key = getKey(value);
    result[key] = (result[key] ?? 0) + 1;
  }
  return Object.fromEntries(
    Object.entries(result).sort((left, right) =>
      right[1] - left[1] || left[0].localeCompare(right[0]),
    ),
  );
}

function expectedOperations(raw, imageModelIds, videoModelIds) {
  const inputs = new Set(raw.architecture?.input_modalities ?? []);
  const outputs = new Set(raw.architecture?.output_modalities ?? []);
  const operations = new Set();

  if (inputs.has("image") && outputs.has("text")) operations.add("analyze_image");
  if (inputs.has("file") && outputs.has("text")) operations.add("analyze_document");
  if (imageModelIds.has(raw.id)) operations.add("generate_image");
  if (outputs.has("transcription")) operations.add("transcribe");
  if (outputs.has("speech")) operations.add("synthesize_speech");
  if (outputs.has("audio")) operations.add("generate_audio");
  if (videoModelIds.has(raw.id) || outputs.has("video")) operations.add("generate_video");
  if (outputs.has("embeddings")) operations.add("embed");
  if (outputs.has("rerank")) operations.add("rerank");
  if (inputs.has("audio") && outputs.has("text")) operations.add("analyze_audio");
  if (inputs.has("video") && outputs.has("text")) operations.add("analyze_video");
  if (inputs.has("text") && outputs.has("text")) operations.add("chat");

  return sorted(operations.size > 0 ? operations : ["chat"]);
}

function expectedAuthoritativeJobs(raw, operations) {
  const parameters = new Set(raw.supported_parameters ?? []);
  const jobs = new Set();
  const mapping = {
    chat: "text_chat",
    analyze_document: "document_understanding",
    analyze_image: "image_understanding",
    generate_image: "image_generation",
    transcribe: "transcription",
    synthesize_speech: "speech_synthesis",
    generate_audio: "music_generation",
    analyze_audio: "audio_understanding",
    analyze_video: "video_understanding",
    generate_video: "video_generation",
    embed: "embedding",
    rerank: "rerank",
  };
  for (const operation of operations) {
    if (mapping[operation]) jobs.add(mapping[operation]);
  }
  if (parameters.has("tools") || parameters.has("tool_choice")) jobs.add("tool_use");
  return sorted(jobs);
}

function compareSets(actual, expected) {
  const actualSet = new Set(actual);
  const expectedSet = new Set(expected);
  return {
    missing: expected.filter((value) => !actualSet.has(value)),
    extra: actual.filter((value) => !expectedSet.has(value)),
  };
}

function sampleModel(model) {
  return {
    id: model.id,
    name: model.name,
    categories: model.categories,
    job_capabilities: model.job_capabilities,
  };
}

function stripBatchSuffix(value) {
  return String(value ?? "").replace(/:batch$/, "");
}

function categoryFileName(category) {
  return `category-${category.replaceAll("/", "-")}.json`;
}

async function loadCategoryReference(categoryDirectory) {
  if (!categoryDirectory) return null;
  const categoriesByModelId = new Map();
  const responseCounts = {};
  for (const category of OPENROUTER_MARKET_CATEGORIES) {
    const payload = JSON.parse(
      await fs.readFile(
        path.join(path.resolve(categoryDirectory), categoryFileName(category)),
        "utf8",
      ),
    );
    if (!Array.isArray(payload.data)) {
      throw new Error(`OpenRouter category ${category} is missing data[]`);
    }
    responseCounts[category] = payload.data.length;
    for (const model of payload.data) {
      if (!model || typeof model.id !== "string") continue;
      const values = categoriesByModelId.get(model.id) ?? new Set();
      values.add(category);
      categoriesByModelId.set(model.id, values);
    }
  }
  return { categoriesByModelId, responseCounts };
}

function resolveRegion(value) {
  const normalized = String(value ?? "").toLowerCase();
  if (normalized.startsWith("eu")) return "eu";
  if (normalized.startsWith("us")) return "us";
  return null;
}

function expectedMarketSnapshots(marketPayload) {
  const data = marketPayload?.data;
  if (!Array.isArray(data?.models)) {
    throw new Error("OpenRouter market payload is missing data.models[]");
  }
  const snapshots = new Map();
  const aliases = new Map();
  for (const record of data.models) {
    const variantId = record?.endpoint?.model_variant_slug || record?.slug || "";
    const modelId = stripBatchSuffix(variantId);
    if (!modelId) continue;
    const snapshot = snapshots.get(modelId) ?? {
      series: "Other",
      author: "",
      providers: new Set(),
      categories: new Set(),
      discounted: false,
      distillable: false,
      zero_data_retention: false,
      regions: new Set(),
      created_at: null,
      tool_call_success_rate: null,
      artificial_analysis: {},
      design_arena: {},
    };
    snapshots.set(modelId, snapshot);
    if (OPENROUTER_MARKET_SERIES.includes(record.group)) {
      snapshot.series = record.group;
    }
    if (record.author) snapshot.author = String(record.author);
    if (record.endpoint?.provider_name) {
      snapshot.providers.add(String(record.endpoint.provider_name));
    }
    snapshot.discounted ||= Number(record.endpoint?.pricing?.discount ?? 0) > 0;
    snapshot.distillable ||=
      record.is_trainable_text === true || record.is_trainable_image === true;
    snapshot.zero_data_retention ||=
      record.endpoint?.data_policy?.retainsPrompts === false;
    const region = resolveRegion(record.endpoint?.provider_region);
    if (region) snapshot.regions.add(region);
    const createdAt = Date.parse(record.created_at ?? "");
    if (Number.isFinite(createdAt)) {
      const seconds = Math.floor(createdAt / 1000);
      snapshot.created_at =
        snapshot.created_at === null
          ? seconds
          : Math.min(snapshot.created_at, seconds);
    }
    for (const alias of [
      record.slug,
      record.permaslug,
      record.endpoint?.model_variant_slug,
      record.endpoint?.model_variant_permaslug,
      record.endpoint?.model?.slug,
      record.endpoint?.model?.permaslug,
    ]) {
      if (alias) aliases.set(stripBatchSuffix(alias), modelId);
    }
  }
  const resolveModelId = (value) => {
    const normalized = stripBatchSuffix(value);
    return aliases.get(normalized) ?? normalized;
  };
  for (const [sourceId, placements] of Object.entries(data.categories ?? {})) {
    const snapshot = snapshots.get(resolveModelId(sourceId));
    if (!snapshot || !Array.isArray(placements)) continue;
    for (const placement of placements) {
      if (OPENROUTER_MARKET_CATEGORIES.includes(placement?.category)) {
        snapshot.categories.add(placement.category);
      }
    }
  }
  for (const [sourceId, analytics] of Object.entries(data.analytics ?? {})) {
    const snapshot = snapshots.get(resolveModelId(sourceId));
    if (!snapshot) continue;
    const toolCalls = Number(analytics?.total_tool_calls ?? 0);
    const toolCallErrors = Number(
      analytics?.requests_with_tool_call_errors ?? 0,
    );
    if (toolCalls > 0 && toolCallErrors >= 0) {
      snapshot.tool_call_success_rate = Number(
        Math.max(
          0,
          Math.min(100, ((toolCalls - toolCallErrors) / toolCalls) * 100),
        ).toFixed(2),
      );
    }
  }
  for (const [sourceId, benchmark] of Object.entries(data.benchmarks ?? {})) {
    const snapshot = snapshots.get(resolveModelId(sourceId));
    if (!snapshot) continue;
    for (const metric of [
      "intelligence_index",
      "coding_index",
      "agentic_index",
    ]) {
      const value = Number(benchmark?.aa?.[metric]);
      if (Number.isFinite(value)) snapshot.artificial_analysis[metric] = Number(value.toFixed(2));
    }
    for (const [sourceMetric, targetMetric] of Object.entries(DESIGN_ARENA_KEYS)) {
      const value = Number(benchmark?.da?.elo_by_category?.[sourceMetric]);
      if (Number.isFinite(value)) snapshot.design_arena[targetMetric] = Number(value.toFixed(2));
    }
  }
  return {
    sourceRecords: data.models.length,
    snapshots: new Map(
      [...snapshots.entries()].map(([modelId, snapshot]) => [modelId, {
        ...snapshot,
        providers: sorted(snapshot.providers),
        categories: OPENROUTER_MARKET_CATEGORIES.filter((category) =>
          snapshot.categories.has(category),
        ),
        regions: ["eu", "us"].filter((region) => snapshot.regions.has(region)),
      }]),
    ),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.models || !args.images || !args.videos || !args.market) {
    throw new Error(
      "Usage: node scripts/audit-openrouter-classifications.mjs --models <models.json> --images <images.json> --videos <videos.json> --market <models-find.json> [--category-dir <optional-reference-directory>] [--output <audit.json>]",
    );
  }

  const repositoryRoot = path.resolve(import.meta.dirname, "..");
  const clientRoot = path.join(repositoryRoot, "client");
  const [modelsPayload, imagesPayload, videosPayload, marketPayload] = await Promise.all([
    fs.readFile(path.resolve(args.models), "utf8").then(JSON.parse),
    fs.readFile(path.resolve(args.images), "utf8").then(JSON.parse),
    fs.readFile(path.resolve(args.videos), "utf8").then(JSON.parse),
    fs.readFile(path.resolve(args.market), "utf8").then(JSON.parse),
  ]);
  const categoryReference = await loadCategoryReference(args["category-dir"]);
  const marketSource = expectedMarketSnapshots(marketPayload);

  const viteCacheDirectory = await fs.mkdtemp(
    path.join(os.tmpdir(), "modelmirror-openrouter-audit-"),
  );
  const vite = await createServer({
    root: clientRoot,
    cacheDir: viteCacheDirectory,
    configFile: false,
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });

  let localModels;
  let jobCapabilityOptions;
  let marketCategoryOptions;
  let seriesOptions;
  let supportedParameterOptions;
  try {
    ({ models: localModels } = await vite.ssrLoadModule("/src/data/models.ts"));
    ({
      jobCapabilityOptions,
      openRouterCategoryOptions: marketCategoryOptions,
      seriesOptions,
      supportedParameterOptions,
    } = await vite.ssrLoadModule("/src/data/filterOptions.ts"));
  } finally {
    await vite.close();
  }

  const allSourceModels = modelsPayload.data ?? [];
  const batchVariants = allSourceModels.filter((model) => model.id.endsWith(":batch"));
  const sourceModels = allSourceModels.filter((model) => !model.id.endsWith(":batch"));
  const sourceById = new Map(sourceModels.map((model) => [model.id, model]));
  const sourceIds = new Set(sourceById.keys());
  const imageModelIds = new Set((imagesPayload.data ?? []).map((model) => model.id));
  const videoModelIds = new Set((videosPayload.data ?? []).map((model) => model.id));
  const localCatalog = localModels.filter(
    (model) => model.catalog_counted && sourceIds.has(model.id),
  );
  const marketSnapshotMismatches = [];
  for (const model of localCatalog) {
    const expected = marketSource.snapshots.get(model.id);
    if (!expected) continue;
    const actual = model.openrouter_market;
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      marketSnapshotMismatches.push({ id: model.id, actual, expected });
    }
  }

  const categoryReferenceMismatches = [];
  if (categoryReference) {
    for (const [modelId, referenceCategories] of categoryReference.categoriesByModelId) {
      const expected = marketSource.snapshots.get(modelId)?.categories ?? [];
      for (const category of referenceCategories) {
        if (!expected.includes(category)) {
          categoryReferenceMismatches.push({ model_id: modelId, category });
        }
      }
    }
  }

  const authoritativeOperationMismatches = [];
  const authoritativeJobMismatches = [];
  const authoritativeJobNames = new Set([
    "text_chat",
    "tool_use",
    "document_understanding",
    "image_understanding",
    "image_generation",
    "audio_understanding",
    "transcription",
    "speech_synthesis",
    "music_generation",
    "video_understanding",
    "video_generation",
    "embedding",
    "rerank",
  ]);

  for (const model of localCatalog) {
    const source = sourceById.get(model.id);
    const expected = expectedOperations(source, imageModelIds, videoModelIds);
    const operationDiff = compareSets(sorted(model.operations), expected);
    if (operationDiff.missing.length || operationDiff.extra.length) {
      authoritativeOperationMismatches.push({
        id: model.id,
        actual: sorted(model.operations),
        expected,
        ...operationDiff,
      });
    }

    const expectedJobs = expectedAuthoritativeJobs(source, expected);
    const actualJobs = sorted(
      model.job_capabilities.filter((job) => authoritativeJobNames.has(job)),
    );
    const jobDiff = compareSets(actualJobs, expectedJobs);
    if (jobDiff.missing.length || jobDiff.extra.length) {
      authoritativeJobMismatches.push({
        id: model.id,
        actual: actualJobs,
        expected: expectedJobs,
        ...jobDiff,
      });
    }
  }

  const sourceOutputCounts = {};
  const sourceInputCounts = {};
  for (const model of sourceModels) {
    for (const modality of model.architecture?.output_modalities ?? []) {
      sourceOutputCounts[modality] = (sourceOutputCounts[modality] ?? 0) + 1;
    }
    for (const modality of model.architecture?.input_modalities ?? []) {
      sourceInputCounts[modality] = (sourceInputCounts[modality] ?? 0) + 1;
    }
  }

  const semanticCategoryNames = [
    "coding",
    "reasoning",
    "safety",
    "long_context",
  ];
  const semanticCategoryCounts = Object.fromEntries(
    semanticCategoryNames.map((category) => [
      category,
      localCatalog.filter((model) => model.categories.includes(category)).length,
    ]),
  );

  const reasoningModels = localCatalog.filter((model) =>
    model.categories.includes("reasoning"),
  );
  const reasoningWithoutStructuredSignal = reasoningModels.filter((model) => {
    const source = sourceById.get(model.id);
    return !source.reasoning &&
      !(source.supported_parameters ?? []).some((value) =>
        ["reasoning", "include_reasoning", "reasoning_effort"].includes(value),
      );
  });
  const reasoningOnlyFromLooseSubstring = reasoningModels.filter((model) => {
    const source = sourceById.get(model.id);
    const haystack = `${source.id} ${source.name} ${source.description ?? ""}`.toLowerCase();
    const hasStructuredReasoning = (source.supported_parameters ?? []).some((value) =>
      ["reasoning", "include_reasoning", "reasoning_effort"].includes(value),
    );
    const hasExplicitReasoningWord = ["reasoning", "thinking", "math", "qwq"].some(
      (needle) => haystack.includes(needle),
    );
    return !hasStructuredReasoning && !hasExplicitReasoningWord &&
      (haystack.includes("o3") || haystack.includes("o4"));
  });

  const translationWithoutTextOutput = localCatalog.filter((model) => {
    const source = sourceById.get(model.id);
    return model.job_capabilities.includes("translation") &&
      !(source.architecture?.output_modalities ?? []).includes("text");
  });
  const codingWithoutTextOutput = localCatalog.filter((model) => {
    const source = sourceById.get(model.id);
    return model.categories.includes("coding") &&
      !(source.architecture?.output_modalities ?? []).includes("text");
  });

  const dedicatedPaidVideoIds = new Set(
    (videosPayload.data ?? [])
      .filter((model) => Object.values(model.pricing_skus ?? {}).some((value) => Number(value) > 0))
      .map((model) => model.id),
  );
  const mediaModelsWithZeroTokenPrice = localCatalog.filter((model) => {
    const outputs = new Set(model.output_modalities);
    return model.pricing_status === "free" &&
      ["image", "video", "audio", "speech"].some(
        (modality) => outputs.has(modality),
      );
  });
  const nonExplicitFreeModels = localCatalog.filter(
    (model) =>
      model.pricing_status === "free" &&
      !model.id.endsWith(":free") &&
      model.id !== "openrouter/free",
  );
  const zeroTokenMediaWithWrongBasis = localCatalog.filter((model) => {
    const source = sourceById.get(model.id);
    const zeroTokenPrice =
      Number(source.pricing?.prompt) === 0 &&
      Number(source.pricing?.completion) === 0;
    const mediaOutput = ["image", "video", "audio", "speech"].some(
      (modality) => model.output_modalities.includes(modality),
    );
    return zeroTokenPrice &&
      mediaOutput &&
      !model.id.endsWith(":free") &&
      model.pricing_basis !== "media";
  });
  const zeroTokenRequestWithWrongBasis = localCatalog.filter((model) => {
    const source = sourceById.get(model.id);
    const zeroTokenPrice =
      Number(source.pricing?.prompt) === 0 &&
      Number(source.pricing?.completion) === 0;
    const requestOutput = ["embeddings", "rerank"].some(
      (modality) => model.output_modalities.includes(modality),
    );
    return zeroTokenPrice &&
      requestOutput &&
      !model.id.endsWith(":free") &&
      model.pricing_basis !== "request";
  });
  const paidVideoModelsMislabeledFree = mediaModelsWithZeroTokenPrice.filter((model) =>
    dedicatedPaidVideoIds.has(model.id),
  );
  const imageModelsUsingEndpointPricing = mediaModelsWithZeroTokenPrice.filter((model) =>
    imageModelIds.has(model.id),
  );
  const audioHourPricingOverlayMismatches = auditAudioHourPricingOverlays({
    localModels,
    sourceModels,
    marketModels: marketPayload.data.models,
  });

  const optionValues = new Set(jobCapabilityOptions.map((option) => option.value));
  const producedJobCapabilities = new Set(localModels.flatMap((model) => model.job_capabilities));
  const producedJobCapabilitiesMissingOption = sorted(
    [...producedJobCapabilities].filter((value) => !optionValues.has(value)),
  );
  const optionsWithoutAnyModel = sorted(
    [...optionValues].filter((value) => !producedJobCapabilities.has(value)),
  );
  const sourceCanonicalSlugs = new Set(sourceModels.map((model) => model.canonical_slug));
  const localProviderOther = localCatalog.filter((model) => model.provider === "其他");
  const currentExpirationSeconds = Math.floor(Date.now() / 1000);
  const sourceExpiredByDeclaredDate = sourceModels.filter(
    (model) => {
      if (typeof model.expiration_date === "number") {
        return model.expiration_date <= currentExpirationSeconds;
      }
      if (typeof model.expiration_date === "string") {
        const parsed = Date.parse(model.expiration_date);
        return Number.isFinite(parsed) && parsed / 1000 <= currentExpirationSeconds;
      }
      return false;
    },
  );
  const localCountedAbsentUpstream = localModels.filter(
    (model) => model.catalog_counted && !sourceIds.has(model.id),
  );
  const sourceModelsMissingLocally = sorted(
    sourceModels
      .filter((model) => !localModels.some((local) => local.id === model.id))
      .map((model) => model.id),
  );
  const discreteOptionSetMatch = {
    series: hasExactOptionSet(
      seriesOptions.map((option) => option.value),
      OPENROUTER_MARKET_SERIES,
    ),
    categories: hasExactOptionSet(
      marketCategoryOptions.map((option) => option.value),
      OPENROUTER_MARKET_CATEGORIES,
    ),
    supported_parameters: hasExactOptionSet(
      supportedParameterOptions.map((option) => option.value),
      OPENROUTER_SUPPORTED_PARAMETERS,
    ),
  };
  const marketStructuralMismatchIds = sorted(
    marketSnapshotMismatches
      .filter((mismatch) =>
        MARKET_STRUCTURAL_FIELDS.some(
          (field) =>
            JSON.stringify(mismatch.actual[field]) !==
            JSON.stringify(mismatch.expected[field]),
        ),
      )
      .map((mismatch) => mismatch.id),
  );
  const marketVolatileMismatchIds = sorted(
    marketSnapshotMismatches
      .filter((mismatch) =>
        MARKET_VOLATILE_FIELDS.some(
          (field) =>
            JSON.stringify(mismatch.actual[field]) !==
            JSON.stringify(mismatch.expected[field]),
        ),
      )
      .map((mismatch) => mismatch.id),
  );
  const classifiedMarketFields = new Set([
    ...MARKET_STRUCTURAL_FIELDS,
    ...MARKET_VOLATILE_FIELDS,
  ]);
  const unclassifiedMarketMismatchIds = sorted(
    marketSnapshotMismatches
      .filter((mismatch) =>
        [...new Set([
          ...Object.keys(mismatch.actual),
          ...Object.keys(mismatch.expected),
        ])].some(
          (field) =>
            JSON.stringify(mismatch.actual[field]) !==
              JSON.stringify(mismatch.expected[field]) &&
            !classifiedMarketFields.has(field),
        ),
      )
      .map((mismatch) => mismatch.id),
  );
  const actionableReasons = {
    source_models_missing_locally: sourceModelsMissingLocally,
    operation_mismatches: authoritativeOperationMismatches.map((item) => item.id),
    job_capability_mismatches: authoritativeJobMismatches.map((item) => item.id),
    structural_market_mismatches: marketStructuralMismatchIds,
    unclassified_market_mismatches: unclassifiedMarketMismatchIds,
    discrete_option_set_mismatches: Object.entries(discreteOptionSetMatch)
      .filter(([, matches]) => !matches)
      .map(([name]) => name),
    reasoning_without_structured_api_signal:
      reasoningWithoutStructuredSignal.map((model) => model.id),
    translation_without_text_output:
      translationWithoutTextOutput.map((model) => model.id),
    coding_without_text_output: codingWithoutTextOutput.map((model) => model.id),
    non_explicit_models_marked_free: nonExplicitFreeModels.map((model) => model.id),
    zero_token_media_with_wrong_basis:
      zeroTokenMediaWithWrongBasis.map((model) => model.id),
    zero_token_request_with_wrong_basis:
      zeroTokenRequestWithWrongBasis.map((model) => model.id),
    audio_hour_pricing_overlay_mismatches:
      audioHourPricingOverlayMismatches.map((item) => item.id),
    provider_other: localProviderOther.map((model) => model.id),
    produced_job_capabilities_missing_option:
      producedJobCapabilitiesMissingOption,
    options_without_any_model: optionsWithoutAnyModel,
  };
  const hasActionableClassificationDrift = Object.values(actionableReasons)
    .some((items) => items.length > 0);

  const report = {
    schema_version: 1,
    audited_at: new Date().toISOString(),
    actionability: {
      actionable: hasActionableClassificationDrift,
      reasons: actionableReasons,
      volatile_market_observation_ids: marketVolatileMismatchIds,
    },
    source: {
      models_url: "https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000",
      images_url: "https://openrouter.ai/api/v1/images/models",
      videos_url: "https://openrouter.ai/api/v1/videos/models",
      general_entries: allSourceModels.length,
      batch_serving_variants: batchVariants.length,
      non_batch_entries: sourceModels.length,
      canonical_entities: sourceCanonicalSlugs.size,
      dedicated_image_models: imageModelIds.size,
      dedicated_video_models: videoModelIds.size,
      declared_expired_but_still_listed: sourceExpiredByDeclaredDate.map((model) => model.id),
      input_modality_model_counts: sourceInputCounts,
      output_modality_model_counts: sourceOutputCounts,
    },
    coverage: {
      local_catalog_entries_compared: localCatalog.length,
      source_models_missing_locally: sourceModelsMissingLocally,
      local_counted_entries_absent_upstream: {
        count: localCountedAbsentUpstream.length,
        by_catalog_status: countBy(
          localCountedAbsentUpstream,
          (model) => model.catalog_status,
        ),
        model_ids: sorted(localCountedAbsentUpstream.map((model) => model.id)),
      },
      unexpected_locally_live_entries_absent_upstream: sorted(
        localCountedAbsentUpstream
          .filter((model) => model.catalog_status === "live")
          .map((model) => model.id),
      ),
    },
    authoritative_classification: {
      operation_mismatches: authoritativeOperationMismatches,
      job_capability_mismatches: authoritativeJobMismatches,
      local_operation_counts: countBy(
        localCatalog.flatMap((model) => model.operations),
        (operation) => operation,
      ),
      local_authoritative_job_counts: countBy(
        localCatalog.flatMap((model) =>
          model.job_capabilities.filter((job) => authoritativeJobNames.has(job)),
        ),
        (job) => job,
      ),
    },
    semantic_taxonomy: {
      authority: "local_heuristic_only",
      category_counts: semanticCategoryCounts,
      reasoning_without_structured_api_signal: {
        count: reasoningWithoutStructuredSignal.length,
        models: reasoningWithoutStructuredSignal.slice(0, 30).map(sampleModel),
      },
      reasoning_only_from_loose_o3_o4_substring: {
        count: reasoningOnlyFromLooseSubstring.length,
        models: reasoningOnlyFromLooseSubstring.slice(0, 30).map(sampleModel),
      },
      translation_without_text_output: {
        count: translationWithoutTextOutput.length,
        models: translationWithoutTextOutput.slice(0, 30).map(sampleModel),
      },
      coding_without_text_output: {
        count: codingWithoutTextOutput.length,
        models: codingWithoutTextOutput.slice(0, 30).map(sampleModel),
      },
      reasoning_models_copy_math_or_analysis: reasoningModels
        .filter(
          (model) =>
            model.categories.includes("math") ||
            model.categories.includes("analysis"),
        )
        .map(sampleModel),
    },
    openrouter_models_sidebar: {
      authority: OPENROUTER_MARKET_URL,
      source_service_records: marketSource.sourceRecords,
      source_model_snapshots: marketSource.snapshots.size,
      compared_models: localCatalog.filter((model) =>
        marketSource.snapshots.has(model.id),
      ).length,
      snapshot_mismatches: marketSnapshotMismatches,
      discrete_options: {
        series: seriesOptions.map((option) => option.value),
        categories: marketCategoryOptions.map((option) => option.value),
        supported_parameters: supportedParameterOptions.map(
          (option) => option.value,
        ),
      },
      expected_discrete_options: {
        series: OPENROUTER_MARKET_SERIES,
        categories: OPENROUTER_MARKET_CATEGORIES,
        supported_parameters: OPENROUTER_SUPPORTED_PARAMETERS,
      },
      discrete_option_set_match: discreteOptionSetMatch,
      category_reference: categoryReference
          ? {
            source: "Optional singular ?category= responses",
            scope: "Cross-reference for the Categories facet only; not ranking or capability authority.",
            gating: false,
            response_counts: categoryReference.responseCounts,
            missing_from_market_category_placements: categoryReferenceMismatches,
          }
        : {
            source: null,
            scope: "Not supplied; category reference is optional.",
            gating: false,
            missing_from_market_category_placements: [],
          },
    },
    pricing_taxonomy: {
      pricing_basis_counts: countBy(
        localCatalog,
        (model) => model.pricing_basis,
      ),
      non_explicit_models_marked_free: {
        count: nonExplicitFreeModels.length,
        model_ids: nonExplicitFreeModels.map((model) => model.id),
      },
      zero_token_media_with_wrong_basis: {
        count: zeroTokenMediaWithWrongBasis.length,
        model_ids: zeroTokenMediaWithWrongBasis.map((model) => model.id),
      },
      zero_token_request_with_wrong_basis: {
        count: zeroTokenRequestWithWrongBasis.length,
        model_ids: zeroTokenRequestWithWrongBasis.map((model) => model.id),
      },
      audio_hour_pricing_overlay_mismatches: {
        required_model_ids: [
          ...REQUIRED_AUDIO_HOUR_PRICING_OVERLAYS.keys(),
        ].sort((left, right) => left.localeCompare(right)),
        count: audioHourPricingOverlayMismatches.length,
        models: audioHourPricingOverlayMismatches,
      },
      media_models_with_zero_token_price_marked_free: {
        count: mediaModelsWithZeroTokenPrice.length,
        model_ids: mediaModelsWithZeroTokenPrice.map((model) => model.id),
      },
      dedicated_paid_video_models_marked_free: {
        count: paidVideoModelsMislabeledFree.length,
        model_ids: paidVideoModelsMislabeledFree.map((model) => model.id),
      },
      image_models_requiring_endpoint_pricing_but_marked_free: {
        count: imageModelsUsingEndpointPricing.length,
        model_ids: imageModelsUsingEndpointPricing.map((model) => model.id),
      },
    },
    filter_and_provider_sync: {
      produced_job_capabilities_missing_option: sorted(
        producedJobCapabilitiesMissingOption,
      ),
      options_without_any_model: sorted(
        optionsWithoutAnyModel,
      ),
      provider_other: {
        count: localProviderOther.length,
        share: Number((localProviderOther.length / localCatalog.length).toFixed(4)),
        distinct_model_authors: new Set(localProviderOther.map((model) => model.model_author)).size,
      },
      series_count: new Set(
        localCatalog.map((model) => model.openrouter_market.series),
      ).size,
      provider_endpoint_count: new Set(
        localCatalog.flatMap((model) => model.openrouter_market.providers),
      ).size,
      author_count: new Set(
        localCatalog.map((model) => model.openrouter_market.author),
      ).size,
      note: "Provider/author options come from the market snapshot. Series, Categories and Supported Parameters must preserve the exact sidebar option sets; display order may prioritize commonly used brands.",
    },
  };

  const output = `${JSON.stringify(report, null, 2)}\n`;
  if (args.output) {
    await fs.writeFile(path.resolve(args.output), output, "utf8");
  }
  process.stdout.write(output);

  if (
    hasActionableClassificationDrift ||
    marketVolatileMismatchIds.length > 0
  ) {
    process.exitCode = 1;
  }
}

await main();
