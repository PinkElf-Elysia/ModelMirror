import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {minimalNarrativeMessages} from '../ui-host/minimal-narrative.mjs';
import {readSseEvents} from '../runtime/node/sse.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const here=path.join(root,'.rpg04-work/rpg05-minimal-v8');
const old='.rpg04-work/rpg05-experience-retest-v7';
const digest=b=>createHash('sha256').update(b).digest('hex');
const encode=v=>JSON.stringify(v,null,2)+'\n';
const read=async p=>JSON.parse(await fs.readFile(path.join(root,p),'utf8'));
const put=async (name,v)=>fs.writeFile(path.join(here,name),encode(v),{flag:'wx'});
const batches=[
 ['ui-host/.local/real/ledger',26],['ui-host/.local/real/manual/ledger',4],
 ['.rpg04-work/rpg05-narrative-20260913/ledger',1],
 ['.rpg04-work/rpg05-card-narrative-v2/ledger',2],
 ['.rpg04-work/rpg05-player-directed-v3/ledger',1],
 ['.rpg04-work/rpg05-player-directed-retest-v4/ledger',1],
 ['.rpg04-work/rpg05-player-directed-recovery-v5/ledger',2],
 [old+'/ledger',2],
];
async function historyBindings(){
 const files=[];
 for(const [dir,count] of batches){
  const names=(await fs.readdir(path.join(root,dir))).filter(n=>/^operation-.*-00000001\.json$/.test(n)).sort();
  if(names.length!==count)throw Error('PRIOR_QUOTA_CHANGED');
  for(const name of names){const p=dir+'/'+name;files.push({path:p,sha256:digest(await fs.readFile(path.join(root,p)))});}
  if(dir.startsWith('.rpg04-work/')){
   const p=dir.replace(/ledger$/,'continue.json');
   if((await read(p)).nextSlot!==0)throw Error('PRIOR_GATE_OPEN');
   files.push({path:p,sha256:digest(await fs.readFile(path.join(root,p)))});
  }
 }
 return files;
}
if(process.argv[2]==='prepare'){
 await fs.mkdir(here,{recursive:true});
 const sourcePath=old+'/evidence/bundle-request.2-00000001.json';
 const source=(await read(sourcePath)).payload, previous=source.transportRequest;
 const data=JSON.parse(previous.messages[1].content), player=data.player;
 // This first paired sample is intentionally exact and empty-resource. No talent is invented.
 if(player.talents.length||player.possessions.length||player.inherentBackgrounds.length||player.characterPower.status!=='unspecified')
  throw Error('PAIRED_SAMPLE_CHANGED');
 const world=data.resources.worlds.find(r=>r.id===player.world.resourceRef);
 const identity=data.resources.identities.find(r=>r.id===player.currentIdentity.resourceRef);
 const gameplay=previous.messages.map(m=>{try{return JSON.parse(m.content);}catch{return null;}})
  .find(m=>m?.entry?.id==='worldbook.rpg04.shared.original-gameplay')?.entry?.content;
 if(!world||!identity||!gameplay)throw Error('PAIRED_CONTENT_MISSING');
 const labels={name:'姓名',gender:'性别',age:'年龄',appearance:'外貌',personality:'性格',preferences:'偏好',notes:'其他设定'};
 const character=Object.entries(player.character).map(([k,v])=>{
  if(!labels[k])throw Error('UNMAPPED_CHARACTER_FIELD');
  return labels[k]+'：'+(Array.isArray(v)?v.join('；'):v);
 }).join('\n');
 const setupText='开局模式：'+player.opening.mode+'\n世界：'+world.displayName+'\n'+world.description+
  '\n身份：'+identity.displayName+'\n'+identity.description+'\n身份等级：'+identity.rankLabel+
  '\n'+character+'\n玩家未选择天赋、物资或额外背景；未指定人物战力。';
 const messages=minimalNarrativeMessages({cardText:gameplay,setupText,input:previous.input.text});
 const payload={model_id:'gpt-5.6-luna',messages,temperature:0,max_tokens:4096,gateway:'default',
  tool_mode:'none',compression:{mode:'off'},output_mode:'none',require_managed_route:true};
 const files=[];
 for(const p of ['ui-host/minimal-narrative.mjs','tooling/rpg05-minimal-contrast.mjs','tooling/context-protocol.mjs',
  'docs/RPG04_PROTOCOL_RUNTIME.txt','runtime/node/sse.mjs','tests/ui-host-minimal-narrative.test.mjs',sourcePath])
  files.push({path:p,sha256:digest(await fs.readFile(path.join(root,p)))});
 const freeze={format:'rpg05-minimal-contrast/1',createdAt:new Date().toISOString(),
  authorization:'user-approved-six-new-experience-retests',cohortLimit:6,priorCohortConsumed:2,
  maxLocalDispatches:1,cohortUnallocatedAfterThis:3,priorTotalConsumed:39,totalAuthorized:45,
  manualReserved:1,oldSupplementReserved:1,automaticRetry:false,sourceRequestSha256:source.requestSha256,
  payload,files,prior:await historyBindings(),scope:'diagnostic API comparison, not a formal UI turn; no output schema or content repair'};
 await put('freeze.json',freeze);
 console.log(JSON.stringify({freezeSha256:digest(encode(freeze)),messages:messages.length,
  messageCharacters:messages.reduce((n,m)=>n+m.content.length,0),previousMessageCharacters:previous.messages.reduce((n,m)=>n+m.content.length,0),
  responseFormat:false,providerCalls:0,localLimit:1}));
}else if(process.argv[2]==='execute'){
 const bytes=await fs.readFile(path.join(here,'freeze.json')),freeze=JSON.parse(bytes);
 if(digest(bytes)!==process.argv[3]||freeze.maxLocalDispatches!==1||freeze.priorCohortConsumed!==2||freeze.cohortLimit!==6)
  throw Error('FREEZE_MISMATCH');
 for(const f of [...freeze.files,...freeze.prior])if(digest(await fs.readFile(path.join(root,f.path)))!==f.sha256)throw Error('SOURCE_OR_HISTORY_DRIFT');
 await historyBindings();
 const controlResponse=await fetch('http://127.0.0.1:18305/api/models/provider-chat-control?model_id=gpt-5.6-luna&capability=chat_text',{redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!controlResponse.ok)throw Error('CONTROL_UNAVAILABLE');
 const control=await controlResponse.json();
 if(!control.available||control.reason_code!=='qualified')throw Error('QUALIFICATION_NOT_CURRENT');
 await put('qualification.json',control);
 // Exclusive reservation is the irreversible dispatch boundary; any uncertainty consumes this slot.
 await put('reservation.json',{slot:1,authorizationSlot:3,priorTotalConsumed:39,requestSha256:digest(JSON.stringify(freeze.payload)),reservedAt:new Date().toISOString()});
 let text='',receipt=null,finishReason=null,done=false,raw=[],rawBytes=0,error=null,observedModel=null,httpStatus=null;
 try{
  const response=await fetch('http://127.0.0.1:18305/api/chat',{method:'POST',redirect:'error',
   headers:{'content-type':'application/json',accept:'text/event-stream'},body:JSON.stringify(freeze.payload),signal:AbortSignal.timeout(60000)});
  httpStatus=response.status;
  if(!response.ok||!response.headers.get('content-type')?.includes('text/event-stream'))throw Error('HTTP_'+response.status);
  const capture={async *[Symbol.asyncIterator](){for await(const b of response.body){rawBytes+=b.length;if(rawBytes>8*1024*1024)throw Error('STREAM_LIMIT');raw.push(Buffer.from(b));yield b;}}};
  const parsed=await readSseEvents(capture,{onEvent(event){
   if(event.data==='[DONE]'){done=true;return;}
   const value=JSON.parse(event.data);
   if(event.event==='route_receipt'){receipt=value;return;}
   if(event.event!=='message')throw Error('UNEXPECTED_EVENT');
   if(value.error)throw Error('UPSTREAM_ERROR');
   if(value.model)observedModel=value.model;
   if(value.choices?.length===0&&value.usage)return;
   const choice=value.choices?.[0];
   if(choice?.delta?.refusal)throw Error('REFUSAL');
   if(typeof choice?.delta?.content==='string')text+=choice.delta.content;
   if(choice?.finish_reason)finishReason=choice.finish_reason;
  }});
  if(!parsed.valid)throw Error(parsed.diagnostics[0].code);
  if(!done||finishReason!=='stop'||receipt?.actual_model!=='gpt-5.6-luna'||receipt?.fallback_attempts!==0||
    !receipt?.reason_codes?.includes('qualified')||observedModel&&observedModel!=='gpt-5.6-luna')throw Error('INCOMPLETE_OR_ROUTE_MISMATCH');
 }catch(e){error=e.code??e.message??'UNKNOWN';}
 await fs.writeFile(path.join(here,'response.sse'),Buffer.concat(raw),{flag:'wx'});
 await fs.writeFile(path.join(here,'response.txt'),text,{flag:'wx'});
 const result={status:error?'failed_or_unknown':'transport_complete',quality:'pending_review',httpStatus,error,finishReason,done,observedModel,receipt,
  rawSha256:digest(Buffer.concat(raw)),textSha256:digest(text),textCharacters:[...text].length,
  cohortConsumed:3,cohortRemaining:3,totalConsumed:40,localDispatches:1,automaticRetry:false,formalUiTurns:0,completedAt:new Date().toISOString()};
 await put('RESULT.json',result);console.log(JSON.stringify(result));
 if(error)process.exitCode=1;
}else throw Error('USE_PREPARE_OR_EXPLICIT_EXECUTE');
