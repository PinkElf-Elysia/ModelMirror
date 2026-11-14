import type {CharacterVersionReference,CharacterSessionSettings} from '../types';
export type ConversationId = `conversation-${string}`;
export type MemberId = `member-${string}`;
export type MessageId = `message-${string}`;
export interface PlayerSnapshot {id:string|null;name:string}
export interface Member {id:MemberId;kind:'player'|'character';name:string;identity:PlayerSnapshot|null;
 binding:null|{reference:CharacterVersionReference;configurationHash:string;worldInstanceRef:null};
 intervals:{from:number;until:number|null}[]}
export type Visibility={kind:'public'}|{kind:'members';memberIds:MemberId[]};
export type MessageSource={kind:'player-input'|'fixture';reference:string|null}|{kind:'opening';openingIndex:number};
export interface Message {id:MessageId;sequence:number;speakerId:MemberId;content:string;visibility:Visibility;addressedTo:MemberId[];source:MessageSource}
export interface PrivateRecord {id:`private-${string}`;ownerId:MemberId;content:string;source:{kind:'manual';reference:string|null}}
export interface Conversation {format:'modelmirror.rpg.character-conversation/1';id:ConversationId;revision:number;mode:'foundation'|'fixture';worldInstanceRef:null;members:Member[];messages:Message[];privateRecords:PrivateRecord[]}
export interface Operation<T> {operationId:string;expectedRevision:number;payload:T}
export interface Receipt {conversationId:ConversationId;revision:number;memberId?:MemberId;messageId?:MessageId;privateId?:`private-${string}`}
/** Trusted host interface. Not an HTTP authorization token or model wire message. */
export interface MemberView {format:'modelmirror.rpg.character-member-view/1';conversationId:ConversationId;member:Omit<Member,'intervals'>;participants:Pick<Member,'id'|'kind'|'name'>[];messages:Omit<Message,'sequence'|'visibility'>[];privateRecords:PrivateRecord[]}

/** Read-only legacy projection; never accepted as a new stored conversation. */
export interface LegacyPrivateRecord {id:string;ownerId:MemberId;content:string;source:{kind:'legacy-memory';reference:string};legacyEntry:unknown}
export interface LegacyMessage extends Omit<Message,'source'> {source:{kind:'legacy-opening'|'legacy-turn';reference:{sessionId:string;position:number}}}
export interface LegacyOwnerView extends Omit<Conversation,'format'|'mode'|'messages'|'privateRecords'> {format:'modelmirror.rpg.character-legacy-view/1';readOnly:true;legacySessionId:string;settings:CharacterSessionSettings;messages:LegacyMessage[];privateRecords:LegacyPrivateRecord[];requestStates:{operationId:string;state:string;canRecover:boolean}[]}

export type RecoveryResult=null|{id:ConversationId;status:'committed'|'not_committed'};
/** Obtain only inside a trusted host; no browser-supplied caller authority is implied. */
export interface ConversationService {
 recover(expectedId?:ConversationId|null):Promise<RecoveryResult>;
 create(input:{operationId:string;expectedRevision:null;payload:{player:PlayerSnapshot}}):Promise<Receipt>;
 readOwner(id:ConversationId):Promise<Conversation>;
 readMember(id:ConversationId,memberId:MemberId):Promise<MemberView>;
 join(id:ConversationId,input:Operation<{reference:CharacterVersionReference}>):Promise<Receipt>;
 leave(id:ConversationId,input:Operation<{memberId:MemberId}>):Promise<Receipt>;
 rejoin(id:ConversationId,input:Operation<{memberId:MemberId}>):Promise<Receipt>;
 append(id:ConversationId,input:Operation<{speakerId:MemberId;content:string;visibility:Visibility;addressedTo:MemberId[];source:Extract<MessageSource,{kind:'player-input'|'fixture'}>}>):Promise<Receipt>;
 appendOpening(id:ConversationId,input:Operation<{speakerId:MemberId;openingIndex:number}>):Promise<Receipt>;
 addPrivate(id:ConversationId,input:Operation<Pick<PrivateRecord,'ownerId'|'content'|'source'>>):Promise<Receipt>;
 close():Promise<void>;
}

export interface LegacyMemberView extends Omit<MemberView,'format'|'messages'|'privateRecords'> {
 format:'modelmirror.rpg.character-legacy-member-view/1';readOnly:true;legacySessionId:string;memoryEnabled:boolean;
 messages:Omit<LegacyMessage,'sequence'|'visibility'>[];privateRecords:LegacyPrivateRecord[];
}
