export type RuntimeScalarValue = boolean | number | string;
export type RuntimeVariableType = "boolean" | "integer" | "enum";
export type RuntimeCueChannel = "visual" | "audio" | "ui";

export interface RuntimeGamePackSource {
  readonly format: "matrix-oasis.authoring-game-pack";
  readonly formatVersion: "0.1.0";
  readonly id: string;
  readonly contentVersion: string;
  readonly canonicalSha256: string;
}

export interface RuntimeEntity {
  readonly id: string;
  readonly label: string;
  readonly description: string | null;
}

export type RuntimeVariable =
  | {
      readonly id: string;
      readonly type: "boolean";
      readonly initial: boolean;
    }
  | {
      readonly id: string;
      readonly type: "integer";
      readonly initial: number;
    }
  | {
      readonly id: string;
      readonly type: "enum";
      readonly allowedValues: readonly string[];
      readonly initial: string;
    };

export interface RuntimeCue {
  readonly id: string;
  readonly channel: RuntimeCueChannel;
  readonly intent: string;
}

export type RuntimeCondition =
  | { readonly op: "all" | "any"; readonly conditions: readonly RuntimeCondition[] }
  | { readonly op: "not"; readonly condition: RuntimeCondition }
  | {
      readonly op: "eq" | "ne";
      readonly variableIndex: number;
      readonly value: RuntimeScalarValue;
    }
  | {
      readonly op: "lt" | "lte" | "gt" | "gte";
      readonly variableIndex: number;
      readonly value: number;
    };

export type RuntimeEffect =
  | {
      readonly op: "set";
      readonly variableIndex: number;
      readonly value: RuntimeScalarValue;
    }
  | {
      readonly op: "add";
      readonly variableIndex: number;
      readonly value: number;
    }
  | { readonly op: "emitCue"; readonly cueIndex: number };

export type RuntimeTarget =
  | { readonly kind: "node"; readonly index: number }
  | { readonly kind: "ending"; readonly index: number };

export interface RuntimeAction {
  readonly id: string;
  readonly label: string;
  readonly entityIndexes: readonly number[];
  readonly when: RuntimeCondition | null;
  readonly effects: readonly RuntimeEffect[];
  readonly target: RuntimeTarget;
}

export interface RuntimeNode {
  readonly id: string;
  readonly title: string;
  readonly text: string | null;
  readonly entityIndexes: readonly number[];
  readonly entryCueIndexes: readonly number[];
  readonly actions: readonly RuntimeAction[];
}

export interface RuntimeEnding {
  readonly id: string;
  readonly title: string;
  readonly text: string | null;
  readonly cueIndexes: readonly number[];
}

export interface RuntimeGamePack {
  readonly format: "matrix-oasis.runtime-game-pack";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly source: RuntimeGamePackSource;
  readonly language: string;
  readonly title: string;
  readonly summary: string | null;
  readonly entryNodeIndex: number;
  readonly entities: readonly RuntimeEntity[];
  readonly variables: readonly RuntimeVariable[];
  readonly cues: readonly RuntimeCue[];
  readonly nodes: readonly RuntimeNode[];
  readonly endings: readonly RuntimeEnding[];
}

export interface RuntimeGamePackReceipt {
  readonly format: "matrix-oasis.runtime-game-pack-receipt";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly compiler: {
    readonly id: "@matrix-oasis/game-pack-compiler";
    readonly version: "0.1.0-r3";
  };
  readonly artifact: {
    readonly format: "matrix-oasis.runtime-game-pack";
    readonly formatVersion: "0.1.0";
    readonly sha256: string;
    readonly byteLength: number;
  };
}

export declare const RUNTIME_GAME_PACK_SCHEMA: Readonly<Record<string, unknown>>;
export declare const RUNTIME_GAME_PACK_FORMAT: "matrix-oasis.runtime-game-pack";
export declare const RUNTIME_GAME_PACK_FORMAT_VERSION: "0.1.0";
export declare const RUNTIME_GAME_PACK_SCHEMA_ID: "urn:matrix-oasis:runtime-game-pack:0.1.0";

export declare const RUNTIME_GAME_PACK_RECEIPT_SCHEMA: Readonly<
  Record<string, unknown>
>;
export declare const RUNTIME_GAME_PACK_RECEIPT_FORMAT: "matrix-oasis.runtime-game-pack-receipt";
export declare const RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION: "0.1.0";
export declare const RUNTIME_GAME_PACK_RECEIPT_SCHEMA_ID: "urn:matrix-oasis:runtime-game-pack-receipt:0.1.0";

export declare const CANONICAL_JSON_PROFILE: "matrix-oasis.canonical-json/1";
export declare const GAME_PACK_COMPILER_ID: "@matrix-oasis/game-pack-compiler";
export declare const GAME_PACK_COMPILER_VERSION: "0.1.0-r3";

export declare class CanonicalJsonValueError extends TypeError {
  readonly code: "CANONICAL_JSON_VALUE_INVALID";
}

export declare class CanonicalJsonOperationalError extends Error {
  readonly code: "CANONICAL_JSON_INTERNAL_ERROR";
}

export declare function canonicalizeJsonValue(value: unknown): string;
