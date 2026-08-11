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
  readonly kind: string;
  readonly model: string;
  requestProposal(request: PrototypeProviderRequest): Promise<PrototypeProviderResponse>;
}

export interface OpenAICompatiblePrototypeProvider extends PrototypeGenerationProvider {
  readonly kind: "openai-compatible";
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
): OpenAICompatiblePrototypeProvider;

export interface GeneratePrototypeRequest {
  readonly prompt: string;
}

export interface GeneratePrototypeSuccess {
  readonly ok: true;
  readonly artifacts: Readonly<{
    authoringGamePackJson: string;
    sceneBlueprintJson: string;
    runtimeGamePackJson: string;
    runtimeReceiptJson: string;
    generationReportJson: string;
  }>;
}

export interface GeneratePrototypeFailure {
  readonly ok: false;
  readonly diagnostics: readonly PrototypeGenerationDiagnostic[];
}

export declare function generatePrototype(
  request: GeneratePrototypeRequest,
  provider: PrototypeGenerationProvider,
): Promise<GeneratePrototypeSuccess | GeneratePrototypeFailure>;
import type { PrototypeGenerationDiagnostic } from "@matrix-oasis/prototype-generation-contracts";
