import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,mkdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {commonVoices} from './common-voices.mjs';
import {speechService,createSpeech} from './speech.mjs';

const model='qwen-audio-3.1-tts-flash',assetId='voice_'+'a'.repeat(32),privateId='voice_'+'b'.repeat(32);
const work=new URL('../.rpg04-work/',import.meta.url);
async function fixture(){
 await mkdir(work,{recursive:true});const directory=await mkdtemp(new URL('common-voices-',work));
 let available=true,state='ready',calls=0;
 const service=speechService('http://server:8000',async(url,options)=>{
  if(url.pathname.endsWith('/audio/models'))return Response.json({profiles:[]});
  if(url.pathname.endsWith('/catalog'))return Response.json({available,models:[{id:model,name:model,available}]});
  if(url.pathname.endsWith('/assets'))return Response.json({assets:[{id:assetId,name:'爱莉希雅音色',model,state,usable:available},{id:privateId,name:'私有音色',model,state:'ready',usable:true}]});
  assert.equal(url.pathname,'/api/multimodal/speech');calls++;assert.equal(JSON.parse(options.body).voice_asset_id,assetId);
  const wav=Buffer.alloc(48);wav.write('RIFF');wav.write('WAVE',8);return new Response(wav,{headers:{'content-type':'audio/wav'}});
 });
 const path=join(directory,'common-voices.json');
 const config={version:1,voices:[{id:'elysia',assetId,model}]};
 await writeFile(path,JSON.stringify(config));
 return {directory,path,config,service,setAvailable:v=>available=v,setState:v=>state=v,calls:()=>calls};
}
test('common directory survives route/quota blocks, missing binding and removed asset without exposing credentials',async()=>{
 const f=await fixture();let entries=await commonVoices(f.path,f.service.voices);
 assert.equal(entries[0].available,true);assert.equal(entries[0].voice,'library:'+assetId);assert.equal(entries[0].name,'爱莉希雅音色');
 f.setAvailable(false);entries=await commonVoices(f.path,f.service.voices);assert.equal(entries[0].available,false);assert.equal(entries[0].name,'爱莉希雅音色');
 f.setState('deleted');assert.equal((await commonVoices(f.path,f.service.voices))[0].voice,undefined);
 assert.equal((await commonVoices(f.path+'missing',f.service.voices))[0].available,false);
 await writeFile(f.path,'null');assert.equal((await commonVoices(f.path,f.service.voices))[0].available,false);
 await writeFile(f.path,JSON.stringify({version:1,voices:[null]}));assert.equal((await commonVoices(f.path,f.service.voices))[0].available,false);
 await writeFile(f.path,'{');assert.equal((await commonVoices(f.path,f.service.voices))[0].available,false);
 await writeFile(f.path,JSON.stringify({...f.config,voices:[{...f.config.voices[0],model:'wrong'}]}));assert.equal((await commonVoices(f.path,f.service.voices))[0].available,false);
 await writeFile(f.path,JSON.stringify(f.config));assert.equal((await commonVoices(f.path,{request:async()=>{throw Error('secret');}}))[0].reason,'统一控制面暂不可用');assert.equal(f.calls(),0);
});
test('shared default across cards and new/old sessions; speech grant required; private voices stay gated',async()=>{
 const f=await fixture();let handle=createSpeech(f.directory,f.service),enabled=true;
 const story={revision:0,turns:[]};
 const plugins={sessionStatus:async(id,plugin)=>({installed:plugin==='rpg.speech',enabled:plugin==='rpg.speech'&&enabled}),invoke:async()=>({}),commitResult:async(t,fn)=>fn()};
 async function request(card,id,action,data){let result;await handle({card,id,action,data,method:data?'POST':'GET',query:new URLSearchParams({model}),plugins,store:{read:async()=>structuredClone(story)},send:(status,value)=>{result={status,value};}});return result;}
 for(const [card,id] of [['earth','old-session'],['god','new-session'],['cultivation','new-session']]){
  const c=await request(card,id,'models');assert.equal(c.value.commonVoices[0].name,'爱莉希雅音色');assert.equal(c.value.profiles[0].model_id,model);
  const p=await request(card,id,'profile');assert.deepEqual(p.value.voices,['library:'+assetId]);assert.equal(p.value.labels['library:'+privateId],undefined);
 }
 const data={operationId:randomUUID(),turn:'voice-preview',text:'中性测试。',model,voice:'library:'+assetId,format:'wav',speed:1};
 await assert.rejects(request('earth','old-session','jobs',{...data,voice:'library:'+privateId}),/PLUGIN_NOT_AUTHORIZED/);assert.equal(f.calls(),0);
 assert.equal((await request('earth','old-session','jobs',data)).status,202);
 for(let i=0;i<100;i++){if((await request('earth','old-session','jobs')).value[0]?.state==='complete')break;await new Promise(r=>setTimeout(r,10));}
 assert.equal(f.calls(),1);handle=createSpeech(f.directory,f.service);
 assert.equal((await request('earth','old-session','jobs')).value[0].state,'complete');
 assert.equal((await request('god','new-session','jobs')).value.length,0);
 assert.equal((await request('earth','old-session','jobs',data)).status,200);assert.equal(f.calls(),1);
 f.setAvailable(false);assert.equal((await request('earth','old-session','models')).value.commonVoices[0].available,false);
 await assert.rejects(request('god','new-session','jobs',{...data,operationId:randomUUID()}));assert.equal(f.calls(),1);
 enabled=false;await assert.rejects(request('god','new-session','models'),/PLUGIN_NOT_AUTHORIZED/);
 assert.deepEqual(story,{revision:0,turns:[]});assert.equal(JSON.parse(await readFile(f.path,'utf8')).voices[0].assetId,assetId);
});
