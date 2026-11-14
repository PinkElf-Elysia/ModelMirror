import {savedProposal,replayThenNarrate} from './recovery.mjs';
import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {ActionHost} from '../host.mjs';import {actionAdapters} from '../adapters.mjs';
import {runtimeManifest} from '../../manifest.mjs';import {ActionRealProvider,MODEL,SLOTS} from './provider.mjs';import {hash,clone,need} from '../../contracts.mjs';
const [mode,path,port]=process.argv.slice(2),root=process.cwd(),directory=resolve(path||'.');
need(['mock','real'].includes(mode)&&directory.startsWith(resolve(root,'experiments/ai-rpg-engine/.rpg03-work/actions-r5-'+mode+'-'))&&port===({mock:'18872',real:'18873'}[mode]),'R5_REAL_CLI');
const save=(name,data)=>writeFile(join(directory,name),typeof data==='string'?data:JSON.stringify(data,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(await readFile(join(directory,'local-auth.json'),'utf8')),url='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+auth.token};
let ledger,host,record,initial,view,confirmation=null,error=null,recovered=null;
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8')),ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'R5_REAL_MODE');
 const selections={};for(const profile of ['god-long-v1']){const response=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(response.ok,'R5_REAL_CATALOG');selections[profile]=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'R5_REAL_SELECTION');}
 const authorization={id:'actions-r5-'+mode+'-'+directory.split(/[\\/]/).at(-1),kind:'saved-proposal-narration',maximumCalls:1,maximumUSD:.30,expiresAt:new Date(Date.now()+86400000).toISOString(),pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 const manifest=await runtimeManifest([new URL('./recover-run.mjs',import.meta.url)],[{url:new URL('./protocol.py',import.meta.url)},{url:new URL('./wire.mjs',import.meta.url)},{url:new URL('./bridge.py',import.meta.url)},{url:new URL('./launch.py',import.meta.url)}]);
 const input='我准备服用两份已登记的养息丹。请依据当前记录拟定方案，先不要执行；其他状态保持不变。';
 await save('execution-claim.json',{mode,model:MODEL,authorization,selections,manifest,input,slots:{3:SLOTS[3]},retries:0,confirmationPolicy:{actionId:'use_medicine',itemId:'medicine',quantity:2,expectedBefore:5,expectedAfter:3,kind:'explicitly-authorized-test-confirmation'}});
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'rpg-actions-r5-real-budget',recoverDeadOwner:true});
 const realProvider=new ActionRealProvider({records:ledger,url,serviceToken:auth.token,selections,authorization,mode});
 const adapter=(await actionAdapters()).find(a=>a.id==='cultivation');adapter.binding.manifestHash=manifest.hash;
 recovered=await savedProposal(resolve(root,'experiments/ai-rpg-engine/.rpg03-work/actions-r5-real-v1'),adapter);
 await save('recovery-source.json',recovered.source);
 const provider=replayThenNarrate(realProvider,recovered);
 host=await ActionHost.open({directory:join(directory,'cultivation'),adapter,offlinePreview:mode==='mock',controlledReal:mode==='real',provider});
 initial=adapter.initial();await save('initial.json',initial);
 record=await host.create({operationId:'create-r5',initial,confirmed:true,source:{kind:'new',id:'r5-adult-neutral-real',history:[]}});
 console.log('Replaying saved native proposal without provider dispatch: '+mode);
 record=await host.turn(record.id,{operationId:'plan',revision:record.revision,input});await save('proposal-session.json',record);
 await save('recovered-tool.json',{message:recovered.result.message,source:recovered.source});
 need(record.operations.plan.status==='proposed'&&!record.pending,'R5_NO_VALID_PROPOSAL');
 const p=record.proposals.plan.proposal;
 need(hash(p.action)===hash({actionId:'use_medicine',itemId:'medicine',quantity:2})&&hash(record.state)===hash(initial)&&record.events.length===0,'R5_PROPOSAL_MISMATCH');
 confirmation={kind:'explicitly-authorized-test-confirmation',at:new Date().toISOString(),proposalId:'plan',proposalHash:p.hash,stateRevision:record.stateRevision,expected:{before:5,after:3},actor:'player',generatedByModel:false};
 await save('confirmation.json',confirmation);
 record=await host.execute(record.id,{operationId:'execute-r5-once',revision:record.revision,proposalId:'plan',proposalHash:p.hash});
 const expected=clone(initial);expected.items.find(i=>i.id==='medicine').quantity=3;
 need(hash(expected)===hash(record.state)&&record.events.filter(e=>e.kind==='action').length===1,'R5_EXECUTION_MISMATCH');
 await save('executed-session.json',record);
 const refreshed=await host.store.read(record.id);need(hash(refreshed.state)===hash(expected)&&refreshed.events.filter(e=>e.kind==='action').length===1,'R5_REFRESH_MISMATCH');console.log('Confirmed exactly once: medicine 5 -> 3; generating receipt narration.');
 record=await host.narrate(record.id);
 need(hash(expected)===hash(record.state),'R5_NARRATION_CHANGED_STATE');
}catch(e){error=e.code||e.message;await save('execution-error.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 if(host&&record){record=await host.store.read(record.id);view=await host.view(record.id);await save('session.json',record);}
 const data=ledger?(await ledger.get('actions-r5-budget',x=>x))?.data||{calls:[],reservedMicros:0}:{calls:[],reservedMicros:0};
 await save('transport-evidence.json',data);
 for(const c of data.calls){await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');await save('stage-'+c.slot+'.json',c);}
 const expected=initial?clone(initial):null;if(expected)expected.items.find(i=>i.id==='medicine').quantity=3;
 await save('review.json',{mode,model:MODEL,error,recoverySource:recovered?.source,initial,confirmation,view,exactExpectedState:record?hash(expected)===hash(record.state):false,calls:data.calls.map(c=>({slot:c.slot,capUSD:c.capUSD,reservedUSD:c.reserveMicros/1e6,status:c.result?.status,finishReason:c.result?.finishReason,usage:c.result?.usage??null,accounting:c.result?.accounting??null,timing:c.result?.timing??null,toolCalls:c.result?.message?.tool_calls??null})),reservedUSD:data.reservedMicros/1e6,limits:{calls:1,totalUSD:.30,perCallUSD:[.30],retries:0}});
 if(host)await host.close();if(ledger)await ledger.close();
 const stop=await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'R5_BRIDGE_STOP');
}
if(mode==='mock')need(!error&&record?.turns.length===1&&!record.pending,'R5_MOCK_CHAIN');
console.log('Saved raw native messages, original prose, state and statistics.');
