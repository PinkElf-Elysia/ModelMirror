import { describe, expect, it } from "vitest";
import { models } from "./models";
import { parseModelCompareState, updateModelCompareParams } from "./modelCompareState";

describe("model comparison URL state", () => {
  it("keeps unrelated parameters and restores a valid comparison", () => {
    const ids = models.slice(0, 2).map((model) => model.id);
    const params = updateModelCompareParams(new URLSearchParams("q=vision"), ids, true);
    expect(params.get("q")).toBe("vision");
    expect(parseModelCompareState(params)).toEqual({ ids, active: true });
  });

  it("rejects unknown IDs and limits selection to four models", () => {
    const params = new URLSearchParams("view=compare&compare=missing/model");
    models.slice(0, 5).forEach((model) => params.append("compare", model.id));
    const state = parseModelCompareState(params);
    expect(state.ids).toEqual(models.slice(0, 4).map((model) => model.id));
    expect(state.active).toBe(true);
  });
});
