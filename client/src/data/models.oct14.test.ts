import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("October 14 specialized contracts", () => {
  it("preserves transcription units and ordinary placement", () => {
    const model = models.find(x => x.id === "assemblyai/universal-3-6-pro")!;
    expect(model).toMatchObject({ operations: ["transcribe"], pricing_basis: "media", media_pricing: { unit: "audio_hour" } });
    expect(model.media_pricing?.usd).toBeCloseTo(0.3, 6);
    expect(models.indexOf(model)).toBeGreaterThanOrEqual(2 + 6 * 3);
  });
  it("routes Jev and the new alias to decisions, not chat", () => {
    for (const id of ["typesafe/jev-1.14", "~perplexity/pplx-decider-latest", "~typesafe/jev-latest"]) {
      expect(models.find(x => x.id === id)).toMatchObject({ operations: ["decide"], ui_entrypoint: "decisions", context_length: 262144 });
    }
    expect(models.findIndex(x => x.id === "typesafe/jev-1.14")).toBeGreaterThanOrEqual(2 + 6 * 3);
  });
});
