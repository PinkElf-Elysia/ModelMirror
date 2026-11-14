export const SETTINGS_KEY = 'rpg-voice-input:v1';
export const MAX_BYTES = 25 * 1024 * 1024;
export const defaults = {enabled:true,model:'',language:'auto',seconds:60};
export const languages = [['auto','自动识别'],['zh','中文'],['en','英语'],['ja','日语'],['ko','韩语'],['fr','法语'],['de','德语'],['es','西班牙语']];
const formats = [{mime:'audio/webm;codecs=opus',extension:'webm'},{mime:'audio/ogg;codecs=opus',extension:'ogg'},{mime:'audio/mp4',extension:'m4a'}];
export function readSettings(storage) {
 const value=JSON.parse(storage.getItem(SETTINGS_KEY)||'null');
 if(value===null)return {...defaults};
 if(typeof value.enabled!=='boolean'||typeof value.model!=='string'||value.model.length>256||!languages.some(([id])=>id===value.language)||![30,60,120].includes(value.seconds))throw Error('语音设置无法读取，请在面板重置设置。');
 return {enabled:value.enabled,model:value.model,language:value.language,seconds:value.seconds};
}
export function appendTranscript(draft,text) {
 if(typeof text!=='string'||!text.trim())throw Error('没有识别到文字，请检查麦克风后重新录音。');
 const next=draft+(draft&&!draft.endsWith('\n')?'\n':'')+text.trim();
 if(next.length>20000)throw Error('合并后超过输入上限，识别结果已保留，请先整理草稿。');
 return next;
}
export function recordingFormat(accepted,supported) {
 const direct=formats.find(f=>accepted.includes(f.extension)&&supported(f.mime));
 if(direct)return {...direct,convert:false};
 const source=formats.find(f=>supported(f.mime));
 if(source&&accepted.includes('wav'))return {...source,convert:true};
 throw Error('浏览器录音格式与所选模型不兼容，请在语音输入设置中更换模型或浏览器。');
}
export async function readJson(url,fetcher=fetch,signal,timeout=20000,init={}) {
 const controller=new AbortController(),abort=()=>controller.abort();
 const timer=setTimeout(abort,timeout);signal?.addEventListener('abort',abort,{once:true});
 if(signal?.aborted)abort();
 try {const r=await fetcher(url,{...init,signal:controller.signal,redirect:'error'});if(!r.ok)throw Error('语音服务暂不可用，请检查模型设置与额度。');return await r.json();}
 finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
export async function catalog(fetcher=fetch,signal) {
 const v=await readJson('/api/multimodal/audio/models',fetcher,signal);
 if(!Array.isArray(v.profiles))throw Error('语音模型列表无法读取。');
 return {...v,profiles:v.profiles.filter(p=>p.chat_modes?.includes('transcribe')&&typeof p.model_id==='string')};
}
export async function profile(model,fetcher=fetch,signal) {
 const c=await catalog(fetcher,signal);
 if(c.microphone_enabled!==true)throw Error('服务尚未开放麦克风输入，请联系维护者。');
 const p=model?c.profiles.find(p=>p.model_id===model):c.profiles.find(p=>p.invocable&&p.interaction_status==='ready');
 if(!p)throw Error(model?'保存的语音模型已不在目录中，请重新选择。':'暂无可用转写模型，请先在模型设置中配置。');
 const q=new URLSearchParams({entry_id:'multimodal_transcription',model_id:p.model_id,execution_shape:'audio_transcription'});
 const control=await readJson('/api/models/provider-workload-control?'+q,fetcher,signal);
 const legacy=control.feature_enabled===false||control.status==='legacy';
 if(legacy?!(p.invocable&&p.interaction_status==='ready'):!(control.available===true&&control.status==='managed_required'))throw Error('所选模型的语音路由尚不可用，请在模型设置中核对认证与额度。');
 const accepted=(legacy?p.input_formats:control.certified_input_formats)||[];
 if(!accepted.length)throw Error('所选模型未提供可用录音格式。');
 return {model:p.model_id,name:p.display_name,formats:accepted,managed:!legacy};
}
export function encodeWav(channels,sampleRate) {
 const length=channels[0]?.length||0;
 if(!length||length/sampleRate>121)throw Error('录音为空或超过时长限制。');
 const bytes=new ArrayBuffer(44+length*2),v=new DataView(bytes);
 const str=(at,s)=>{for(let i=0;i<s.length;i++)v.setUint8(at+i,s.charCodeAt(i));};
 str(0,'RIFF');v.setUint32(4,36+length*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,sampleRate,true);v.setUint32(28,sampleRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,length*2,true);
 for(let i=0;i<length;i++){let n=0;for(const c of channels)n+=c[i]/channels.length;n=Math.max(-1,Math.min(1,n));v.setInt16(44+i*2,Math.round(n*(n<0?32768:32767)),true);}
 return new Blob([bytes],{type:'audio/wav'});
}
export async function toWav(blob) {
 const context=new OfflineAudioContext(1,1,16000);
 const decoded=await context.decodeAudioData(await blob.arrayBuffer());
 return encodeWav(Array.from({length:decoded.numberOfChannels},(_,i)=>decoded.getChannelData(i)),decoded.sampleRate);
}
// Recording has no network access. Permission arriving after cancellation releases tracks immediately.
export function capture({mediaDevices,Recorder,onState,onDone,onError,seconds,format}) {
 let cancelled=false,stream,recorder,timer,size=0,chunks=[];
 const release=()=>{clearTimeout(timer);stream?.getTracks().forEach(t=>t.stop());};
 const cancel=()=>{cancelled=true;if(recorder?.state==='recording')recorder.stop();release();chunks=[];};
 const stop=()=>{if(recorder?.state==='recording')recorder.stop();release();};
 void (async()=>{try {
  stream=await mediaDevices.getUserMedia({audio:true});if(cancelled){release();return;}
  recorder=new Recorder(stream,{mimeType:format.mime});
  recorder.ondataavailable=e=>{if(cancelled||!e.data.size)return;size+=e.data.size;if(size>MAX_BYTES){cancel();onError(Error('录音超过25 MiB，已取消，请缩短后重新录音。'));return;}chunks.push(e.data);};
  recorder.onerror=()=>{cancel();onError(Error('录音中断，请检查设备后重新录音。'));};
  recorder.onstop=()=>{release();if(cancelled)return;cancelled=true;const blob=new Blob(chunks,{type:recorder.mimeType});chunks=[];if(!blob.size){onError(Error('没有录到声音，请重试。'));return;}onDone(blob);};
  recorder.start(250);onState();timer=setTimeout(stop,seconds*1000);
 }catch(e){release();if(!cancelled)onError(e);}})();
 return {cancel,stop};
}
export async function transcribe({blob,extension,model,language,id},fetcher=fetch,signal) {
 if(!blob.size||blob.size>MAX_BYTES)throw Error('录音大小不符合要求。');
 const body=new FormData();body.set('model_id',model);body.set('language',language);body.set('file',blob,'recording.'+extension);
 // The existing unified endpoint validates the selected route and reserves/settles its quota.
 return readJson('/api/multimodal/transcriptions',fetcher,signal,240000,{method:'POST',headers:{'Idempotency-Key':id},body});
}
export function recorderError(e) {
 return ({NotAllowedError:'未获得麦克风权限，请在浏览器权限设置中允许录音。',NotFoundError:'没有检测到麦克风，请连接设备后重试。',NotReadableError:'麦克风被占用或无法读取，请检查设备。'})[e?.name]||e?.message||'语音输入未完成，请重试。';
}
