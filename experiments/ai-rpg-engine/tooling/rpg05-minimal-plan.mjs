import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {receiveMinimalText} from '../ui-host/minimal-transport.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),here=path.join(root,'.rpg04-work/rpg05-minimal-plan20');
const digest=v=>createHash('sha256').update(v).digest('hex'),json=v=>JSON.stringify(v,null,2)+'\n';
const read=async p=>JSON.parse(await fs.readFile(p,'utf8'));
const save=async(p,v)=>fs.writeFile(p,json(v),{flag:'wx'});
const mode=process.argv[2];
if(mode==='prepare'){
 await fs.mkdir(here,{recursive:true});
 const v8=path.join(root,'.rpg04-work/rpg05-minimal-v8'),freeze=await read(path.join(v8,'freeze.json'));
 const answer=await fs.readFile(path.join(v8,'response.txt'),'utf8');
 if(digest(answer)!=='ebf62146dc9132e6e1a5ab78bc6dbf7e53584e1e336271386a88076620a17bb9')throw Error('APPROVED_BASELINE_DRIFT');
 const continuation=structuredClone(freeze.payload);
 continuation.messages.push({role:'assistant',content:answer},{role:'user',content:'我看了看竹篓，又看向刚才嘲笑我的人，笑着问：“既然连凡人都不敢久留，你对那地方倒熟悉得很。上次送篓子的人，是怎么回来的？”'});
 const length=structuredClone(freeze.payload);
 length.messages[0].content+='\n\n普通剧情正文可参考800—1200字，充分展开人物互动和事件过程，详略随情境，不必凑字。';
 const cases=[
  {id:1,name:'natural-continuation',purpose:'full approved reply carried forward; no prompt addition',payload:continuation},
  {id:2,name:'single-length-preference',purpose:'same original first turn; only one optional length sentence added',payload:length},
 ];
 const sourceNames=['tooling/rpg05-minimal-plan.mjs','ui-host/minimal-transport.mjs','ui-host/minimal-narrative.mjs','docs/RPG04_PROTOCOL_RUNTIME.txt','runtime/node/sse.mjs'];
 const sources=[];for(const name of sourceNames)sources.push({path:name,sha256:digest(await fs.readFile(path.join(root,name)))});
 const auth={authorization:'user-approved-20-additional-minimal-route-dispatches',quote:'从现在起新增最多20次',maxDispatches:20,priorConsumed:40,totalAuthorized:65,
  oldRemaining:{manualUserOnly:1,previousSupplementPaused:1,previousSixPaused:3},automaticRetry:false,newWebsiteProbes:false,
  model:'gpt-5.6-luna',maxTokens:4096,temperature:0,createdAt:new Date().toISOString()};
 await save(path.join(here,'authorization.json'),auth);
 await save(path.join(here,'user-review.json'),{baselineTextSha256:digest(answer),scope:'v8 first-turn roleplay quality, not entire round',quote:'RPG效果取得重大提升，输出长度确有偏短问题，但不构成对结论的阻塞',accepted:true});
 for(const c of cases){
  const record={...c,authorizationSha256:digest(json(auth)),sources,priorFreezeSha256:digest(await fs.readFile(path.join(v8,'freeze.json')))};
  await save(path.join(here,'case-'+c.id+'.json'),record);
  console.log(JSON.stringify({slot:c.id,name:c.name,caseSha256:digest(json(record)),messageCharacters:c.payload.messages.reduce((n,m)=>n+m.content.length,0)}));
 }
}else if(mode==='execute'){
 const slot=Number(process.argv[3]);
 if(!Number.isSafeInteger(slot)||slot<1||slot>20)throw Error('SLOT_INVALID');
 const auth=await read(path.join(here,'authorization.json'));
 if(auth.maxDispatches!==20||auth.priorConsumed!==40||auth.authorization!=='user-approved-20-additional-minimal-route-dispatches')throw Error('AUTH_DRIFT');
 const caseBytes=await fs.readFile(path.join(here,'case-'+slot+'.json')),c=JSON.parse(caseBytes);
 if(digest(caseBytes)!==process.argv[4]||c.id!==slot||c.authorizationSha256!==digest(json(auth)))throw Error('CASE_DRIFT');
 for(const f of c.sources)if(digest(await fs.readFile(path.join(root,f.path)))!==f.sha256)throw Error('SOURCE_DRIFT');
 const existing=(await fs.readdir(here)).filter(n=>/^dispatch-\d+$/.test(n));
 if(existing.length!==slot-1)throw Error('LEDGER_SEQUENCE');
 for(let i=1;i<slot;i++){
  const prior=await read(path.join(here,'dispatch-'+i,'review.json'));
  if(prior.continue!==true)throw Error('PRIOR_REVIEW_STOP');
 }
 const response=await fetch('http://127.0.0.1:18305/api/models/provider-chat-control?model_id=gpt-5.6-luna&capability=chat_text',{redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!response.ok)throw Error('CONTROL_FAILED');
 const q=await response.json();if(!q.available||q.reason_code!=='qualified')throw Error('QUALIFICATION_REQUIRED');
 const dir=path.join(here,'dispatch-'+slot);await fs.mkdir(dir); // exclusive per-slot reservation; never reused
 await save(path.join(dir,'reservation.json'),{slot,caseSha256:digest(caseBytes),authorizationSha256:digest(json(auth)),at:new Date().toISOString()});
 await save(path.join(dir,'qualification.json'),q);
 const result=await receiveMinimalText(c.payload);
 await fs.writeFile(path.join(dir,'response.sse'),result.raw,{flag:'wx'});
 await fs.writeFile(path.join(dir,'response.txt'),result.text,{flag:'wx'});
 const {text,raw,...meta}=result;
 const receipt={...meta,textSha256:digest(text),rawSha256:digest(raw),characters:[...text].length,slot,newConsumed:slot,newRemaining:20-slot,totalConsumed:40+slot,quality:'pending_review',formalUiTurns:0,at:new Date().toISOString()};
 await save(path.join(dir,'RESULT.json'),receipt);console.log(JSON.stringify(receipt));
 if(result.status!=='transport_complete')process.exitCode=1;
}else throw Error('PREPARE_OR_EXECUTE_REQUIRED');
