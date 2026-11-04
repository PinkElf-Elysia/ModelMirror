import {assemble} from '../card-replica/lib/assembly.mjs';
import {selectHistory} from '../plugins/history-window.mjs';
import {completeHistory} from './branch-archive.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
// Host reads full raw history. Existing assembly sees the real progress and worldbook scope.
const earthAssembly=(session,input)=>{
 if((session.runtime?.descriptor?.cardId&&session.runtime.descriptor.cardId!=='earth')||(session.cardId&&session.cardId!=='earth'))throw fail('CARD_CONTEXT_ADAPTER_REQUIRED');
 return assemble({characterText:session.characterText,input,history:session.history,world:session.world});
};
export function assembleHistoryRequest(session,input,policy,baseAssembly=earthAssembly){
 completeHistory(session);
 if(policy.runtimeHash!==session.runtime.hash)throw fail('HISTORY_POLICY_RUNTIME_MISMATCH');
 const imported=(session.importedTurns||[]).flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
 const original=baseAssembly(imported.length?{...session,history:[...imported,...session.history]}:session,input);
 const selected=selectHistory({history:session.history,characterText:session.characterText,enabled:policy.enabled,config:policy.config});
 const left=policy.enabled?Math.max(0,policy.config.turns*2-selected.history.length):imported.length;
 const prefix=left?imported.slice(-left):[];
 const messages=[original.messages[0],...prefix,...selected.history,original.messages.at(-1)];
 const record={...selected.record,configRevision:policy.configRevision,policyRevision:policy.revision,authorizationRevision:policy.authorizationRevision,runtimeHash:session.runtime.hash,selectedRequestIds:selected.record.selectedTurns.map(n=>session.turns[n-1].requestId),requestHash:sha(canonical(messages))};
 return {...original,messages,historyPolicy:record};
}
