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
      acceptanceProfile?: PrototypeAcceptanceProfile;
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

export interface PrototypeAcceptanceRange {
  readonly min: number;
  readonly max: number;
}

export interface PrototypeAcceptanceProfile {
  readonly format: "matrix-oasis.prototype-acceptance-profile";
  readonly formatVersion: "0.1.0";
  readonly nodes: PrototypeAcceptanceRange;
  readonly endings: PrototypeAcceptanceRange;
  readonly actions: PrototypeAcceptanceRange;
  readonly zones: PrototypeAcceptanceRange;
  readonly props: PrototypeAcceptanceRange;
  readonly characterPlaceholders: PrototypeAcceptanceRange;
  readonly requireReachableCycle: boolean;
  readonly requireAllEndingsReachable: boolean;
  readonly requireAllNonEnvironmentBriefsBound: boolean;
}

export interface GeneratePrototypeOptions {
  readonly acceptanceProfile: PrototypeAcceptanceProfile;
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
  options?: GeneratePrototypeOptions,
): Promise<GeneratePrototypeSuccess | GeneratePrototypeFailure>;
import type { PrototypeGenerationDiagnostic } from "@matrix-oasis/prototype-generation-contracts";
