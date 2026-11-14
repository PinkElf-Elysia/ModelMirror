import test from 'node:test';
import assert from 'node:assert/strict';
import {defaults,readSettings,appendTranscript,recordingFormat,profile,catalog,transcribe,capture,encodeWav} from '../src/voice-input-client.mjs';
const response=v=>new Response(JSON.stringify(v),{headers:{'content-type':'application/json'}});
const models={microphone_enabled:true,profiles:[{model_id:'voice/stt',display_name:'STT',invocable:true,interaction_status:'ready',input_formats:['wav'],chat_modes:['transcribe']},{model_id:'tts',chat_modes:['synthesize_speech']}]};
test('settings default, validation, draft preservation and overflow',()=>{
 assert.deepEqual(readSettings({getItem:()=>null}),defaults);
 assert.throws(()=>readSettings({getItem:()=>'{}'}));
 assert.equal(appendTranscript('原草稿','新识别'),'原草稿\n新识别');
 assert.equal(appendTranscript('',' 新识别 '),'新识别');
 assert.throws(()=>appendTranscript('a'.repeat(20000),'新增'));assert.throws(()=>appendTranscript('草稿',' '));
});
test('model catalog and exact unified control block failed routes, never fall back',async()=>{
 let calls=[];const fetcher=async(url)=>{calls.push(url);return response(url.includes('audio/models')?models:{feature_enabled:true,status:'managed_required',available:true,certified_input_formats:['wav']});};
 assert.equal((await catalog(fetcher)).profiles.length,1);
 assert.deepEqual(await profile('',fetcher),{model:'voice/stt',name:'STT',formats:['wav'],managed:true});
 assert.ok(calls.some(x=>x.includes('entry_id=multimodal_transcription')&&x.includes('execution_shape=audio_transcription')));
 await assert.rejects(profile('missing',fetcher),/已不在目录/);
 await assert.rejects(profile('',async url=>response(url.includes('audio/models')?models:{status:'degraded_required',available:false})),/不可用/);
 await assert.rejects(profile('',async()=>response({...models,microphone_enabled:false})),/尚未开放/);
});
test('legacy only allows ready models; managed certified formats override catalog',async()=>{
 const f=async url=>response(url.includes('audio/models')?models:{status:'legacy'});
 assert.equal((await profile('voice/stt',f)).managed,false);
 const unavailable={...models,profiles:[{...models.profiles[0],invocable:false}]};
 await assert.rejects(profile('voice/stt',async url=>response(url.includes('audio/models')?unavailable:{status:'legacy'})),/不可用/);
});
test('recording selects browser intersection or local WAV conversion',()=>{
 assert.equal(recordingFormat(['webm'],()=>true).convert,false);
 assert.equal(recordingFormat(['wav'],()=>true).convert,true);
 assert.throws(()=>recordingFormat(['mp3'],()=>true),/不兼容/);
 assert.throws(()=>recordingFormat(['wav'],()=>false),/不兼容/);
});
test('WAV output is mono 16-bit PCM with correct length and clipped samples',async()=>{
 const blob=encodeWav([new Float32Array([-2,0,2])],16000),v=new DataView(await blob.arrayBuffer());
 assert.equal(blob.type,'audio/wav');assert.equal(blob.size,50);assert.equal(v.getUint16(22,true),1);assert.equal(v.getUint32(24,true),16000);assert.equal(v.getInt16(44,true),-32768);assert.equal(v.getInt16(48,true),32767);
});
test('transcription uses same-origin main endpoint, multipart exact model and idempotency, once',async()=>{
 let calls=0;const r=await transcribe({blob:new Blob(['wav']),extension:'wav',model:'voice/stt',language:'zh',id:'operation'},async(url,init)=>{
  calls++;assert.equal(url,'/api/multimodal/transcriptions');assert.equal(init.headers['Idempotency-Key'],'operation');assert.equal(init.body.get('model_id'),'voice/stt');assert.equal(init.body.get('language'),'zh');assert.equal(init.body.get('file').name,'recording.wav');return response({text:'你好'});
 });assert.equal(r.text,'你好');assert.equal(calls,1);
 await assert.rejects(transcribe({blob:new Blob(['wav']),extension:'wav',model:'voice/stt',language:'auto',id:'x'},async()=>{calls++;throw Error('lost');}));assert.equal(calls,2);
});
test('permission resolving after cancel releases tracks without constructing recorder',async()=>{
 let resolve,stopped=0,constructed=0;
 const r=capture({mediaDevices:{getUserMedia:()=>new Promise(r=>resolve=r)},Recorder:class{constructor(){constructed++;}},seconds:60,format:{mime:'audio/webm'},onState:()=>assert.fail(),onDone:()=>assert.fail(),onError:()=>assert.fail()});
 r.cancel();resolve({getTracks:()=>[{stop:()=>stopped++}]});await new Promise(r=>setImmediate(r));assert.equal(stopped,1);assert.equal(constructed,0);
});
test('stop waits for final data, releases tracks; cancellation never dispatches',async()=>{
 for(const cancel of [false,true]){
  let count=0,released=0;
  class Recorder{state='inactive';mimeType='audio/webm';start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable({data:new Blob(['final'])});this.onstop();});}}
  const r=capture({mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>released++}]})},Recorder,seconds:60,format:{mime:'audio/webm'},onState:()=>{},onDone:b=>{count++;assert.equal(b.size,5);},onError:e=>{throw e;}});
  await new Promise(r=>setImmediate(r));cancel?r.cancel():r.stop();await new Promise(r=>setImmediate(r));assert.equal(count,cancel?0:1);assert.ok(released>=1);
 }
});
