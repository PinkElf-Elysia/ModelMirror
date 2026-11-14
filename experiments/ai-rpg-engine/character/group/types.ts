export type GroupId = `group-${string}`;
export type MemberId = `member-${string}`;
export type RequestId = `request-${string}`;
export interface VersionReference { workId: `work-${string}`; versionId: `version-${string}` }
export interface Write<T> { operationId: string; expectedRevision: number | null; payload: T }
export interface Budget { calls: number; microUsd: number }
export interface CreateGroup {
 player: { id: string | null; name: string }; scene: string;
 members: Array<{reference: VersionReference; modelId: string}>;
 opening: null | {memberIndex: number; kind: 'ordinary' | 'group'; index: number}; budget: Budget;
}
/** Null input means manual continuation, never a stored player turn. */
export interface Send { targetId: MemberId; input: string | null }
export type RequestStatus = 'prepared' | 'dispatching' | 'completed' | 'failed' | 'unknown';
/** Owner/browser view deliberately excludes full definitions and private data. */
export interface GroupView {
 id: GroupId; revision: number; scene: string;
 members: Array<{id: MemberId; name: string; kind: 'player' | 'character'; active: boolean; modelId: string | null}>;
 messages: Array<{id: string; speakerId: MemberId; content: string; addressedTo: MemberId[]; source: {kind: 'opening' | 'player-input' | 'model-result'; reference?: RequestId; modelId?: string}}>;
 budget: Budget & {usedCalls: number; usedMicroUsd: number};
 requests: Array<{id: RequestId; targetId: MemberId; status: RequestStatus; modelId: string; actualModel: string | null; partial: string; canRecover: boolean; canClose: boolean}>;
}
