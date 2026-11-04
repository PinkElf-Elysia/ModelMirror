export interface TranscriptTurn {
  input: string;
  raw: string;
}

export const MAX_BYTES: number;
export const MAX_TURNS: number;
export function exportChunks(turns: readonly TranscriptTurn[]): Generator<string, void, unknown>;
export function serialize(turns: readonly TranscriptTurn[]): string;
export function parse(bytes: ArrayBuffer | ArrayBufferView): { turns: TranscriptTurn[]; skipped: number };

type TranscriptCapability = 'session.transcript.import' | 'session.transcript.export';

export function invoke<C extends TranscriptCapability>(request: {
  capability: C;
  session: { id: string };
}): { kind: C; sessionId: string };
