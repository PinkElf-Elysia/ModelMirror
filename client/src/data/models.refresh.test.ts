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
    const countedModels = models.filter((model) => model.catalog_counted);
    const lowerBound = Math.floor(countedModels.length * 0.25);
    const upperBound = Math.ceil(countedModels.length * 0.75);

    for (const modelId of middleModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      const index = countedModels.findIndex((model) => model.id === modelId);
      expect(index).toBeGreaterThan(lowerBound);
      expect(index).toBeLessThan(upperBound);
    }
  });

  it("adds Seed 2.0 Code as a counted live model below the sixth row", () => {
    const index = models.findIndex(
      (model) => model.id === "bytedance-seed/seed-2.0-code",
    );
    const model = models[index];

    expect(index).toBeGreaterThanOrEqual(2 + 6 * 3);
    expect(model?.catalog_status).toBe("live");
    expect(model?.catalog_counted).toBe(true);
    expect(model?.active).toBe(true);
  });

  it("restores GPT-5.2 Chat as live", () => {
    const model = models.find(
      (candidate) => candidate.id === "openai/gpt-5.2-chat",
    );

    expect(model?.catalog_status).toBe("live");
    expect(model?.active).toBe(true);
  });

  it("keeps uncertain entries callable between live models and routes", () => {
    const uncertain = models.filter(
      (model) => model.catalog_status === "uncertain",
    );
    const ling = uncertain.find(
      (model) => model.id === "inclusionai/ling-3.0-flash:free",
    );
    const lastClearIndex = models.reduce(
      (lastIndex, model, index) =>
        model.catalog_status === "live" ||
        model.catalog_status === "curated"
          ? index
          : lastIndex,
      -1,
    );
    const firstUncertainIndex = models.findIndex(
      (model) => model.catalog_status === "uncertain",
    );
    const firstRouteIndex = models.findIndex(
      (model) => model.catalog_status === "route_variant",
    );

    expect(uncertain).toHaveLength(53);
    expect(ling?.active).toBe(true);
    expect(firstUncertainIndex).toBeGreaterThan(lastClearIndex);
    expect(firstRouteIndex).toBeGreaterThan(firstUncertainIndex);
  });

  it("keeps all route variants usable, uncounted, and at the list bottom", () => {
    const firstRouteIndex = models.findIndex(
      (model) => model.catalog_status === "route_variant",
    );
    const routeVariants = models.slice(firstRouteIndex);

    expect(routeVariants).toHaveLength(62);
    expect(
      routeVariants.every(
        (model) =>
          model.catalog_status === "route_variant" &&
          model.catalog_counted === false &&
          model.active,
      ),
    ).toBe(true);
  });

  it("keeps only explicitly expired models inactive", () => {
    expect(
      models
        .filter((model) => model.catalog_status === "expired")
        .map((model) => model.id)
        .sort(),
    ).toEqual([
      "openai/gpt-5.3-chat",
      "poolside/laguna-m.1",
      "poolside/laguna-m.1:free",
      "tencent/hy3:free",
    ]);
  });
});
