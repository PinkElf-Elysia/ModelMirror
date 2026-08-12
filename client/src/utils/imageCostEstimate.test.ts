import { describe, expect, it } from "vitest";
import {
  estimateImageCost,
  GROK_IMAGINE_IMAGE_2_PRICING,
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
});
