import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHost} from '../server.mjs';
import {defaults,hash,assemble} from '../lib/assembly.mjs';
import {dispatch,createGeminiProvider,MODEL,ROUTE} from '../lib/gemini-provider.mjs';
const base=fileURLToPath(new URL('../',import.meta.url)),work=join(base,'.local/gemini-real'),prep=join(base,'.local/comparison-openrouter-20260917'),ledgerPath=join(base,'resources/CALL_LEDGER.json');
const mode=process.argv[2];
if(mode==='--freeze'){
 await mkdir(work,{recursive:true});
 const profile=JSON.parse(await readFile(join(prep,'profile.json'),'utf8')),request=JSON.parse(await readFile(join(prep,'prepared-request.json'),'utf8')),characterText=await readFile(join(prep,'character.txt'),'utf8');
 if(hash(characterText)!==profile.characterTextHash||hash(JSON.stringify(request))!==profile.requestHash||JSON.stringify(assemble({characterText,input:'开始这一世。'}).messages)!==JSON.stringify(request.messages))throw Error('Prepared input changed');
 const cert={...request,messages:[{role:'user',content:'Reply with OK.'}],max_tokens:256};
 const paths=['server.mjs','lib/store.mjs','lib/assembly.mjs','lib/render.mjs','lib/gemini-provider.mjs','tools/openrouter-route.py','tools/gemini.mjs','resources/prompts.json','resources/MANIFEST.json','package.json','package-lock.json','src/main.tsx','src/platform.tsx','src/styles.css','.local/gemini-real/transport/mm_transport/__init__.py','.local/gemini-real/transport/mm_transport/egress.py','.local/gemini-real/transport/mm_transport/provider_chat.py'];
 const files=await Promise.all(paths.map(async p=>({path:join(base,p),sha256:hash(await readFile(join(base,p)))})));
 const freeze={at:new Date().toISOString(),model:MODEL,route:ROUTE,characterText,world:'表世界',input:'开始这一世。',params:profile.requestedParameters,requests:{certification:hash(JSON.stringify(cert)),generation:hash(JSON.stringify(request))},files,worldbookEnabled:[],reasoning:'not sent; provider default, not claimed as zero',automaticRetry:false,qualification:'card-scoped single-call stream qualification using frozen ModelMirror transport; not legacy newAPI qualification'};
 for(const [name,data] of [['freeze.json',freeze],['certification-request.json',cert],['generation-request.json',request]])await writeFile(join(work,name),JSON.stringify(data),{flag:'wx'});
 const archive=join(work,'source-at-dispatch');await mkdir(archive,{recursive:true});
 for(const row of files){const rel=row.path.slice(base.length).replaceAll('\\','/');const dest=join(archive,rel);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(row.path),{flag:'wx'});}
 console.log(JSON.stringify({frozen:true,generationRequestHash:freeze.requests.generation,characterHash:hash(characterText),providerDispatches:0}));process.exit(0);
}
if(mode==='--certify'){
 const q=await(await fetch(ROUTE+'/api/status',{signal:AbortSignal.timeout(10000)})).json();
 if(q.qualified){console.log('Existing live qualification valid; no dispatch');process.exit(0);}
 const r=await dispatch({kind:'certification',ledgerPath,work});console.log(JSON.stringify({...r,raw:undefined}));process.exit(0);
}
if(!['--generate','--view'].includes(mode))throw Error('Use --freeze, --certify, --generate or --view');
const freeze=JSON.parse(await readFile(join(work,'freeze.json'),'utf8'));
const host=await createHost({port:18414,directory:join(work,'sessions'),realGenerate:mode==='--generate'?createGeminiProvider({ledgerPath,work}):null});
await writeFile(join(work,'preview.pid'),String(process.pid));
console.log('Gemini review host http://127.0.0.1:18414/');
if(mode==='--generate'){
 const origin='http://127.0.0.1:'+host.port;
 const post=async(path,body)=>{const r=await fetch(origin+path,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error('Host request failed');return r.json();};
 const s=await post('/api/sessions',{characterText:freeze.characterText,world:freeze.world,params:freeze.params,mode:'real'});
 await writeFile(join(work,'active-session.json'),JSON.stringify({sessionId:s.id,requestId:'gemini-first-comparison'}));
 const result=await post('/api/sessions/'+s.id+'/send',{input:freeze.input,requestId:'gemini-first-comparison',revision:0});
 await writeFile(join(work,'host-result.json'),JSON.stringify(result));
 console.log(JSON.stringify({sessionId:s.id,turns:result.turns.length,requests:result.requests,userReviewRequired:true}));
}
