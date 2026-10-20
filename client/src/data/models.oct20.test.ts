import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("October 20 catalog contracts", () => {
  it.each(["minimax/minimax-m3.1-flash:free", "inception/mercury-3", "unbiased/pareto-26.10"])("places %s once after six rows", id => {
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeGreaterThanOrEqual(20);
  });
  it("keeps MiniMax free multimodal understanding separate from generation", () => {
    const model = models.find(m => m.id === "minimax/minimax-m3.1-flash:free")!;
    expect(model).toMatchObject({ context_length: 1000000, input_modalities: ["text", "image", "video"], output_modalities: ["text"], pricing_status: "free", pricing: {input: 0, output: 0}, reasoning_declared: true, ui_entrypoint: "chat" });
    expect(model.operations).toEqual(expect.arrayContaining(["chat", "analyze_image", "analyze_video"]));
    expect(model.operations).not.toContain("generate_video");
    expect(model.operations).not.toContain("generate_image");
  });
  it("uses post-promotion ElevenLabs rates with their original units", () => {
    for (const m of models.filter(m => m.id.startsWith("elevenlabs/"))) {
      if (m.media_pricing?.unit === "character_million") expect(m.media_pricing.usd).toBe(m.pricing.input);
      if (m.media_pricing?.unit === "audio_hour") expect(m.media_pricing.usd).toBeCloseTo(0.22);
      expect(m.note).not.toContain("目录价已含首发五折");
    }
  });
  it("removes unavailable Batch modes without deleting base models", () => {
    for (const id of ["google/gemini-2.5-flash", "google/gemini-2.5-pro"]) {
      const model = models.find(m => m.id === id)!;
      expect(model).toBeDefined();
      expect(model.serving_variants.filter(v => v.type === "batch")).toEqual([]);
    }
  });
});
