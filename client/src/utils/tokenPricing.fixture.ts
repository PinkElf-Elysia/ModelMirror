import type { Model } from "../data/models";

// Synthetic prices for contract tests, independent of the changing catalog.
export function timeWindowPricingFixture(): Pick<
  Model,
  "price_cny" | "pricing_time_windows"
> {
  const windows: Array<[number, number, boolean]> = [
    [0, 100, false],
    [100, 400, true],
    [400, 600, false],
    [600, 1000, true],
    [1000, 0, false],
  ];
  return {
    price_cny: { input: 2.98, output: 8.94 },
    pricing_time_windows: windows.map(([utc_start, utc_end, peak]) => ({
      utc_start,
      utc_end,
      pricing: peak
        ? { input: 0.44, output: 1.32 }
        : { input: 0.22, output: 0.66 },
      price_cny: peak
        ? { input: 2.98, output: 8.94 }
        : { input: 1.49, output: 4.47 },
    })),
  };
}
