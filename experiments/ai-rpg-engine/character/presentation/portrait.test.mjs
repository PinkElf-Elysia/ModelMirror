import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {animatedFace} from './portrait.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');

function view(t){
  const dom=new JSDOM('<figure><span><img alt="静态肖像"></span><figcaption></figcaption></figure>');
  t.after(()=>dom.window.close());
  const win=dom.window,doc=win.document,face=doc.querySelector('span'),controls=doc.querySelector('figcaption');
  const state={reads:0,closed:0,frames:0,draws:0};
  win.fetch=async()=>{state.reads++;return {ok:true,arrayBuffer:async()=>new ArrayBuffer(1)};};
  win.HTMLCanvasElement.prototype.getContext=()=>({clearRect(){},drawImage(){state.draws++;}});
  function attach(){return animatedFace(face,controls,'/scope/animation/local',{frames:1,plays:1},()=>{});}
  return {win,state,face,controls,attach};
}
test('poster remains independently available with no decoder or playback request',t=>{
  const {face,controls,attach,state}=view(t),handle=attach();
  assert.equal(controls.querySelector('button').hidden,true);
  assert.match(controls.textContent,/静态肖像/);assert.ok(face.querySelector('img'));
  assert.equal(state.reads,0);handle.dispose();
});
test('a frame resolving after pagehide closes without repainting the detached scope',async t=>{
  const {win,state,face,controls,attach}=view(t);
  let decoded,started;
  const ready=new Promise(r=>{started=r;});
  win.ImageDecoder=class{
    tracks={ready:Promise.resolve(),selectedTrack:{frameCount:1}};
    decode(){started();return new Promise(r=>{decoded=r;});}
    close(){state.closed++;}
  };
  const handle=attach(),playing=controls.querySelector('button').onclick();
  await ready;assert.equal(state.reads,1);
  win.dispatchEvent(new win.Event('pagehide'));
  decoded({image:{close(){state.frames++;}}});await playing;
  assert.deepEqual(state,{reads:1,closed:1,frames:1,draws:0});
  assert.equal(face.querySelector('canvas'),null);assert.ok(face.querySelector('img'));
  handle.dispose();assert.equal(state.closed,1);
});
