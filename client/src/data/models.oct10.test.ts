import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("October 10 catalog contracts", () => {
  it("keeps decision models on their dedicated surface after the flagship rows", () => {
    for (const id of ["microsoft/microsoft-decision-1", "cloudflare/clef-omni", "nace-ai/drex-v1.5", "inception/mercury-decide"]) {
      expect(models.find((entry) => entry.id === id)).toMatchObject({ operations: ["decide"], ui_entrypoint: "decisions", catalog_status: "live", pricing_basis: "token" });
      expect(models.findIndex((entry) => entry.id === id)).toBeGreaterThanOrEqual(20);
    }
    expect(models.find((entry) => entry.id === "cloudflare/clef-omni")?.note).toContain("图片状态格式与界面尚未完成适配");
  });

  it("preserves Step 5 multimodal understanding and reasoning", () => {
    expect(models.find((entry) => entry.id === "stepfun/step-5-preview")).toMatchObject({ input_modalities: ["text", "image", "video"], output_modalities: ["text"], context_length: 1000000, pricing: { input: 1, output: 2.7 }, reasoning_declared: true, ui_entrypoint: "chat" });
    expect(models.findIndex((entry) => entry.id === "stepfun/step-5-preview")).toBeGreaterThanOrEqual(20);
  });

  it("retains missing free Sante and attaches new Batch tiers without cards", () => {
    expect(models.find((entry) => entry.id === "inclusionai/ling-3.0-flash-sante")?.catalog_status).toBe("live");
    expect(models.find((entry) => entry.id === "inclusionai/ling-3.0-flash-sante:free")).toMatchObject({ catalog_status: "uncertain", active: true });
    for (const id of ["openai/gpt-6.1-sol", "openai/gpt-6.1-sol-pro"]) {
      expect(models.some((entry) => entry.id === `${id}:batch`)).toBe(false);
      expect(models.find((entry) => entry.id === id)?.serving_variants?.some((variant) => variant.catalog_id === `${id}:batch`)).toBe(true);
    }
  });
});
