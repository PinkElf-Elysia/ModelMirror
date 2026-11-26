import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {mountRoundPanel} from './round-panel.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
const html=await readFile(new URL('./page.html',import.meta.url),'utf8');

test('tab refresh drops advance permission even when the first receipt arrives late',async t=>{
  const dom=new JSDOM(html), {window}=dom, doc=window.document, $=id=>doc.getElementById(id);
  t.after(()=>window.close());
  const entered=Promise.withResolvers(),reply=Promise.withResolvers(),posts=[];
  let group={id:'g',members:[{id:'a',kind:'character',name:'甲'},{id:'b',kind:'character',name:'乙'}],requests:[],revision:0};
  const steps=group.members.map(m=>({memberId:m.id,status:'waiting',requestId:null}));
  const round={id:'r',status:'ready',steps,next:0,revision:0};
  const next={...round,next:1,revision:3,steps:[{...steps[0],status:'completed'},steps[1]]};
  const panel=mountRoundPanel({document:doc,window,crypto:window.crypto,rules:()=>({mode:'fixed'}),
    refresh:async()=>panel.update(group,true),api:async(path)=>{
      posts.push(path);
      if(path.endsWith('round-start')){group={...group,round};return {round,duplicate:false};}
      if(path.endsWith('round-step')){entered.resolve();await reply.promise;group={...group,revision:3,round:next};return next;}
      throw Error('unexpected action');
    }});
  panel.update(group,true);$('round-message').value='玩家只说一次';$('round-message').oninput();
  const running=$('round-start').onclick();await entered.promise;
  $('refresh').click();reply.resolve();await running;
  assert.equal(posts.length,2);assert.equal($('round-resume').hidden,false);
  const reloaded=mountRoundPanel({document:doc,window,crypto:window.crypto,api:()=>assert.fail('unexpected dispatch'),
    refresh:()=>assert.fail('unexpected refresh'),rules:()=>null});
  reloaded.update(group,true);assert.equal($('round-resume').hidden,false);assert.equal(posts.length,2);
});
