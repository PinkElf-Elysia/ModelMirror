/** Public boundaries; runtime validation remains authoritative. Unknown JSON keys round-trip. */
export type Json = null | boolean | number | string | Json[] | {[key: string]: Json};
export interface BookEntry {
  keys: string[]; content: string; enabled: boolean; insertion_order: number;
  extensions: Record<string, Json>; id?: string | number; name?: string; comment?: string;
  case_sensitive?: boolean; selective?: boolean; secondary_keys?: string[];
  constant?: boolean; priority?: number; position?: 'before_char' | 'after_char';
  use_regex?: boolean; [unknown: string]: unknown;
}
export interface CharacterBook {
  entries: BookEntry[]; extensions: Record<string, Json>; name?: string; description?: string;
  scan_depth?: number; token_budget?: number; recursive_scanning?: boolean; [unknown: string]: unknown;
}
export interface CharacterData {
  name: string; description: string; personality: string; scenario: string;
  first_mes: string; mes_example: string; creator_notes: string; system_prompt: string;
  post_history_instructions: string; alternate_greetings: string[]; tags: string[];
  creator: string; character_version: string; extensions: Record<string, Json>;
  character_book?: CharacterBook; [unknown: string]: unknown;
}
export type CharacterCard =
  | {spec: 'chara_card_v2'; spec_version: '2.0'; data: CharacterData; [unknown: string]: unknown}
  | {spec: 'chara_card_v3'; spec_version: string; data: CharacterData & {
      group_only_greetings: string[]; nickname?: string; source?: string[];
      assets?: {type: string; uri: string; name: string; ext: string; [unknown: string]: unknown}[];
      creator_notes_multilingual?: Record<string,string>; creation_date?: number; modification_date?: number;
    }; [unknown: string]: unknown};
export interface CharacterSource {
  sha256: string; size: number; container: 'json' | 'png'; selection: null | 'chara' | 'ccv3';
}
export interface CompatibilityReport {
  format: CharacterCard['spec']; version: string;
  warnings: {code: string; path: string; message: string}[]; sources: {v2: string; v3: string};
}
/** Additional branch on the existing authoring document; metadata/settings retain existing types. */
export interface CharacterDocumentBranch {
  format: 'modelmirror.rpg.character-document/1'; kind: 'character';
  character: {card: CharacterCard; source: CharacterSource | null};
}
export interface CharacterVersionReference {workId: `work-${string}`; versionId: `version-${string}`}
export interface CharacterInstanceBinding {
  reference: CharacterVersionReference; configurationHash: string; openingIndex: number;
  openingText: string; player: {id: string | null; name: string};
  /** Reserved reference only. No capability to read/write any world state. */
  worldInstanceRef: null;
}
export interface CreateCharacterSession {
  operationId: string; reference: CharacterVersionReference; openingIndex: number;
  player: CharacterInstanceBinding['player'];
}
export interface CharacterSessionSettings {memoryEnabled: boolean; historyTurns: number | null}
export interface CharacterChatMessage {role: 'system' | 'user' | 'assistant'; content: string}
