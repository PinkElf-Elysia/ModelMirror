import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("October 23 snapshot contracts", () => {
  it("places Kimi once within the first six rows, preserving multimodal chat", () => {
    const id = "moonshotai/kimi-k3.1-pro";
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeLessThan(20);
    expect(models.find(m => m.id === id)).toMatchObject({
      catalog_status: "live", ui_entrypoint: "chat", context_length: 1048576,
      input_modalities: ["text", "image", "video"], output_modalities: ["text"],
      pricing: { input: 2, output: 10 },
      supported_parameters: expect.arrayContaining(["tools", "reasoning", "structured_outputs"]),
    });
  });
  it("keeps paid and missing free MiniMax identities separate", () => {
    expect(models.findIndex(m => m.id === "minimax/minimax-m3.1-flash")).toBeGreaterThanOrEqual(20);
    expect(models.find(m => m.id === "minimax/minimax-m3.1-flash")).toMatchObject({catalog_status: "live", pricing: {input: 0.15, output: 0.6}});
    expect(models.find(m => m.id === "minimax/minimax-m3.1-flash:free")).toMatchObject({catalog_status: "uncertain", active: true});
  });
});
