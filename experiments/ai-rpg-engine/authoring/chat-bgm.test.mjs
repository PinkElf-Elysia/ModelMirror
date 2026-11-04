import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chatBGMTrack,createChatBGMController} from './chat-bgm.mjs';
import {blankDocument,validateDocument,sha256} from './contracts.mjs';
import {AuthoringStore} from './store.mjs';
import {startAuthoringServer} from './server.mjs';

const doc=()=>{const d=blankDocument();d.metadata.name='音乐航站';d.metadata.audience='male';d.sources.system='冻结 system\n';return d;};
const bytes=Buffer.from('ID3\x04\x00\x00\x00\x00\x00\x00fixture audio header');
const track=(autoplay=false,key='a')=>({key,alias:key,name:'音乐.mp3',url:'/local/'+key,autoplay,error:null});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function player(){
  const audios=[];let before=0;
  const controller=createChatBGMController({beforePlay:()=>before++,createAudio:()=>{
    const a={paused:true,plays:0,pauses:0,loads:0,play(){this.plays++;if(this.next)return this.next();this.paused=false;this.onplaying?.();return Promise.resolve();},pause(){this.paused=true;this.pauses++;this.onpause?.();},removeAttribute(name){delete this[name];},load(){this.loads++;}};
    audios.push(a);return a;
  }});
  return {controller,audios,before:()=>before};
}

test('旧作品无自动播放字段原样往返；显式选项只接受布尔值',()=>{
  const original=doc();assert.deepEqual(validateDocument(original),original);assert.equal(Object.hasOwn(original.settings,'bgmAutoplay'),false);
  for(const value of [true,false]){const d=doc();d.settings.bgmAutoplay=value;assert.equal(validateDocument(d).settings.bgmAutoplay,value);}
  for(const value of ['true',1,null]){const d=doc();d.settings.bgmAutoplay=value;assert.throws(()=>validateDocument(d));}
  const d=doc();d.settings.unknownBGM='ignore';assert.throws(()=>validateDocument(d));
});
test('曲目绑定作品、别名、类型和字节 hash；无关 revision 不重播',()=>{
  const d=doc();d.settings.media.bgm='local-song';
  const work={id:'work-a',revision:1,assets:[{alias:'local-song',fileName:'曲目.mp3',mediaType:'audio/mpeg',sha256:sha256(bytes)}]};
  const first=chatBGMTrack(work,d);assert.equal(first.autoplay,false);assert.match(first.url,/^\/rpg-app\/api\/authoring\/works\/work-a\/assets\/local-song\?hash=[a-f0-9]{64}$/);
  assert.deepEqual(chatBGMTrack({...work,revision:9},d),first);
  const changed=structuredClone(work);changed.assets[0].sha256='f'.repeat(64);assert.notEqual(chatBGMTrack(changed,d).key,first.key);
  assert.equal(chatBGMTrack({...work,assets:[]},d).url,null);assert.match(chatBGMTrack({...work,assets:[]},d).error,/缺少/);
  changed.assets[0].mediaType='image/png';assert.equal(chatBGMTrack(changed,d).url,null);
  d.settings.media.bgm=null;assert.equal(chatBGMTrack(work,d),null);
});
test('未启用自动播放只有入口；打开保持暂停；点击播放循环且先暂停试听',async()=>{
  const {controller:c,audios,before}=player();c.update(track(),{active:true});assert.equal(c.snapshot().panel,false);assert.equal(audios.length,0);
  c.open();assert.equal(audios[0].paused,true);assert.equal(audios[0].loop,true);assert.equal(audios[0].plays,0);
  await c.play();assert.equal(c.snapshot().status,'playing');assert.equal(audios[0].paused,false);assert.equal(before(),1);
  c.pause();c.update(track(),{active:true});assert.equal(audios[0].plays,1);assert.equal(c.snapshot().status,'paused');
});
test('自动播放只在进入时触发一次，收起不停止，关闭与再次打开按偏好执行',async()=>{
  const {controller:c,audios}=player();const selected=track(true);c.update(selected,{active:true});await flush();
  assert.equal(audios.length,1);assert.equal(audios[0].plays,1);assert.equal(c.snapshot().status,'playing');
  c.collapse();assert.equal(c.snapshot().collapsed,true);assert.equal(audios[0].paused,false);
  c.update(selected,{active:true});assert.equal(audios[0].plays,1);
  c.pause();c.update(selected,{active:true});assert.equal(c.snapshot().status,'paused');assert.equal(audios[0].plays,1);
  c.close();assert.equal(audios[0].src,undefined);assert.equal(audios[0].loads,1);
  c.update(selected,{active:true});assert.equal(c.snapshot().panel,false);assert.equal(audios.length,1);
  c.open();await flush();assert.equal(audios.length,2);assert.equal(c.snapshot().status,'playing');
});
test('隐藏预览、切换来源与卸载释放声音，不泄露旧媒体事件',async()=>{
  const {controller:c,audios}=player();c.update(track(true),{active:true});await flush();const old=audios[0];
  c.update(track(true),{active:false});assert.equal(old.paused,true);assert.equal(old.onplaying,null);assert.equal(old.src,undefined);
  c.update(track(true),{active:true,enter:false});assert.equal(audios.length,1);
  c.update(track(true),{active:true});await flush();assert.equal(audios.length,2);
  c.update(track(false,'new'),{active:true});assert.equal(audios[1].paused,true);assert.equal(c.snapshot().panel,false);
  c.open();await c.play();c.destroy();assert.equal(audios[2].paused,true);assert.equal(c.snapshot().active,false);assert.equal(c.snapshot().track,null);
});
test('浏览器拒绝自动播放给出阻断状态，不隐式重试，允许手动重试',async()=>{
  const {controller:c,audios}=player();c.update(track(),{active:true});c.open();
  audios[0].next=()=>Promise.reject(Object.assign(new Error('gesture'),{name:'NotAllowedError'}));
  await c.play();assert.equal(c.snapshot().status,'blocked');c.update(track(),{active:true});assert.equal(audios[0].plays,1);
  delete audios[0].next;await c.play();assert.equal(c.snapshot().status,'playing');assert.equal(audios[0].plays,2);
});
test('媒体加载失败与缺失引用可诊断，关闭不会自动重试',async()=>{
  const {controller:c,audios}=player();c.update(track(),{active:true});c.open();await c.play();audios[0].onerror();
  assert.equal(c.snapshot().status,'failed');assert.equal(audios[0].paused,true);c.update(track(),{active:true});assert.equal(audios[0].plays,1);
  c.update({...track(false,'missing'),url:null,error:'缺失资源'},{active:true});c.open();assert.equal(c.snapshot().status,'missing');assert.equal(audios.length,1);
});
test('待完成的 play 在关闭或换曲后不能复活旧声音或覆盖新状态',async()=>{
  for(const action of ['close','change','pause']){
    const {controller:c,audios}=player();c.update(track(),{active:true});c.open();const old=audios[0];let resolve;
    old.next=()=>new Promise(r=>resolve=r);const pending=c.play();
    if(action==='change'){c.update(track(false,'next'),{active:true});c.open();await c.play();}
    else c[action]();
    old.paused=false;resolve();await pending;assert.equal(old.paused,true);
    assert.equal(c.snapshot().status,action==='change'?'playing':action==='pause'?'paused':'ready');
  }
});
test('过期 play 拒绝不覆盖后来成功的手动播放',async()=>{
  const {controller:c,audios}=player();c.update(track(),{active:true});c.open();let reject;
  audios[0].next=()=>new Promise((_,r)=>reject=r);const first=c.play();c.pause();delete audios[0].next;await c.play();
  reject(new Error('late failure'));await first;assert.equal(c.snapshot().status,'playing');assert.equal(audios[0].paused,false);
});

const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5e/tests/',import.meta.url));await mkdir(parent,{recursive:true});
test('音乐偏好、MP3 字节和不可变版本在保存重开及完整包导入后保留',async t=>{
  const directory=await mkdtemp(join(parent,'store-'));let store=await AuthoringStore.open(directory);t.after(()=>store.close());
  const made=await store.create({operationId:'create',expectedRevision:null,document:doc()});
  await store.putAsset(made.workId,{operationId:'audio',expectedRevision:0,alias:'song',fileName:'曲目.mp3',mediaType:'audio/mpeg',bytes});
  const d=doc();d.settings.media.bgm='song';d.settings.bgmAutoplay=true;
  await store.save(made.workId,{operationId:'save',expectedRevision:1,document:d});
  const sealed=await store.publish(made.workId,{operationId:'seal',expectedRevision:2});
  d.settings.bgmAutoplay=false;await store.save(made.workId,{operationId:'off',expectedRevision:3,document:d});
  assert.equal((await store.exportConfiguration(made.workId,sealed.result.versionId)).document.settings.bgmAutoplay,true);
  const pack=await store.exportPackage(made.workId);await store.close();store=await AuthoringStore.open(directory);
  assert.deepEqual((await store.read(made.workId)).draft,d);
  const other=await AuthoringStore.open(await mkdtemp(join(parent,'import-')));t.after(()=>other.close());
  const inspection=await other.inspectImport(pack);assert.equal(inspection.valid,true);
  const imported=await other.importPackage({operationId:'import',workId:null,expectedRevision:null,expectedImportHash:inspection.payloadHash,payload:pack});
  assert.deepEqual(await other.exportPackage(imported.workId),pack);assert.deepEqual((await other.getAsset(imported.workId,'song')).bytes,bytes);
});
test('播放器模块按同源服务；音频 hash 不匹配被拒绝，回复仍不执行模型音乐或脚本',async t=>{
  const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-'))});t.after(()=>host.close());const api='/rpg-app/api/authoring';
  const post=async(path,body)=>{const r=await fetch(host.origin+api+path,{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json();};
  for(const path of ['/chat-bgm.mjs','/chat-bgm.css'])assert.equal((await fetch(host.origin+path)).status,200);
  const made=await post('/works',{operationId:'create',expectedRevision:null,document:doc()});
  await post('/works/'+made.workId+'/assets',{operationId:'asset',expectedRevision:0,alias:'song',fileName:'test.mp3',mediaType:'audio/mpeg',base64:bytes.toString('base64')});
  const d=doc();d.settings.media.bgm='song';d.settings.bgmAutoplay=true;
  const work=await(await fetch(host.origin+api+'/works/'+made.workId)).json();const bound=chatBGMTrack(work,d);
  const r=await fetch(host.origin+bound.url);assert.equal(r.status,200);assert.match(r.headers.get('content-type'),/audio\/mpeg/);assert.deepEqual(Buffer.from(await r.arrayBuffer()),bytes);
  assert.equal((await fetch(host.origin+bound.url.replace(sha256(bytes),'0'.repeat(64)))).status,409);
  const rendered=await post('/presentations/prepare',{workId:made.workId,expectedRevision:1,document:d,mode:'reply',reply:'<p>正文</p><audio autoplay src="asset://song"></audio><script>play()</script>'});
  const frame=await fetch(host.origin+rendered.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}});assert.equal(frame.status,200);
  assert.match(frame.headers.get('content-security-policy'),/media-src 'none'/);assert.doesNotMatch(await frame.text(),/<audio[^>]*(?:\ssrc=|\sautoplay)|<script[^>]*>play\(\)/);
  assert.equal((await(await fetch(host.origin+api+'/works/'+made.workId)).json()).revision,1);
});


test('同别名替换字节会立即停止旧曲，普通 revision 更新不打断播放',async()=>{
  const {controller:c,audios}=player(),d=doc();d.settings.media.bgm='song';
  const work={id:'work-a',revision:1,assets:[{alias:'song',fileName:'song.mp3',mediaType:'audio/mpeg',sha256:sha256(bytes)}]};
  c.update(chatBGMTrack(work,d),{active:true});c.open();await c.play();
  work.revision++;c.update(chatBGMTrack(work,d),{active:true,enter:false});assert.equal(audios[0].paused,false);assert.equal(audios.length,1);
  work.assets[0].sha256=sha256(Buffer.from('replacement'));c.update(chatBGMTrack(work,d),{active:true,enter:false});
  assert.equal(audios[0].paused,true);assert.equal(audios[0].src,undefined);assert.equal(c.snapshot().panel,false);
  c.open();assert.match(audios[1].src,new RegExp(work.assets[0].sha256+'$'));assert.equal(audios[1].paused,true);
});
