// Integration acceptance: real Node host -> local Python bridge -> mandatory mock provider.
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {hash,need,clone} from '../definition.mjs';
import {exampleState,OPENING} from '../example.mjs';
import {assembleToolsContext} from './adapter.mjs';
import {ToolsHost} from './host.mjs';
import {NativeBridgeProvider} from './bridge-provider.mjs';
const run=new URL('../../.rpg03-work/tools-isolation-mock/',import.meta.url);
const dir=fileURLToPath(run);
const save=async(n,x)=>writeFile(new URL(n,run),JSON.stringify(x,null,2)+'\n',{flag:'wx'});
const initial=exampleState(),input='核对全部已登记丹药与功法，然后服用两份现有丹药；其他状态不变，不新增物资。';
const context=await assembleToolsContext({source:{history:[]},turns:[],events:[],state:initial,stateRevision:0,config:{profiles:[],mods:[]}},input);
context.messages[0].content+='\n\n【本次独立实测场景】人物均为成年人；只进行非性化的药圃日常，不生成成人向内容或选项。开局：'+OPENING;
const first={messages:context.messages,tools:context.tools,tool_choice:'auto'};
need(hash(first)==='b05a79f54058f226a9ef38a16dc374b5c6200c118a370a88e1d3b7fef2c6bf82','FROZEN_INPUT_CHANGED');
const auth=JSON.parse(await readFile(new URL('local-auth.json',run),'utf8'));
const catalog=await(await fetch('http://127.0.0.1:18827/api/rpg/v1/models?profile=god-long-v1',{headers:{Authorization:'Bearer '+auth.token}})).json();
const selection=catalog.models.find(m=>m.model==='google/gemini-3.8-flash'&&m.available);need(selection,'SELECTION');
const provider=await NativeBridgeProvider.open({directory:dir+'transport',enabled:true,baseURL:'http://127.0.0.1:18827/',serviceToken:auth.token,selection,parameterProfile:'god-long-v1',authorization:{id:'mock-only-isolation-validation',maximumCalls:2,perCallUSD:.6,maximumUSD:1.2,expiresAt:new Date(Date.now()+600000).toISOString(),pricing:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'mock-budget-check-not-real-billing'}}});
const host=await ToolsHost.open({directory:dir+'runtime',enabled:true,provider});
try{
 const record=await host.create({operationId:'mock-initial',initial,confirmed:true});
 const action={operationId:'mock-query-use',revision:record.revision,input};
 const started=await host.store.startTurn(record.id,action,{...context,hash:hash(first)});
 need(started.dispatch,'NEW_OPERATION');
 await host.store.update(record.id,r=>{r.operations[action.operationId].native={state:clone(initial),options:{variant:'calm',fault:null},selection:clone(provider.selection),stages:[],query:null};});
 const result=await host.advance(record.id,action.operationId,false);
 await save('session.json',result);
 const ledger=await provider.read();await save('transport-evidence.json',ledger.data);
 const op=result.operations[action.operationId],stages=op.native.stages;
 need(op.status==='completed','MOCK_TURN_NOT_COMPLETED');
 need(stages.length===2&&stages.every(s=>s.result.receipt.dispatched===true&&s.result.receipt.status==='complete'),'MOCK_DISPATCH_RECEIPT');
 need(result.turns.length===1&&result.state.items[0].quantity===3,'MOCK_STATE');
 need(op.native.query.results.length===2&&op.native.query.results[0].entries[0].quantity===5,'MOCK_QUERY');
 const history=await assembleToolsContext(result,'继续查看当前情况。');
 need(!JSON.stringify(history.messages).includes('mock-signature-preserve-exactly'),'TOOL_METADATA_LEAKED');
 need(!history.messages.some(m=>m.role==='tool'),'TOOL_HISTORY_LEAKED');
 const duplicate=await host.turn(record.id,action),recovered=await host.recover(record.id);
 need(duplicate.state.items[0].quantity===3&&recovered.turns.length===1,'DUPLICATE_SETTLEMENT');
 need((await provider.read()).data.calls.length===2,'DUPLICATE_MODEL_STAGE');
 let budgetBlocked=false;
 try{await provider.generate(record.id,'unapproved-third-stage',first);}catch(e){budgetBlocked=e.code==='NATIVE_BUDGET';}
 need(budgetBlocked,'THIRD_STAGE_NOT_BLOCKED');
 const verification={kind:'mock-only-real-host-and-bridge',passed:true,realProviderCalls:0,actualUSD:0,mockProviderRequests:2,requestHash:hash(first),sessionId:record.id,checks:{nativeQuery:true,opaqueSignaturePreserved:true,originalDispatchChecks:true,completeReceipts:true,medicine5to3:true,oneAtomicTurn:true,duplicateAndRecoveryNoReplay:true,thirdStageBudgetBlocked:true,toolMessagesAbsentFromNextStory:true},stages:stages.map(s=>({index:s.index,id:s.id,status:s.status,receipt:s.result.receipt,timing:s.result.timing,syntheticUsage:s.result.usage})),notice:'Model decisions, replies and usage are synthetic. Original routing, DNS validation, HTTP bridge, state query, persistence and commit execute unchanged.'};
 await save('verification.json',verification);
 console.log(JSON.stringify(verification));
}finally{await host.close();}
