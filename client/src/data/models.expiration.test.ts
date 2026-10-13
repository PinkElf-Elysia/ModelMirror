import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("catalog expiration boundary", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers({ toFake: ["Date"] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
  });

  it.each([
    ["2026-10-07T23:59:59.999Z", "live", true],
    ["2026-10-08T00:00:00.000Z", "expired", false],
    ["2026-10-09T00:00:00.000Z", "expired", false],
  ] as const)("evaluates expiration at %s", async (date, status, active) => {
    vi.setSystemTime(new Date(date));
    const { models } = await import("./models");
    expect(models.find((model) => model.id === "baidu/ernie-4.5-vl-424b-a47b")).toMatchObject({
      catalog_status: status,
      catalog_counted: true,
      active,
    });
  });
});
