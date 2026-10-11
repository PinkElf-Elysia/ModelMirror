import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {canonical,sha} from '../../plugins/catalog.mjs';
import {serializeSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
const repo=fileURLToPath(new URL('../../../../',import.meta.url)),work=repo+'experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real/';
const origin='http://127.0.0.1:18512';
const json=async p=>JSON.parse(await readFile(p,'utf8'));
const save=(p,value)=>writeFile(work+p,JSON.stringify(value,null,2)+'\n',{flag:'wx'});
async function api(path,data){const r=await fetch(origin+'/rpg-app/god/api/'+path,{method:data?'POST':'GET',headers:data?{Origin:origin,'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify(data)}:{}),signal:AbortSignal.timeout(280000)});const body=await r.json();if(!r.ok)throw Error(body.error||'HTTP_'+r.status);return body;}
const a=await json(work+'AUTHORIZATION.json');
if(a.id!=='rpg-god-b4-20261010'||a.limit!==4||!a.noRetry)throw Error('AUTHORIZATION_MISMATCH');
const setup=await json(repo+'docs/ai-rpg-experiment/god-card/B4-ROLE.json'),expected=serializeSetup(setup);
const action=process.argv[2],status=await api('status');
if(!status.providerEnabled||status.model!==a.model||canonical(status.parameters)!==canonical(a.parameters)||status.budget.limit!==4)throw Error('HOST_CONFIGURATION_MISMATCH');
if(action==='prepare'||action==='recover-preparation'){
 const existing=await api('sessions');
 if(status.budget.used!==0||action==='prepare'&&existing.length||action==='recover-preparation'&&existing.length!==1)throw Error('PREPARATION_ALREADY_EXISTS_CHECK_HISTORY');
 if(action==='prepare')await save('prepare-invocation.json',{at:new Date().toISOString(),noReplay:true});
 const s=action==='prepare'?await api('sessions',{setup,mode:'real'}):await api('sessions/'+existing[0].id),p=await api('sessions/'+s.id+'/memory-palace');
 if(s.characterText!==expected||s.turns.length||p.enabled||p.effectivePolicy.summaryEnabled||p.effectivePolicy.historyPolicy.enabled)throw Error('ZERO_PLUGIN_OR_ROLE_MISMATCH');
 const request={operationId:randomUUID(),expectedSessionRevision:p.sessionRevision,expectedMemoryRevision:p.memoryRevision,expectedContextPolicyRevision:p.effectivePolicy.revision,input:s.characterText};
 const messages=[{role:'system',content:loadFrozen().system},{role:'user',content:expected}];
 await save('prepared.json',{sessionId:s.id,runtime:s.runtime,selection:s.modelSelection.current,setupHash:sha(canonical(setup)),messages,request,model:a.model,parameters:a.parameters,requestMessagesHash:sha(canonical(messages)),authorSystemHash:sha(messages[0].content),characterTextHash:sha(expected),referenceAssistantIncluded:false,pluginsEnabled:false});
 console.log(JSON.stringify({prepared:true,sessionId:s.id,providerCalls:0,openingCharacters:[...expected].length,systemHash:sha(messages[0].content)}));
}else if(action==='dispatch'){
 const p=await json(work+'prepared.json'),s=await api('sessions/'+p.sessionId),m=await api('sessions/'+p.sessionId+'/memory-palace');
 if(status.budget.used!==0||s.turns.length||s.characterText!==expected||p.setupHash!==sha(canonical(setup))||s.runtime.hash!==p.runtime.hash||!s.runtime.compatible||m.effectivePolicy.revision!==p.request.expectedContextPolicyRevision)throw Error('FROZEN_CANDIDATE_CHANGED');
 await save('first-dispatch-invocation.json',{at:new Date().toISOString(),sessionId:s.id,operationId:p.request.operationId,noRetry:true,maxDispatches:1});
 try{
  const result=await api('sessions/'+s.id+'/send',p.request);await save('first-http-result.json',result);
  console.log(JSON.stringify({operation:result.operation,sessionId:s.id,turns:result.turns.length,budget:(await api('status')).budget,manual:'pending'}));
 }catch(e){await save('first-http-unknown.json',{error:e.message,operationId:p.request.operationId,noRetry:true});throw e;}
}else throw Error('ACTION_REQUIRED');
