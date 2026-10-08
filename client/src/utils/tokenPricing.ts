import type {
  Model,
  TimeWindowPricingOverride,
  TokenPricing,
} from "../data/models";

export function tokenPricingForPrompt(
  model: Model,
  promptTokens: number,
): TokenPricing {
  const normalizedPromptTokens = Math.max(0, Math.floor(promptTokens));
  return model.pricing_overrides.reduce<TokenPricing>(
    (selected, override) =>
      normalizedPromptTokens >= override.min_prompt_tokens
        ? override.pricing
        : selected,
    model.pricing,
  );
}

function formatTokenThreshold(value: number) {
  if (value >= 1_000_000) return `${value / 1_000_000}M`;
  if (value >= 1_000) return `${value / 1_000}K`;
  return String(value);
}

export function formatPricingOverridesCny(
  model: Pick<Model, "pricing_overrides">,
) {
  return model.pricing_overrides
    .map(
      (override) =>
        `≥${formatTokenThreshold(override.min_prompt_tokens)}：输入 ¥${override.price_cny.input.toFixed(2)} / 输出 ¥${override.price_cny.output.toFixed(2)} / M`,
    )
    .join("；");
}

export function formatUtcClock(value: number) {
  const hours = Math.floor(value / 100);
  const minutes = value % 100;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

export function formatUtcPricingWindow(
  window: Pick<TimeWindowPricingOverride, "utc_start" | "utc_end" | "utc_days">,
) {
  const start = formatUtcClock(window.utc_start);
  const end = formatUtcClock(window.utc_end);
  const days: Record<string, string> = { monday: "周一", tuesday: "周二", wednesday: "周三", thursday: "周四", friday: "周五", saturday: "周六", sunday: "周日" };
  const prefix = window.utc_days?.length ? `${window.utc_days.map((day) => days[day] ?? day).join("、")} ` : "";
  if (window.utc_start === window.utc_end) return `${prefix}全天`;
  return prefix + (window.utc_end <= window.utc_start
    ? `${start}–次日 ${end}`
    : `${start}–${end}`);
}

export function pricingWindowForUtcTime(
  model: Pick<Model, "pricing_time_windows">,
  at = new Date(),
) {
  const clock = at.getUTCHours() * 100 + at.getUTCMinutes();
  const day = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"][at.getUTCDay()];
  return (
    model.pricing_time_windows.find((window) =>
      (!window.utc_days || window.utc_days.includes(day)) && (window.utc_end <= window.utc_start
        ? clock >= window.utc_start || clock < window.utc_end
        : clock >= window.utc_start && clock < window.utc_end),
    ) ?? null
  );
}

export function priceCnyForUtcTime(
  model: Pick<Model, "price_cny" | "pricing_time_windows">,
  at = new Date(),
) {
  return pricingWindowForUtcTime(model, at)?.price_cny ?? model.price_cny;
}
