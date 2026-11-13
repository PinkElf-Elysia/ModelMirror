import {editedHistory,outputCorrections} from './output-edits.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
import {selectHistory,validConfig,POLICY_VERSION} from '../plugins/history-window.mjs';
import {completeHistory} from './branch-archive.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
// Host reads full raw history. Existing assembly sees the real progress and worldbook scope.
const earthAssembly=(session,input)=>{
 if((session.runtime?.descriptor?.cardId&&session.runtime.descriptor.cardId!=='earth')||(session.cardId&&session.cardId!=='earth'))throw fail('CARD_CONTEXT_ADAPTER_REQUIRED');
 return assemble({characterText:session.characterText,input,history:session.history,world:session.world});
};
// The reviewed legacy selector requires a bound character. Free-opening chats
// have none; select complete turns without inventing an initial snapshot.
function selectChatHistory(session,policy){
 if(session.characterText!=='')return selectHistory({history:session.history,characterText:session.characterText,enabled:policy.enabled,config:policy.config});
 if(typeof policy.enabled!=='boolean'||!validConfig(policy.config))throw fail('HISTORY_CONFIGURATION_INVALID',400);
 const totalTurns=session.history.length/2,start=policy.enabled?Math.max(0,totalTurns-policy.config.turns):0;
 return {history:session.history.slice(start*2).map(({role,content})=>({role,content})),record:{policyVersion:POLICY_VERSION,enabled:policy.enabled,config:{...policy.config},totalTurns,selectedTurns:Array.from({length:totalTurns-start},(_,i)=>start+i+1),initialSnapshotAdded:false}};
}
export function assembleHistoryRequest(session,input,policy,baseAssembly=earthAssembly){
 completeHistory(session);
 if(policy.runtimeHash!==session.runtime.hash)throw fail('HISTORY_POLICY_RUNTIME_MISMATCH');
 const imported=(session.importedTurns||[]).flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
 const history=editedHistory(session);
 const original=baseAssembly({...session,history:[...imported,...history]},input);
 const corrections=outputCorrections(session);
 if(corrections)original.messages[0]={...original.messages[0],content:original.messages[0].content+corrections};
 const selected=selectChatHistory({...session,history},policy);
 const left=policy.enabled?Math.max(0,policy.config.turns*2-selected.history.length):imported.length;
 const prefix=left?imported.slice(-left):[];
 const messages=[original.messages[0],...prefix,...selected.history,original.messages.at(-1)];
 const record={...selected.record,configRevision:policy.configRevision,policyRevision:policy.revision,authorizationRevision:policy.authorizationRevision,runtimeHash:session.runtime.hash,selectedRequestIds:selected.record.selectedTurns.map(n=>session.turns[n-1].requestId),requestHash:sha(canonical(messages))};
 return {...original,messages,historyPolicy:record};
}
