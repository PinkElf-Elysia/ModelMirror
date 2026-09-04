import fs from "node:fs";

const [inputPath, countPath, sourceKey = "", comparisonPath = ""] =
  process.argv.slice(2);
if (!inputPath || !["data", "data.models"].includes(countPath)) {
  process.stderr.write(
    "Usage: inspect-json.mjs <input> <data|data.models> [source-key] [comparison-input]\n",
  );
  process.exit(2);
}

function recordsAt(payload, path) {
  const records = path === "data" ? payload.data : payload?.data?.models;
  if (!Array.isArray(records) || records.length === 0) {
    throw new Error(`No records at ${path}`);
  }
  return records;
}

function validateCompleteModelsResponse(payload, records) {
  const totalCount = Number(payload.total_count);
  const nextPage = payload?.links?.next;
  if (
    !Number.isInteger(totalCount) ||
    totalCount < 0 ||
    records.length !== totalCount ||
    (nextPage !== null && nextPage !== undefined && nextPage !== "")
  ) {
    throw new Error(
      `Models response is paginated or truncated: data=${records.length}, total_count=${String(payload.total_count)}, next=${String(nextPage)}`,
    );
  }
}

function requiredModelId(record, label, index) {
  const modelId = String(record?.id ?? "").trim();
  if (!modelId) {
    throw new Error(`${label} record ${index} has no model identity`);
  }
  return modelId;
}

function requiredMarketModelId(record, index) {
  const modelId = String(
    record?.endpoint?.model_variant_slug ?? record?.slug ?? "",
  ).trim();
  if (!modelId) {
    throw new Error(`Market record ${index} has no model identity`);
  }
  return modelId;
}

function sorted(values) {
  return [...values].sort((left, right) => left.localeCompare(right));
}

export function compareModelMarketCoverage(modelsPayload, marketPayload) {
  const modelRecords = recordsAt(modelsPayload, "data");
  validateCompleteModelsResponse(modelsPayload, modelRecords);
  const marketRecords = recordsAt(marketPayload, "data.models");
  const nonBatchIds = modelRecords
    .map((record, index) => requiredModelId(record, "Models", index))
    .filter((modelId) => !modelId.endsWith(":batch"));
  const sourceModelIds = new Set(nonBatchIds);
  if (sourceModelIds.size !== nonBatchIds.length) {
    const seen = new Set();
    const duplicates = [];
    for (const modelId of nonBatchIds) {
      if (seen.has(modelId)) duplicates.push(modelId);
      seen.add(modelId);
    }
    throw new Error(
      `Models response has duplicate non-Batch identities: ${sorted(new Set(duplicates)).join(",")}`,
    );
  }
  const marketModelIds = new Set(
    marketRecords.map((record, index) =>
      requiredMarketModelId(record, index).replace(/:batch$/, ""),
    ),
  );
  const missingFromMarket = sorted(
    [...sourceModelIds].filter((modelId) => !marketModelIds.has(modelId)),
  );
  const marketOnly = sorted(
    [...marketModelIds].filter((modelId) => !sourceModelIds.has(modelId)),
  );
  return {
    comparison: "exact-id-set-v1",
    source_non_batch_models: sourceModelIds.size,
    market_unique_base_models: marketModelIds.size,
    missing_from_market: missingFromMarket,
    market_only: marketOnly,
    complete: missingFromMarket.length === 0 && marketOnly.length === 0,
  };
}

const payload = JSON.parse(fs.readFileSync(inputPath, "utf8"));
try {
  const records = recordsAt(payload, countPath);
  if (
    sourceKey === "models" ||
    sourceKey === "models-non-batch" ||
    sourceKey === "compare-market"
  ) {
    validateCompleteModelsResponse(payload, records);
  }
  if (sourceKey === "compare-market") {
    if (!comparisonPath) {
      throw new Error("compare-market requires a market response path");
    }
    const coverage = compareModelMarketCoverage(
      payload,
      JSON.parse(fs.readFileSync(comparisonPath, "utf8")),
    );
    process.stdout.write(JSON.stringify(coverage));
    if (!coverage.complete) process.exitCode = 2;
  } else if (sourceKey === "models-non-batch") {
    process.stdout.write(
      String(
        records
          .map((record, index) => requiredModelId(record, "Models", index))
          .filter((modelId) => !modelId.endsWith(":batch")).length,
      ),
    );
  } else if (sourceKey === "market-unique-base") {
    const uniqueModelIds = new Set(
      records.map((record, index) =>
        requiredMarketModelId(record, index).replace(/:batch$/, ""),
      ),
    );
    process.stdout.write(String(uniqueModelIds.size));
  } else {
    process.stdout.write(String(records.length));
  }
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 2;
}
