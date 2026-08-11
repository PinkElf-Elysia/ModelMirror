export interface PrototypeProviderDiagnostic {
  readonly code: string;
  readonly path: string;
}

export type PrototypeProviderRequest =
  | Readonly<{ kind: "initial"; prompt: string }>
  | Readonly<{
      kind: "repair";
      previousCandidate: string;
      diagnostics: readonly PrototypeProviderDiagnostic[];
    }>;

export interface PrototypeProviderUsage {
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly totalTokens: number;
}

export interface PrototypeProviderResponse {
  readonly candidateText: string;
  readonly model: string;
  readonly usage: PrototypeProviderUsage | null;
}

export interface PrototypeGenerationProvider {
  readonly kind: "openai-compatible";
  readonly model: string;
  requestProposal(request: PrototypeProviderRequest): Promise<PrototypeProviderResponse>;
}

export interface OpenAICompatibleProviderConfig {
  readonly endpoint: string;
  readonly model: string;
  readonly apiKey: string;
}

export declare class PrototypeGeneratorOperationalError extends Error {
  readonly code: "PROTOTYPE_GENERATOR_INTERNAL_ERROR";
}

export declare function createOpenAICompatibleProvider(
  config: OpenAICompatibleProviderConfig,
): PrototypeGenerationProvider;
