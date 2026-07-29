import { models } from "./models";

export const DEFAULT_CHAT_MODEL_ID = "openai/gpt-5.6-sol";
export const DEFAULT_EMBEDDING_MODEL_ID = "text-embedding-3-small";

export const chatModelOptions = models.filter(
  (model) =>
    model.active &&
    model.categories.includes("chat") &&
    !model.categories.includes("embeddings") &&
    !model.categories.includes("rerank"),
);

export const embeddingModelOptions = [
  {
    id: DEFAULT_EMBEDDING_MODEL_ID,
    name: "OpenAI: Text Embedding 3 Small",
    provider: "稳定默认",
  },
  ...models
    .filter(
      (model) =>
        model.active &&
        model.categories.includes("embeddings") &&
        model.id !== `openai/${DEFAULT_EMBEDDING_MODEL_ID}`,
    )
    .map((model) => ({
      id: model.id,
      name: model.name,
      provider: model.provider,
    })),
];

export const rerankModelOptions = models
  .filter(
    (model) =>
      model.active &&
      model.output_modalities.includes("rerank"),
  )
  .map((model) => ({
    id: model.id,
    name: model.name,
    provider: model.provider,
  }));
