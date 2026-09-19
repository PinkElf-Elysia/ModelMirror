import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {assemble} from '../card-replica/lib/assembly.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {modelRuntime} from '../studio/model-runtime.mjs';
const root=new URL('../.rpg04-work/model-selector-b5/',import.meta.url),origin='http://127.0.0.1:18449';
const action=process.argv[2];assert.equal(action,'prepare');
const save=(name,x)=>writeFile(new URL(name,root),JSON.stringify(x,null,2),'utf8');
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,data}));return data;}
const old=JSON.parse(await readFile(new URL('input-freeze.json',root),'utf8'));
assert.equal(sha(old.input.characterText),old.characterHash);
try{await readFile(new URL('luna-session.json',root));throw Error('ALREADY_PREPARED');}catch(e){if(e.code!=='ENOENT')throw e;}
let s=await api('earth/api/sessions',{characterText:old.input.characterText,world:old.input.world,mode:'real',params:{max_tokens:16384}});await save('luna-session-created.json',{id:s.id});
let catalog=await api('api/plugins'),p=catalog.plugins.find(p=>p.id==='rpg.model-selector');
const base=()=>({pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision});
if(!p.installed){await api('api/plugins/'+p.id+'/install',{...base(),operationId:'b5-install-model'});catalog=await api('api/plugins');p=catalog.plugins.find(p=>p.id==='rpg.model-selector');}
await api('earth/api/sessions/'+s.id+'/plugins/'+p.id+'/enable',{...base(),operationId:'b5-enable-model',sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
const options=await api('earth/api/sessions/'+s.id+'/model-catalog');const m=options.models.find(x=>x.model==='openai/gpt-5.6-luna');assert.ok(m?.available);
s=(await api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'b5-select-luna',expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
assert.deepEqual(s.modelSelection.current.parameters,{max_tokens:16384});assert.equal(s.turns.length,0);assert.ok(s.runtime.compatible);
const assembly=assemble({...old.input,history:[]});assert.deepEqual(assembly.messages,old.messages);assert.equal(assembly.messages.length,2);
const freeze={sessionId:s.id,model:s.modelSelection.current,selectionRevision:s.modelSelection.revision,runtimeHash:modelRuntime.hash,messages:assembly.messages,messagesHash:sha(canonical(assembly.messages)),characterHash:old.characterHash,sourceFreeze:'B5-PRE-AUTH-FREEZE.json',send:{requestId:'b5-luna-output-1',revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,input:old.input.input}};
await save('luna-session.json',s);await save('input-freeze-luna.json',freeze);
console.log(JSON.stringify({prepared:true,sessionId:s.id,parameters:freeze.model.parameters,runtimeHash:freeze.runtimeHash,messagesHash:freeze.messagesHash,paidGeneration:0}));
