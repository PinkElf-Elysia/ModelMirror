// Real local HTTP host + synthetic offline provider only. Never opens shared data.
import {start} from '../../studio/server.mjs';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
const work=fileURLToPath(new URL('../../.rpg04-work/',import.meta.url));await mkdir(work,{recursive:true});
const directory=await mkdtemp(work+'session-history-preview-');
const host=await start({port:Number(process.argv[2]||19873),directory,messageTools:true,autoSummaryV2:true});
const origin='http://127.0.0.1:'+host.server.address().port;
const call=async(path,body)=>{const r=await fetch(origin+'/rpg-app/xingjian/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(JSON.stringify(v));return v;};
const s=await call('sessions',{opening:'姓名：离线验收旅人'}),base='sessions/'+s.id;
for(const id of ['rpg.model-selector','rpg.rolling-summary','rpg.memory-palace','rpg.message-tools'])for(const action of ['install','enable']){
 const c=await call('plugins'),p=c.plugins.find(p=>p.id===id),body={operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision};
 if(action==='enable')Object.assign(body,{sessionId:s.id,expectedSessionRevision:(await call(base)).revision,permissions:p.permissions});
 await call((action==='enable'?base+'/':'')+'plugins/'+id+'/'+action,body);
}
const [model]=(await call(base+'/model-catalog')).models,h=await call(base+'/memory-palace');
await call(base+'/memory-palace/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:model.selectionId,catalogRevision:model.selectionRevision});
for(const [index,input] of ['抵达港口，观察路牌。','前往书院，询问开门时间。'].entries()){
 const h=await call(base+'/memory-palace');
 await call(base+'/send',{operationId:randomUUID(),expectedMemoryRevision:h.memoryRevision,expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input});
 let done=false;for(const deadline=Date.now()+20000;Date.now()<deadline;){const state=await call(base+'/memory-palace');if(!state.busy&&state.processedThrough===index+1){done=true;break;}await delay(100);}if(!done)throw Error('OFFLINE_SEED_DID_NOT_SETTLE');
}
console.log(JSON.stringify({url:origin+'/rpg-app/xingjian/?session='+s.id,id:s.id,origin,directory,offline:true}));
