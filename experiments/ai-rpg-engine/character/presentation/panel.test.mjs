import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createLookPanel} from './panel.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
const snapshot=(name,face=true)=>({people:[{id:name,name,portrait:null,
  expressions:face?[{key:name,name:'微笑'}]:[],backgrounds:[]}],expressions:{},background:null,revision:'first'});
test('rapid session navigation clears the prior scene before the new response, ignores stale loads and hides empty panels',async t=>{
  const dom=new JSDOM('<section></section>');t.after(()=>dom.window.close());
  const root=dom.window.document.querySelector('section');let finish;
  const panel=createLookPanel(root,async path=>path==='slow'?new Promise(r=>{finish=r;}):snapshot(path,path!=='empty'));
  await panel('old');assert.equal(root.querySelector('figcaption').textContent,'old');
  const late=panel('slow');assert.equal(root.hidden,true);assert.equal(root.querySelectorAll('figure').length,0);
  await panel('new');finish(snapshot('slow'));await late;assert.equal(root.querySelector('figcaption').textContent,'new');
  await panel('empty');assert.equal(root.hidden,true);
});
test('preview changes are transient; unreadable artwork falls back to the character initial without dropping the controls',async t=>{
  const dom=new JSDOM('<section></section>');t.after(()=>dom.window.close());
  const root=dom.window.document.querySelector('section');let reads=0;
  const panel=createLookPanel(root,async(path,body)=>{assert.equal(body,undefined);reads++;const value=snapshot('潮汐');value.people[0].portrait={key:'portrait',name:'默认'};return value;});
  await panel('/preview','',true);
  const expression=root.querySelectorAll('select')[1];expression.value='潮汐';await expression.onchange();
  const img=root.querySelector('.look-face img');assert.ok(img);img.dispatchEvent(new dom.window.Event('error'));
  const fallback=root.querySelector('.look-face img');assert.ok(fallback.src.endsWith('/image/portrait'));
  fallback.dispatchEvent(new dom.window.Event('error'));
  assert.equal(root.querySelector('.look-face').textContent,'潮');assert.equal(expression.disabled,false);
  await panel('');await panel('/preview','',true);assert.equal(expression.value,'');assert.equal(reads,2);
});
