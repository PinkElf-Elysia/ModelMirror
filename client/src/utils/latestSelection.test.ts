import { describe, expect, it } from "vitest";
import { createLatestSelection } from "./latestSelection";

describe("latest asynchronous selection", () => {
  it("rejects an older completion after a newer selection", async () => {
    const gate = createLatestSelection();
    const first = gate.begin();
    const second = gate.begin();
    await Promise.resolve();
    expect(second()).toBe(true);
    expect(first()).toBe(false);
  });
  it("rejects completion after removal, model change or unmount", () => {
    const gate = createLatestSelection();
    const current = gate.begin();
    gate.invalidate();
    expect(current()).toBe(false);
    expect(gate.begin()()).toBe(true);
  });
});
