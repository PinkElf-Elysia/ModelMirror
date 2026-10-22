import { describe, expect, it } from "vitest";
import { models } from "./models";

const ids = ["recursive/flint-v1.0:free", "baseten/mammoth-1:free", "baseten/elephant-1:free", "nace-ai/drex-max", "nvidia/nemotron-3.5-embed-1b:free", "nvidia/nemotron-3.5-embed-8b:free", "nvidia/nemotron-3.5-rerank-8b:free", "mistralai/voxtral-mini-tts-3-0", "mistralai/voxtral-small-transcribe-3-0", "mistralai/voxtral-mini-transcribe-3-0"];
const byId = new Map(models.map(m => [m.id, m]));
describe("October 22 contracts", () => {
  it.each(ids)("places %s once outside flagship rows", id => {
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeGreaterThanOrEqual(20);
    expect(byId.get(id)?.catalog_status).toBe("live");
  });
  it.each(ids.slice(1, 4))("routes %s to decisions, not chat", id => {
    expect(byId.get(id)).toMatchObject({ ui_entrypoint: "decisions", operations: ["decide"] });
  });
  it.each(ids.slice(4, 7))("keeps %s on the retrieval path", id => {
    expect(byId.get(id)).toMatchObject({ ui_entrypoint: "rag", pricing_status: "free" });
    expect(byId.get(id)?.operations).not.toContain("chat");
    expect(byId.get(id)?.note).toContain("尚未适配");
  });
  it("prices speech by characters and transcription by duration", () => {
    expect(byId.get(ids[7])?.media_pricing).toEqual({unit: "character_million", usd: 13});
    expect(byId.get(ids[8])?.media_pricing?.usd).toBeCloseTo(0.24);
    expect(byId.get(ids[9])?.media_pricing?.usd).toBeCloseTo(0.21);
    for (const id of ids.slice(7)) expect(byId.get(id)?.pricing_basis).toBe("media");
  });
  it("removes Flash Lite Batch only", () => {
    expect(byId.has("google/gemini-2.5-flash-lite")).toBe(true);
    expect(byId.get("google/gemini-2.5-flash-lite")?.serving_variants.filter(v => v.type === "batch")).toEqual([]);
  });
});
