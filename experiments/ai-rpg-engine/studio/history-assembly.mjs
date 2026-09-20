import {assemble} from '../card-replica/lib/assembly.mjs';
import {selectHistory} from '../plugins/history-window.mjs';
import {completeHistory} from './branch-archive.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
// Host reads full raw history. Existing assembly sees the real progress and worldbook scope.
export function assembleHistoryRequest(session,input,policy){
 completeHistory(session);
 if(policy.runtimeHash!==session.runtime.hash)throw fail('HISTORY_POLICY_RUNTIME_MISMATCH');
 const original=assemble({characterText:session.characterText,input,history:session.history,world:session.world});
 const selected=selectHistory({history:session.history,characterText:session.characterText,enabled:policy.enabled,config:policy.config});
 const messages=[original.messages[0],...selected.history,original.messages.at(-1)];
 const record={...selected.record,configRevision:policy.configRevision,policyRevision:policy.revision,authorizationRevision:policy.authorizationRevision,runtimeHash:session.runtime.hash,selectedRequestIds:selected.record.selectedTurns.map(n=>session.turns[n-1].requestId),requestHash:sha(canonical(messages))};
 return {...original,messages,historyPolicy:record};
}
