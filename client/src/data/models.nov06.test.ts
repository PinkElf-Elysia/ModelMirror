import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("November 6 refresh", () => {
  it("keeps Parallel on request-priced Decisions, not free chat", () => {
    const id = "parallel/decisions-fast";
    expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    expect(models.find((model) => model.id === id)).toMatchObject({primary_operation:"decide", ui_entrypoint:"decisions", pricing_basis:"request", pricing_status:"dynamic"});
  });
  it.each(["google/gemini-omni-1.1-flash", "google/gemini-omni-1.2-flash"])("routes %s to dedicated video", (id) => {
    expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    expect(models.find((model) => model.id === id)).toMatchObject({primary_operation:"generate_video", ui_entrypoint:"multimodal", pricing_basis:"media"});
  });
  it("keeps Auto Code on dynamic chat rather than dedicated image generation", () => {
    const model = models.find((item) => item.id === "openrouter/auto-code");
    expect(model).toMatchObject({ui_entrypoint:"chat", pricing_basis:"dynamic"});
    expect(model?.operations).not.toContain("generate_image");
  });
  it("retains explicit per-hour Nemotron transcription pricing", () => {
    expect(models.find((model) => model.id === "nvidia/nemotron-3.5-transcribe")?.media_pricing).toEqual({unit:"audio_hour", usd:0.06000012});
  });
});
