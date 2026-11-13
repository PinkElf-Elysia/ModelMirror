// Host API payloads are validated by the comparison client before rendering.
export type ComparisonBatchReference = {plan: {id: string}};
export function mountComparison(root: HTMLElement, options: {
  api: (path: string, body?: unknown) => Promise<unknown>;
  base: string; sessionId: string; getInput: () => string; setInput: (value: string) => void;
  onChange?: (batch: ComparisonBatchReference) => Promise<void>;
  onBlocked?: (blocked: boolean, operationIds: string[]) => void;
  prepare: (batch: ComparisonBatchReference, candidateId: string) => Promise<unknown>;
  release?: (token: string) => Promise<unknown>;
  creator?: boolean; storage?: Storage; enabled?: boolean;
}): {open: (batchId?: string | null) => Promise<void>; refresh: () => Promise<void>; setEnabled: (enabled: boolean) => void; dispose: () => void};
