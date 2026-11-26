import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createMediaPlayer} from './player.mjs';
import {createLookPanel} from './panel.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');

function fixture(t){
  const dom=new JSDOM('<section></section>',{url:'http://localhost/'});t.after(()=>dom.window.close());
  const {document,HTMLMediaElement}=dom.window,calls=[];
  for(const action of ['play','pause','load'])HTMLMediaElement.prototype[action]=function(){
    calls.push([action,this]);if(action==='play')return Promise.resolve();
  };
  const player=createMediaPlayer(document),root=document.querySelector('section');
  const people=[{id:'a',name:'潮',expressions:[],backgrounds:[],media:[
    {key:'tone',name:'钟',type:'audio/wav'},{key:'clip',name:'短片',type:'video/mp4'}]}];
  const show=createLookPanel(root,async()=>({people,expressions:{},background:null,revision:'r'}),player);
  const click=name=>Array.from(root.querySelectorAll('button')).find(b=>b.textContent===name).click();
  return {show,root,click,calls,player,window:dom.window};
}
test('a media-only version offers tracks without loading any; pause retains the source, stop releases it',async t=>{
  const {show,root,click,calls}=fixture(t);await show('/preview',0,true);
  assert.equal(root.hidden,false);assert.equal(root.querySelector('.look-controls').hidden,true);
  assert.equal(root.querySelector('audio,video'),null);assert.equal(calls.length,0);
  click('播放');const a=root.querySelector('audio');assert.ok(a.src.endsWith('/preview/media/tone'));
  click('暂停');assert.equal(calls.at(-1)[0],'pause');a.onpause();assert.equal(a.isConnected,true);
  click('继续播放');assert.equal(root.querySelector('audio'),a);
  click('停止');assert.equal(a.isConnected,false);assert.equal(a.hasAttribute('src'),false);
  assert.deepEqual(calls.slice(-2).map(([action])=>action),['pause','load']);assert.equal(a.onended,null);
});
test('track switches, decoding errors, natural end, pagehide and a different scope all release the native element',async t=>{
  const {show,root,click,window}=fixture(t);await show('/one');
  click('播放');const a=root.querySelector('audio'),select=root.querySelector('.look-player select');
  select.value='clip';select.onchange();assert.equal(a.hasAttribute('src'),false);
  for(const event of ['ended','error','pagehide']){
    click('播放');const v=root.querySelector('video');assert.ok(v);
    if(event==='pagehide')window.dispatchEvent(new window.Event(event));else v.dispatchEvent(new window.Event(event));
    assert.equal(v.isConnected,false);assert.equal(v.hasAttribute('src'),false);
  }
  click('播放');const previous=root.querySelector('video');
  const next=show('/two');assert.equal(previous.hasAttribute('src'),false);await next;
  assert.equal(root.querySelector('audio,video'),null);
});
