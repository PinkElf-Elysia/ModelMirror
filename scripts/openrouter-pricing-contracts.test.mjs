import assert from "node:assert/strict";
import test from "node:test";
import { auditAudioHourPricingOverlays } from "./openrouter-pricing-contracts.mjs";

const source = {
  id: "microsoft/mai-transcribe-2",
  pricing: { prompt: "0.1" },
};
const local = {
  id: "microsoft/mai-transcribe-2",
  pricing_basis: "media",
  media_pricing: { unit: "audio_hour", usd: 0.1 },
};
const market = {
  slug: "microsoft/mai-transcribe-2",
  endpoint: {
    model_variant_slug: "microsoft/mai-transcribe-2",
    pricing: { prompt: "0.1" },
    display_pricing: [
      {
        kind: "unit",
        sku_label: "Audio Hours",
        price: "0.1",
        displayMultiplier: 1,
        unitLabel: "/hour",
      },
    ],
    pricing_json: { "microsoft_stt:audio_hours": "0.1" },
  },
};

test("accepts the required MAI audio-hour overlay", () => {
  assert.deepEqual(
    auditAudioHourPricingOverlays({
      localModels: [local],
      sourceModels: [source],
      marketModels: [market],
    }),
    [],
  );
});

test("reports a required overlay that was removed", () => {
  const [mismatch] = auditAudioHourPricingOverlays({
    localModels: [{ ...local, media_pricing: undefined }],
    sourceModels: [source],
    marketModels: [market],
  });
  assert.equal(mismatch.id, local.id);
  assert.deepEqual(mismatch.reasons, ["overlay_missing"]);
});

test("reports wrong units, bases, invalid prices, and price drift", () => {
  const [wrongContract] = auditAudioHourPricingOverlays({
    localModels: [
      {
        ...local,
        pricing_basis: "token",
        media_pricing: { unit: "audio_minute", usd: "not-a-price" },
      },
    ],
    sourceModels: [source],
    marketModels: [market],
  });
  assert.deepEqual(wrongContract.reasons, [
    "wrong_unit",
    "pricing_basis_not_media",
    "invalid_overlay_price",
  ]);

  const [sourceMissing] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [{ ...source, pricing: { prompt: null } }],
    marketModels: [market],
  });
  assert.deepEqual(sourceMissing.reasons, ["invalid_source_price"]);

  const [drift] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [{ ...source, pricing: { prompt: "0.2" } }],
    marketModels: [market],
  });
  assert.deepEqual(drift.reasons, ["price_mismatch"]);
});

test("fails closed when the market unit changes but the numeric price does not", () => {
  const [drift] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [source],
    marketModels: [
      {
        ...market,
        endpoint: {
          ...market.endpoint,
          display_pricing: [
            {
              ...market.endpoint.display_pricing[0],
              sku_label: "Audio Minutes",
              unitLabel: "/minute",
            },
          ],
        },
      },
    ],
  });
  assert.deepEqual(drift.reasons, [
    "market_sku_label_mismatch",
    "market_unit_label_mismatch",
  ]);
});

test("fails closed when the market pricing SKU changes", () => {
  const [drift] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [source],
    marketModels: [
      {
        ...market,
        endpoint: {
          ...market.endpoint,
          pricing_json: { "microsoft_stt:audio_minutes": "0.1" },
        },
      },
    ],
  });
  assert.deepEqual(drift.reasons, ["market_pricing_json_sku_missing"]);
});

test("fails closed when the market pricing record is absent", () => {
  const [drift] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [source],
    marketModels: [],
  });
  assert.deepEqual(drift.reasons, ["market_realtime_record_missing"]);
});

test("does not accept a Batch record as the realtime pricing authority", () => {
  const [drift] = auditAudioHourPricingOverlays({
    localModels: [local],
    sourceModels: [source],
    marketModels: [
      {
        ...market,
        slug: "microsoft/mai-transcribe-2:batch",
        endpoint: {
          ...market.endpoint,
          model_variant_slug: "microsoft/mai-transcribe-2:batch",
        },
      },
    ],
  });
  assert.deepEqual(drift.reasons, ["market_realtime_record_missing"]);
});

test("requires every local audio-hour overlay to register its market contract", () => {
  const unregistered = {
    ...local,
    id: "vendor/future-transcriber",
  };
  const drift = auditAudioHourPricingOverlays({
    localModels: [unregistered],
    sourceModels: [
      {
        ...source,
        id: unregistered.id,
      },
    ],
    marketModels: [
      {
        slug: unregistered.id,
        endpoint: {
          model_variant_slug: unregistered.id,
          pricing: { prompt: "0.1" },
          display_pricing: [
            {
              kind: "unit",
              sku_label: "Audio Minutes",
              price: "0.1",
              unitLabel: "/minute",
            },
          ],
          pricing_json: { "vendor:audio_minutes": "0.1" },
        },
      },
    ],
  }).find((item) => item.id === unregistered.id);
  assert.ok(drift);
  assert.deepEqual(drift.reasons, [
    "pricing_overlay_contract_unregistered",
  ]);
});
