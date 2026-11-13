import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createPlaybackFrames,playbackSessionId,savedTurnMatches,playbackPollDelay,playbackSessionNotice} from './playback-ui.mjs';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {sha256} from './contracts.mjs';
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};};
function harness(){const calls=[],events=[];const api=(path,body)=>{const d=deferred();calls.push({path,body,...d});if(path.endsWith('/release'))d.resolve({released:true});return d.promise;};const manager=createPlaybackFrames({api,changed:e=>events.push(e)});return {calls,events,manager};}
const request={sessionId:'session-'+sha256('one'),turnNumber:1,device:'desktop'};
const frame=token=>({token,frameUrl:'/frame/'+token});

test('same-turn polling preserves frames instead of reloading details or animations',async()=>{
 const h=harness(),pending=h.manager.load('same',request),again=h.manager.load('same',request);
 assert.equal(h.calls.length,2);assert.deepEqual(h.calls.map(c=>c.body.role),['user','assistant']);
 h.calls[0].resolve(frame('user'));h.calls[1].resolve(frame('assistant'));await Promise.all([pending,again]);
 await h.manager.load('same',request);assert.equal(h.calls.length,2);assert.equal(h.events.length,2);assert.equal(h.manager.frames[1].token,'assistant');
 assert.ok(h.calls.every(c=>c.path==='/joint/playback/prepare'));
});

test('late old-session frames are released and never shown in the new session',async()=>{
 const h=harness(),old=h.manager.load('old',request),next=h.manager.load('new',{...request,sessionId:'session-'+sha256('two')});
 h.calls[2].resolve(frame('new-user'));h.calls[3].resolve(frame('new-ai'));await next;
 h.calls[0].resolve(frame('old-user'));h.calls[1].resolve(frame('old-ai'));await old;
 assert.deepEqual(h.manager.frames.map(f=>f.token),['new-user','new-ai']);
 assert.deepEqual(h.calls.filter(c=>c.path.endsWith('/release')).map(c=>c.body.token),['old-user','old-ai']);
 assert.deepEqual(h.events.filter(e=>e.frames.length).map(e=>e.frames[1].token),['new-ai']);
});

test('partial prepare failure releases the successful frame and does not retry on polling',async()=>{
 const h=harness(),p=h.manager.load('one',request);h.calls[0].resolve(frame('partial'));h.calls[1].reject({code:'AUTHORING_PRESENTATION_CAPACITY'});await p;
 assert.equal(h.events.at(-1).error.code,'AUTHORING_PRESENTATION_CAPACITY');assert.equal(h.manager.frames.length,0);
 const n=h.calls.length;await h.manager.load('one',request);assert.equal(h.calls.length,n);
 assert.equal(h.calls.at(-1).body.token,'partial');h.manager.clear();
 const retry=h.manager.load('one',request);h.calls[n].resolve(frame('fixed-user'));h.calls[n+1].resolve(frame('fixed-ai'));await retry;
 assert.equal(h.manager.frames[1].token,'fixed-ai');assert.ok(h.calls.every(c=>!c.path.endsWith('/send')&&!c.path.endsWith('/recover')));
});

test('closing while prepare is in flight releases results without a late UI update',async()=>{
 const h=harness(),p=h.manager.load('one',request);h.manager.clear();h.calls[0].resolve(frame('u'));h.calls[1].resolve(frame('a'));await p;
 assert.equal(h.manager.frames.length,0);assert.equal(h.events.length,1);assert.equal(h.calls.filter(c=>c.path.endsWith('/release')).length,2);
});

test('switching device releases live frames before preparing the device-specific view',async()=>{
 const h=harness(),p=h.manager.load('desktop',request);h.calls[0].resolve(frame('du'));h.calls[1].resolve(frame('da'));await p;
 const phone=h.manager.load('mobile',{...request,device:'mobile'});assert.deepEqual(h.calls.slice(2,4).map(c=>c.body.token),['du','da']);assert.equal(h.calls[4].body.device,'mobile');
 h.calls[4].resolve(frame('mu'));h.calls[5].resolve(frame('ma'));await phone;
 assert.deepEqual(h.manager.frames.map(f=>f.token),['mu','ma']);
});

test('invalid or unavailable local references fail closed without inventing a session',()=>{
 const storage=v=>({getItem:()=>v});assert.equal(playbackSessionId(storage(null),'work'),null);assert.equal(playbackSessionId(storage(JSON.stringify(request.sessionId)),'work'),request.sessionId);
 for(const v of ['{bad','false','{}','"other"','"session-'+sha256('x')+'/../y"'])assert.throws(()=>playbackSessionId(storage(v),'work'),e=>e.code==='PLAYBACK_SESSION_REFERENCE_INVALID');
 assert.throws(()=>playbackSessionId({getItem(){throw Error('denied');}},'work'),e=>e.code==='PLAYBACK_SESSION_REFERENCE_INVALID');
});

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6w-tests/',import.meta.url));await mkdir(root,{recursive:true});
for(const enabled of [false,true])test('playback host flag '+enabled+' controls entry and static module, without calls',async t=>{
 const dir=await mkdtemp(join(root,'case-')),provider=await OfflineFixtureProvider.open(join(dir,'provider'),{replies:{}});
 const control=new OfflineModelControl({catalogId:'ui-fixture',models:[{selectionId:'story',selectionRevision:sha256('story'),modelId:'offline/story',name:'离线剧情',available:true,contextLength:128000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]});
 const host=await startAuthoringServer({directory:join(dir,'works'),creatorExperience:true,modelSettings:{directory:join(dir,'sessions'),control,provider},historySettings:{directory:join(dir,'history')},jointSettings:{directory:join(dir,'joint'),allowance:{id:'ui-budget',limit:10}},...(enabled?{playbackSettings:{directory:join(dir,'display')}}:{})});
 t.after(async()=>{await host.close();await provider.close();});
 const html=await(await fetch(host.origin)).text();assert.equal(html.includes('data-turn-playback="true"'),enabled);assert.equal((await fetch(host.origin+'/playback-ui.mjs')).status,enabled?200:404);assert.equal(provider.dispatchCount,0);
 const state=await(await fetch(host.origin+'/rpg-app/api/authoring/joint')).json();assert.equal(Object.hasOwn(state,'turnPlayback'),enabled);
 const css=await fetch(host.origin+'/playback-ui.css');assert.equal(css.status,enabled?200:404);
 if(enabled){assert.match(css.headers.get('content-type'),/text\/css/);assert.match(await css.text(),/\.playback-view/);assert.equal((await fetch(host.origin+'/playback-ui.mjs',{headers:{Origin:'https://outside.invalid'}})).status,403);const page=await fetch(host.origin);assert.match(page.headers.get('content-security-policy'),/style-src 'self';/);}
});


test('saved-turn view follows only the active work and original selected session',()=>{
 const id='session-'+sha256('selected'),storage={getItem:()=>JSON.stringify(id)};
 assert.equal(savedTurnMatches({workId:'work',sessionId:id},'work',storage),true);
 assert.equal(savedTurnMatches({workId:'old-work',sessionId:id},'work',storage),false);
 assert.equal(savedTurnMatches({workId:'work',sessionId:'session-'+sha256('old')},'work',storage),false);
 assert.equal(savedTurnMatches({workId:'work',sessionId:id},'work',{getItem:()=>'{broken'}),false);
 assert.equal(savedTurnMatches(null,'work',storage),false);
});


test('仅进行中的后台工作轮询；闲置与未知结果等待显式操作',()=>{
 for(const v of [null,{busy:false},{busy:false,pending:true,status:'unknown'},{busy:false,pending:false,status:'completed'}])assert.equal(playbackPollDelay(v),null);
 assert.equal(playbackPollDelay({busy:true,pending:true}),2500);
});


test('comparison settlement notice does not masquerade as an unknown ordinary send',()=>{
 const run={state:'running',comparison:{plan:{id:'comparison'}}};
 assert.match(playbackSessionNotice({pending:true,session:{runs:[run]}}),/对比仍在收束/);
 assert.equal(playbackSessionNotice({pending:false,session:{runs:[{...run,state:'completed'}]}}),'');
 assert.match(playbackSessionNotice({pending:true,session:{runs:[{state:'unknown'}]}}),/恢复原操作/);
 assert.match(playbackSessionNotice({pending:true}),/恢复原操作/);
 assert.match(playbackSessionNotice({pending:true,session:{runs:[run,{state:'unknown'}]}}),/恢复原操作/);
});
