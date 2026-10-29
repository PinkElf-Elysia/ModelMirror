import { describe, expect, it } from "vitest";
import { models } from "./models";
const ids = ["liquid/d1.1", "~liquid/d1-ultra-latest", "~liquid/d1-latest", "amazon/titan-embed-image-v1", "amazon/titan-embed-text-v1", "amazon/titan-embed-text-v2", "openai/gpt-6.1-terra-pro", "openai/gpt-6.1-terra", "heygen/heygen-voice-1-turbo:free", "prism-ml/ternary-bonsai-2-9b", "liquid/lfm-2.5-2.6b"];
describe("October 28 contracts", () => {
  it.each(ids)("keeps %s unique after the sixth row", id => {
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeGreaterThanOrEqual(20);
  });
  it.each(ids.slice(0, 3))("uses decisions for %s", id => {
    expect(models.find(m => m.id === id)).toMatchObject({ui_entrypoint: "decisions", primary_operation: "decide"});
  });
  it.each(ids.slice(3, 6))("uses retrieval for %s", id => {
    expect(models.find(m => m.id === id)).toMatchObject({ui_entrypoint: "rag", primary_operation: "embed"});
  });
  it.each(ids.slice(6, 8))("attaches Batch to %s without duplicate cards", id => {
    expect(models.find(m => m.id === id)?.serving_variants.some(v => v.catalog_id === `${id}:batch`)).toBe(true);
    expect(models.some(m => m.id === `${id}:batch`)).toBe(false);
  });
  it("does not claim image embedding adaptation", () => {
    expect(models.find(m => m.id === ids[3])?.note).toContain("尚未适配");
  });
});
