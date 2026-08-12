import { models } from "./models";

const validModelIds = new Set(models.map((model) => model.id));

export interface ModelCompareState {
  ids: string[];
  active: boolean;
}

export function parseModelCompareState(params: URLSearchParams): ModelCompareState {
  const ids = Array.from(new Set(params.getAll("compare")))
    .filter((modelId) => validModelIds.has(modelId))
    .slice(0, 4);
  return {
    ids,
    active: params.get("view") === "compare" && ids.length >= 2,
  };
}

export function updateModelCompareParams(
  current: URLSearchParams,
  ids: string[],
  active: boolean,
) {
  const next = new URLSearchParams(current);
  next.delete("compare");
  Array.from(new Set(ids))
    .filter((modelId) => validModelIds.has(modelId))
    .slice(0, 4)
    .forEach((modelId) => next.append("compare", modelId));
  if (active && ids.length >= 2) next.set("view", "compare");
  else if (next.get("view") === "compare") next.delete("view");
  return next;
}
