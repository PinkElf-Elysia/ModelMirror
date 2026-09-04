export const REQUIRED_AUDIO_HOUR_PRICING_OVERLAYS = new Map([
  [
    "microsoft/mai-transcribe-2",
    Object.freeze({
      unit: "audio_hour",
      pricingBasis: "media",
      sourcePricingField: "prompt",
      normalizedPricingField: "input",
      normalizedPriceDivisor: 1_000_000,
      marketPricing: Object.freeze({
        pricingField: "prompt",
        displayKind: "unit",
        skuLabel: "Audio Hours",
        unitLabel: "/hour",
        pricingJsonKey: "microsoft_stt:audio_hours",
      }),
    }),
  ],
]);

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function normalizedCatalogPricePerUnit(model, contract) {
  const value = finiteNumber(
    model?.pricing?.[contract.normalizedPricingField],
  );
  const divisor = finiteNumber(contract.normalizedPriceDivisor);
  if (value === null || divisor === null || divisor <= 0) return null;
  return value / divisor;
}

function stripBatchSuffix(value) {
  return String(value ?? "").replace(/:batch$/, "");
}

function marketModelIdentity(record) {
  const variantId = String(
    record?.endpoint?.model_variant_slug || record?.slug || "",
  );
  return {
    id: stripBatchSuffix(variantId),
    isBatch: variantId.endsWith(":batch"),
  };
}

function auditMarketPricingRecord(record, contract, expectedPrice) {
  const endpoint = record?.endpoint;
  const marketContract = contract.marketPricing;
  const reasons = [];
  const endpointPrice = finiteNumber(
    endpoint?.pricing?.[marketContract.pricingField],
  );
  if (endpointPrice === null) {
    reasons.push("market_endpoint_price_invalid");
  } else if (
    expectedPrice !== null &&
    Math.abs(endpointPrice - expectedPrice) > 1e-12
  ) {
    reasons.push("market_endpoint_price_mismatch");
  }

  const displayPricing = Array.isArray(endpoint?.display_pricing)
    ? endpoint.display_pricing
    : Array.isArray(endpoint?.pricing?.display_pricing)
      ? endpoint.pricing.display_pricing
      : [];
  const displayEntry =
    displayPricing.find((entry) => entry?.sku_label === marketContract.skuLabel) ??
    displayPricing[0];
  if (!displayEntry) {
    reasons.push("market_display_pricing_missing");
  } else {
    if (displayEntry.kind !== marketContract.displayKind) {
      reasons.push("market_display_kind_mismatch");
    }
    if (displayEntry.sku_label !== marketContract.skuLabel) {
      reasons.push("market_sku_label_mismatch");
    }
    if (displayEntry.unitLabel !== marketContract.unitLabel) {
      reasons.push("market_unit_label_mismatch");
    }
    const displayPrice = finiteNumber(displayEntry.price);
    if (displayPrice === null) {
      reasons.push("market_display_price_invalid");
    } else if (
      expectedPrice !== null &&
      Math.abs(displayPrice - expectedPrice) > 1e-12
    ) {
      reasons.push("market_display_price_mismatch");
    }
  }

  const pricingJsonValue = finiteNumber(
    endpoint?.pricing_json?.[marketContract.pricingJsonKey],
  );
  if (pricingJsonValue === null) {
    reasons.push("market_pricing_json_sku_missing");
  } else if (
    expectedPrice !== null &&
    Math.abs(pricingJsonValue - expectedPrice) > 1e-12
  ) {
    reasons.push("market_pricing_json_price_mismatch");
  }
  return reasons;
}

export function auditAudioHourPricingOverlays({
  localModels,
  sourceModels,
  marketModels,
}) {
  const localById = new Map(localModels.map((model) => [model.id, model]));
  const sourceById = new Map(sourceModels.map((model) => [model.id, model]));
  const marketRecordsById = new Map();
  for (const record of marketModels ?? []) {
    const { id, isBatch } = marketModelIdentity(record);
    if (!id || isBatch) continue;
    const records = marketRecordsById.get(id) ?? [];
    records.push(record);
    marketRecordsById.set(id, records);
  }
  const candidateIds = new Set(REQUIRED_AUDIO_HOUR_PRICING_OVERLAYS.keys());
  for (const model of localModels) {
    if (model.media_pricing?.unit === "audio_hour") candidateIds.add(model.id);
  }

  return [...candidateIds]
    .sort((left, right) => left.localeCompare(right))
    .map((id) => {
      const registeredContract = REQUIRED_AUDIO_HOUR_PRICING_OVERLAYS.get(id);
      const contract = registeredContract ?? {
        unit: "audio_hour",
        pricingBasis: "media",
        sourcePricingField: "prompt",
        normalizedPricingField: "input",
        normalizedPriceDivisor: 1_000_000,
      };
      const local = localById.get(id);
      const source = sourceById.get(id);
      const sourcePrice = finiteNumber(
        source?.pricing?.[contract.sourcePricingField],
      );
      const overlayPrice = finiteNumber(local?.media_pricing?.usd);
      const reasons = [];

      if (!registeredContract) {
        reasons.push("pricing_overlay_contract_unregistered");
      }
      if (!local) reasons.push("local_model_missing");
      if (local && !local.media_pricing) reasons.push("overlay_missing");
      if (
        local?.media_pricing &&
        local.media_pricing.unit !== contract.unit
      ) {
        reasons.push("wrong_unit");
      }
      if (local && local.pricing_basis !== contract.pricingBasis) {
        reasons.push("pricing_basis_not_media");
      }
      if (sourcePrice === null) reasons.push("invalid_source_price");
      if (local?.media_pricing && overlayPrice === null) {
        reasons.push("invalid_overlay_price");
      }
      if (
        sourcePrice !== null &&
        overlayPrice !== null &&
        Math.abs(sourcePrice - overlayPrice) > 1e-12
      ) {
        reasons.push("price_mismatch");
      }
      const marketRecords = marketRecordsById.get(id) ?? [];
      if (contract.marketPricing) {
        if (marketRecords.length === 0) {
          reasons.push("market_realtime_record_missing");
        } else {
          const expectedPrice = overlayPrice ?? sourcePrice;
          const marketAudits = marketRecords.map((record) =>
            auditMarketPricingRecord(record, contract, expectedPrice),
          );
          if (!marketAudits.some((auditReasons) => auditReasons.length === 0)) {
            marketAudits.sort((left, right) => left.length - right.length);
            reasons.push(...marketAudits[0]);
          }
        }
      }

      return {
        id,
        reasons,
        source_usd_per_audio_hour: sourcePrice,
        overlay_usd_per_audio_hour: overlayPrice,
        pricing_basis: local?.pricing_basis ?? null,
        pricing_unit: local?.media_pricing?.unit ?? null,
        market_record_count: marketRecords.length,
      };
    })
    .filter((item) => item.reasons.length > 0);
}
