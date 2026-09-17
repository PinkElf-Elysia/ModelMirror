import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHost} from '../server.mjs';
import {defaults,assemble,hash,loadFrozen} from '../lib/assembly.mjs';
import {blank,characterText} from '../src/data.ts';
import {reserve,settle,qualification,createProvider,wirePayload,MODEL,ROUTE} from '../lib/provider.mjs';
const base=fileURLToPath(new URL('../',import.meta.url)),work=join(base,'.local/real'),ledgerPath=join(base,'resources/CALL_LEDGER.json');
await mkdir(work,{recursive:true});
const mode=process.argv[2];
async function sourceFreeze(){
 const paths=['server.mjs','lib/store.mjs','lib/assembly.mjs','lib/render.mjs','lib/provider.mjs','src/main.tsx','src/platform.tsx','src/data.ts','src/styles.css','resources/MANIFEST.json','resources/prompts.json','resources/roots.json','package.json','package-lock.json','tools/real.mjs','tools/qualification.py'];
 return Promise.all(paths.map(async p=>({path:join(base,p),sha256:hash(await readFile(join(base,p)))})));
}
if(mode==='--certify'){
 const read=await fetch(ROUTE+'/api/models/provider-chat-control?model_id='+MODEL+'&capability=chat_text',{redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!read.ok)throw Error('Control unavailable');
 const q=await read.json();if(q.available&&q.reason_code==='qualified'){console.log('Qualification already valid; no certification dispatched');process.exit(0);}
 const freeze={kind:'certification',model:MODEL,route:ROUTE,maxTokens:64,source:await sourceFreeze(),qualificationBefore:q,routeOwner:JSON.parse(await readFile(join(base,'.local/route-recovery-20260917/owner.json'),'utf8'))};
 await writeFile(join(work,'certification-freeze.json'),JSON.stringify(freeze,null,2));
 const slot=await reserve(ledgerPath,{kind:'certification',requestHash:hash('Reply with OK.'),freezeHash:hash(JSON.stringify(freeze))});
 const child=spawn('C:\\tmp\\modelmirror-gate-q-venv-4764406c\\Scripts\\python.exe',[join(base,'tools/qualification.py'),'--reserved',slot.id],{cwd:base,windowsHide:true,stdio:['ignore','pipe','ignore']});
 let stdout='';child.stdout.on('data',x=>stdout+=x.toString('utf8'));
 const code=await new Promise((ok,bad)=>{child.once('close',ok);child.once('error',bad);});
 let result;try{result=JSON.parse(stdout.trim());}catch{result={status:'unknown',errorType:'NoSafeReceipt'};}
 await writeFile(join(work,'certification-safe-result.json'),JSON.stringify(result,null,2));
 await settle(ledgerPath,slot.id,{status:code===0&&result.status==='passed'?'complete':'failed',resultHash:hash(JSON.stringify(result)),actualModel:result.actual_model,errorCode:result.error_code??null,evidence:'.local/real/certification-safe-result.json'});
 console.log(JSON.stringify({kind:'certification',slot:slot.slot,result},null,2));process.exit(code===0&&result.status==='passed'?0:1);
}
if(!['--prepare','--generate','--view'].includes(mode))throw Error('Use --prepare, --certify, --generate or --view');
const input='开始这一世。',character={...blank,world:'表世界',name:'林舟',sex:'男性',country:'中国',city:'广州',region:'东亚',year:'1998',yearExact:true,family:'小康之家',height:175,weight:70,body:'标准匀称',hair:'利落短发',look:'沉稳',gifts:['📖一目十行','🤲双手灵巧','🧭方向感强']};
const text=characterText(character),assembled=assemble({characterText:text,input}),wire=wirePayload(assembled.messages,defaults);
const freezePath=join(work,'generation-freeze.json');
if(mode==='--prepare'){
 const freeze={at:new Date().toISOString(),model:MODEL,route:ROUTE,character,input,characterText:text,requestedParameters:defaults,effective:wire.effective,requestHash:hash(JSON.stringify(wire.payload)),promptHashes:loadFrozen().manifest.prompts,files:await sourceFreeze(),automaticRetry:false,worldbookEnabled:[],contextPolicy:'retain all literal history; context UI value is not a truncation limit',routeOwner:JSON.parse(await readFile(join(base,'.local/route-recovery-20260917/owner.json'),'utf8'))};
 await writeFile(freezePath,JSON.stringify(freeze,null,2));await writeFile(join(work,'prepared-request.json'),JSON.stringify(wire.payload,null,2));
 console.log(JSON.stringify({prepared:true,requestHash:freeze.requestHash,effective:freeze.effective,dispatches:0},null,2));process.exit(0);
}
const freeze=JSON.parse(await readFile(freezePath,'utf8'));
const realGenerate=mode==='--generate'?createProvider({ledgerPath,evidenceDirectory:join(work,'dispatches'),freeze}):null;
if(realGenerate)await qualification();
const host=await createHost({port:18413,directory:join(work,'sessions'),realGenerate});
await writeFile(join(work,'preview.pid'),String(process.pid));
console.log('Review host http://127.0.0.1:18413/; first generation only; subsequent output blocked pending user review');
if(realGenerate){
 const origin='http://127.0.0.1:'+host.port,post=async(path,body)=>{const r=await fetch(origin+path,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(body)});const result=await r.json();if(!r.ok)throw Error('Host request failed '+r.status);return result;};
 const s=await post('/api/sessions',{characterText:freeze.characterText,world:freeze.character.world,params:freeze.requestedParameters,mode:'real'});
 await writeFile(join(work,'active-session.json'),JSON.stringify({sessionId:s.id,requestId:'first-author-card-generation'},null,2));
 const result=await post('/api/sessions/'+s.id+'/send',{input:freeze.input,requestId:'first-author-card-generation',revision:0});
 await writeFile(join(work,'host-result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({sessionId:s.id,turns:result.turns.length,requests:result.requests,reviewRequired:true}));
}
