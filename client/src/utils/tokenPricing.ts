import type { Model, TokenPricing } from "../data/models";

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
