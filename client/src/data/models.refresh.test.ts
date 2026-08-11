import { describe, expect, it } from "vitest";
import { models } from "./models";

const middleModelIds = [
  "sakana/sakana-namazu",
  "upstage/solar-pro4",
  "meta/muse-glimmer-30b",
  "inclusionai/ling-3.0-tiny:free",
];

describe("OpenRouter 2026-08-11 model refresh", () => {
  it("places Seedance 2.5 at the former GPT-4o Mini TTS slot", () => {
    expect(models[11]?.id).toBe("bytedance/seedance-2.5");
    expect(models[12]?.id).toBe("gpt-4o-mini-tts");
  });

  it("keeps the four general models in the catalog middle", () => {
    const lowerBound = Math.floor(models.length * 0.25);
    const upperBound = Math.ceil(models.length * 0.75);

    for (const modelId of middleModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      const index = models.findIndex((model) => model.id === modelId);
      expect(index).toBeGreaterThan(lowerBound);
      expect(index).toBeLessThan(upperBound);
    }
  });
});
