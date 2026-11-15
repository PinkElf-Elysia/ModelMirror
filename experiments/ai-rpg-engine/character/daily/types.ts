export interface PlayerSnapshot {format:'modelmirror.rpg.player-snapshot/1';profileId:`profile-${string}`|null;profileRevision:number|null;name:string;description:string;avatar:string|null}
/** HTTP accepts a revision reference or temporary public identity, never a trusted snapshot. */
export type PlayerSelection={profileId:`profile-${string}`;revision:number}|{name:string;description:string;avatar:string|null}|{id:string|null;name:string};
export interface SessionMetadata {revision:number|null;title:string;archived:boolean;lastActivity:string|null;observedRevision:number|null}
export interface DailyWrite<T>{operationId:string;expectedRevision:number|null;payload:T}
export interface VisibleMessage{id:string;speaker:string;content:string}
