import {CharacterSessionHost} from '../session.mjs';import {AuthoringStore} from '../../authoring/store.mjs';
import {fail} from '../../authoring/contracts.mjs';import {clone,digest,idFor,projectMember} from './contracts.mjs';
export const LEGACY_MEMBER_VIEW='modelmirror.rpg.character-legacy-member-view/1';
export const LEGACY_VIEW='modelmirror.rpg.character-legacy-view/1';
/** Read through the existing validating host. Never opens raw records or invokes recover/send. */
export class LegacyConversationReader {
 #host;#works;
 constructor({host,works}){if(!(host instanceof CharacterSessionHost)||!(works instanceof AuthoringStore))fail('CONVERSATION_HOST_REQUIRED');this.#host=host;this.#works=works;}
 async readOwner(sessionId){
  const s=await this.#host.read(sessionId),c=await this.#works.verifyConfiguration(s.binding.reference.workId,s.binding.reference.versionId);
  if(c.document.kind!=='character'||digest(c)!==s.binding.configurationHash)fail('CONVERSATION_VERSION_CHANGED');
  const id=idFor('conversation','legacy',s.id),playerId=idFor('member',s.id,'player'),characterId=idFor('member',s.id,'character');
  const members=[{id:playerId,kind:'player',name:s.binding.player.name,identity:clone(s.binding.player),binding:null,intervals:[{from:1,until:null}]},
   {id:characterId,kind:'character',name:c.document.character.card.data.name.slice(0,120),identity:null,binding:{reference:clone(s.binding.reference),configurationHash:s.binding.configurationHash,worldInstanceRef:null},intervals:[{from:1,until:null}]}];
  const messages=s.messages.map((m,i)=>({id:idFor('message',s.id,'position',i),sequence:i+1,speakerId:m.role==='user'?playerId:characterId,content:m.content,
   visibility:{kind:'public'},addressedTo:[],source:{kind:i===0?'legacy-opening':'legacy-turn',reference:{sessionId:s.id,position:i}}}));
  const privateRecords=s.memories.map(m=>({id:idFor('private',s.id,'memory',m.id),ownerId:characterId,content:m.content,source:{kind:'legacy-memory',reference:m.id},legacyEntry:clone(m)}));
  return {format:LEGACY_VIEW,id,revision:s.revision,readOnly:true,legacySessionId:s.id,settings:clone(s.settings),worldInstanceRef:null,members,messages,privateRecords,
   requestStates:Object.entries(s.operations).map(([operationId,o])=>({operationId,state:o.state,canRecover:o.canRecover}))};
 }
 async readMember(sessionId,memberId){const state=await this.readOwner(sessionId);return {...projectMember(state,memberId),format:LEGACY_MEMBER_VIEW,readOnly:true,legacySessionId:sessionId,memoryEnabled:state.settings.memoryEnabled};}
}
