import { describe, expect, it } from "vitest";
import {
  estimateImageCost,
  GROK_IMAGINE_IMAGE_2_PRICING,
  imageTokenPricingSummary,
  MAI_IMAGE_TOKEN_PRICING_BY_MODEL_ID,
  MUSE_IMAGE_PRICING,
  RECRAFT_V4_STYLES_PRICING_BY_MODEL_ID,
  SEEDREAM_5_LITE_PRICING,
  SEEDREAM_5_PRO_PRICING,
} from "./imageCostEstimate";

describe("estimateImageCost", () => {
  it("returns the exact Grok output and reference-image estimate", () => {
    expect(
      estimateImageCost(GROK_IMAGINE_IMAGE_2_PRICING, {
        outputCount: 1,
        referenceCount: 3,
        resolution: "2K",
        quality: "medium",
      }),
    ).toEqual({
      minUsd: 0.11,
      maxUsd: 0.11,
      inputUsd: 0.03,
      exact: true,
    });
  });

  it("returns an honest range while model defaults are selected", () => {
    expect(
      estimateImageCost(GROK_IMAGINE_IMAGE_2_PRICING, {
        outputCount: 1,
        referenceCount: 0,
      }),
    ).toEqual({
      minUsd: 0.04,
      maxUsd: 0.08,
      inputUsd: 0,
      exact: false,
    });
  });

  it("does not understate reference-image cost when its rate is absent", () => {
    expect(
      estimateImageCost(GROK_IMAGINE_IMAGE_2_PRICING.slice(1), {
        outputCount: 1,
        referenceCount: 1,
        resolution: "1K",
        quality: "low",
      }),
    ).toBeNull();
  });

  it("uses Seedream's high-resolution output rate at 2K", () => {
    expect(
      estimateImageCost(SEEDREAM_5_PRO_PRICING, {
        outputCount: 1,
        referenceCount: 2,
        resolution: "2K",
      }),
    ).toEqual({
      minUsd: 0.096,
      maxUsd: 0.096,
      inputUsd: 0.006,
      exact: true,
    });
  });

  it("uses Seedream Lite's flat per-image rate for 4K output", () => {
    expect(
      estimateImageCost(SEEDREAM_5_LITE_PRICING, {
        outputCount: 4,
        referenceCount: 0,
        resolution: "4K",
      }),
    ).toEqual({
      minUsd: 0.14,
      maxUsd: 0.14,
      inputUsd: 0,
      exact: true,
    });
  });

  it("uses Muse Image's flat output price when endpoint details are empty", () => {
    expect(
      estimateImageCost(MUSE_IMAGE_PRICING, {
        outputCount: 1,
        referenceCount: 0,
      }),
    ).toEqual({
      minUsd: 0.01,
      maxUsd: 0.01,
      inputUsd: 0,
      exact: true,
    });
  });

  it("charges Recraft style creation once per request", () => {
    expect(
      estimateImageCost(
        RECRAFT_V4_STYLES_PRICING_BY_MODEL_ID[
          "recraft/recraft-v4-styles-pro-vector"
        ],
        {
          outputCount: 6,
          referenceCount: 10,
        },
      ),
    ).toEqual({
      minUsd: 0.725,
      maxUsd: 0.725,
      inputUsd: 0.005,
      exact: true,
    });
  });

  it("shows MAI token rates without fabricating a preflight total", () => {
    const pricing =
      MAI_IMAGE_TOKEN_PRICING_BY_MODEL_ID["microsoft/mai-image-2.6-flash"];

    expect(
      estimateImageCost(pricing, {
        outputCount: 1,
        referenceCount: 2,
      }),
    ).toBeNull();
    expect(imageTokenPricingSummary(pricing)).toBe(
      "文本输入 $1.75/M Token · 图片输入 $2.50/M Token · 图片输出 $19/M Token",
    );
  });
});
