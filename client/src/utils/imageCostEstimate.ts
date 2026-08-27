export interface ImagePricingItem {
  billable: "input_image" | "input_reference" | "output_image";
  unit: "image" | "request";
  cost_usd: number;
  variant?: string | null;
}

export interface ImageCostEstimateInput {
  outputCount: number;
  referenceCount: number;
  resolution?: string;
  quality?: string;
}

export interface ImageCostEstimate {
  minUsd: number;
  maxUsd: number;
  inputUsd: number;
  exact: boolean;
}

// Verified against OpenRouter's dedicated image endpoint on 2026-08-12.
// Live profile pricing takes precedence; this keeps the snapshot usable when
// the optional pricing-detail request is temporarily unavailable.
export const GROK_IMAGINE_IMAGE_2_PRICING: ImagePricingItem[] = [
  { billable: "input_image", unit: "image", cost_usd: 0.01 },
  { billable: "output_image", unit: "image", cost_usd: 0.04, variant: "low_1k" },
  { billable: "output_image", unit: "image", cost_usd: 0.06, variant: "low_2k" },
  { billable: "output_image", unit: "image", cost_usd: 0.06, variant: "medium_1k" },
  { billable: "output_image", unit: "image", cost_usd: 0.08, variant: "medium_2k" },
];

// Verified against the dedicated Seedream endpoint on 2026-08-13. The live
// endpoint profile still takes precedence when OpenRouter updates its pricing.
export const SEEDREAM_5_PRO_PRICING: ImagePricingItem[] = [
  { billable: "input_image", unit: "image", cost_usd: 0.003 },
  { billable: "output_image", unit: "image", cost_usd: 0.045 },
  {
    billable: "output_image",
    unit: "image",
    cost_usd: 0.09,
    variant: "high_resolution",
  },
];

// Verified against the dedicated Seedream endpoint on 2026-08-14. The Lite
// endpoint currently charges a flat rate per output image for both 2K and 4K.
export const SEEDREAM_5_LITE_PRICING: ImagePricingItem[] = [
  { billable: "output_image", unit: "image", cost_usd: 0.035 },
];

// Verified against the Muse Image model page and dedicated Images catalog on
// 2026-08-26. The endpoint-detail feed is currently empty, so this fallback is
// used only when no live structured pricing item is available.
export const MUSE_IMAGE_PRICING: ImagePricingItem[] = [
  { billable: "output_image", unit: "image", cost_usd: 0.01 },
];

// Verified against the four Recraft Styles endpoint profiles on 2026-08-26.
// Style creation is billed once per request, not once per reference image.
export const RECRAFT_V4_STYLES_PRICING_BY_MODEL_ID: Record<
  string,
  ImagePricingItem[]
> = {
  "recraft/recraft-v4-styles": [
    { billable: "input_reference", unit: "request", cost_usd: 0.005 },
    { billable: "output_image", unit: "image", cost_usd: 0.035 },
  ],
  "recraft/recraft-v4-styles-pro": [
    { billable: "input_reference", unit: "request", cost_usd: 0.005 },
    { billable: "output_image", unit: "image", cost_usd: 0.1 },
  ],
  "recraft/recraft-v4-styles-vector": [
    { billable: "input_reference", unit: "request", cost_usd: 0.005 },
    { billable: "output_image", unit: "image", cost_usd: 0.05 },
  ],
  "recraft/recraft-v4-styles-pro-vector": [
    { billable: "input_reference", unit: "request", cost_usd: 0.005 },
    { billable: "output_image", unit: "image", cost_usd: 0.12 },
  ],
};

function normalized(value?: string) {
  return value?.trim().toLowerCase() ?? "";
}

export function estimateImageCost(
  pricing: ImagePricingItem[],
  input: ImageCostEstimateInput,
): ImageCostEstimate | null {
  const outputCount = Math.max(1, Math.floor(input.outputCount));
  const referenceCount = Math.max(0, Math.floor(input.referenceCount));
  const resolution = normalized(input.resolution);
  const quality = normalized(input.quality);
  const hasHighResolutionVariant = pricing.some(
    (item) => normalized(item.variant ?? undefined) === "high_resolution",
  );
  const inputCosts = pricing
    .filter(
      (item) =>
        item.billable === "input_image" ||
        item.billable === "input_reference",
    )
    .map((item) =>
      item.unit === "request"
        ? item.cost_usd
        : referenceCount * item.cost_usd,
    )
    .filter((value) => Number.isFinite(value) && value >= 0);
  let outputRates = pricing
    .filter((item) => item.billable === "output_image")
    .filter((item) => {
      const variant = normalized(item.variant ?? undefined);
      if (!variant) {
        return !(resolution === "2k" && hasHighResolutionVariant);
      }
      if (variant === "high_resolution") {
        return !resolution || resolution === "2k";
      }
      if (quality && !variant.startsWith(`${quality}_`)) return false;
      if (resolution && !variant.endsWith(`_${resolution}`)) return false;
      return true;
    })
    .map((item) => item.cost_usd)
    .filter((value) => Number.isFinite(value) && value >= 0);

  if (!outputRates.length && (quality || resolution)) {
    outputRates = pricing
      .filter(
        (item) =>
          item.billable === "output_image" && !normalized(item.variant ?? undefined),
      )
      .map((item) => item.cost_usd)
      .filter((value) => Number.isFinite(value) && value >= 0);
  }
  if (!outputRates.length || (referenceCount > 0 && !inputCosts.length)) {
    return null;
  }

  const inputUsd =
    referenceCount > 0 && inputCosts.length ? Math.max(...inputCosts) : 0;
  const minUsd = inputUsd + outputCount * Math.min(...outputRates);
  const maxUsd = inputUsd + outputCount * Math.max(...outputRates);
  return {
    minUsd,
    maxUsd,
    inputUsd,
    exact: Math.abs(minUsd - maxUsd) < 1e-9,
  };
}
