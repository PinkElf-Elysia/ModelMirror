import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("October 12 model contracts", () => {
  it("adds multimodal Wonder without changing default placement", () => {
    const model = models.find((entry) => entry.id === "meta/muse-wonder");
    expect(model).toMatchObject({ context_length: 1048576, pricing: { input: 4, output: 20 }, reasoning_declared: true, ui_entrypoint: "chat" });
    expect(model?.input_modalities).toEqual(expect.arrayContaining(["text", "image", "audio", "video", "file"]));
    expect(model?.output_modalities).toEqual(["text"]);
    expect(model?.note).toContain("WAV");
  });
  it("preserves exact decision and alias identities on the dedicated surface", () => {
    for (const [id, price] of [["nace-ai/drex-v2.0", 0.06], ["~perplexity/pplx-decider-27b-latest", 0.02]] as const) {
      expect(models.find((entry) => entry.id === id)).toMatchObject({ operations: ["decide"], ui_entrypoint: "decisions", pricing: { input: price, output: 0 }, catalog_status: "live" });
    }
    expect(models.find((entry) => entry.id === "~perplexity/pplx-decider-27b-latest")?.note).toContain("图片能力尚未在本站适配");
  });
  it("keeps all three new cards after the first six rows", () => {
    for (const id of ["meta/muse-wonder", "nace-ai/drex-v2.0", "~perplexity/pplx-decider-27b-latest"]) {
      expect(models.findIndex((entry) => entry.id === id)).toBeGreaterThanOrEqual(20);
    }
  });
});
