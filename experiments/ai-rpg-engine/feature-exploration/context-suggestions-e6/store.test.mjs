import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {openSource} from './source.mjs';import {Choices,giftText} from './store.mjs';import {start} from './server.mjs';
test('choose, edit, ignore and restore; no world changes or model tasks',async t=>{
 const dir=await mkdtemp(resolve('.rpg03-work/e6-choice-')),host=await openSource(join(dir,'r8'));let s=await Choices.open(join(dir,'choices'),host);t.after(async()=>{await s.close();await host.close();});
 const before=await host.audit();let v=await s.command({kind:'suggest',revision:0,goal:'我想感谢他'});
 assert.ok(v.result.question);v=await s.command({kind:'suggest',revision:v.revision,goal:'我想感谢他',answer:'周禾'});
 v=await s.command({kind:'save',revision:v.revision,optionId:'gift',quantity:2,text:giftText(2)});
 assert.equal(v.saved[0].plan.plan.steps.length,1);assert.equal(v.saved[0].modelSuggestion.mode,'offline-fixture');
 v=await s.command({kind:'save',revision:v.revision,optionId:'ask',text:'我想等下次遇到他时再询问。'});
 assert.equal(v.saved[1].plan,null);assert.equal(v.saved[1].text,'我想等下次遇到他时再询问。');
 v=await s.command({kind:'dismiss',revision:v.revision});assert.equal(v.result,null);
 v=await s.command({kind:'save',revision:v.revision,text:'他已经答应了。'});assert.equal(v.saved[2].plan,null);
 await s.close();s=await Choices.open(join(dir,'choices'),host);assert.equal((await s.view()).saved.length,3);
 assert.deepEqual(await host.audit(),before);
});
test('stale source, duplicate clicks, invalid amount, edited text do not silently convert',async t=>{
 const dir=await mkdtemp(resolve('.rpg03-work/e6-guard-')),host=await openSource(join(dir,'r8'));
 const realRead=host.reader.read;let drift=false;
 const wrapped={...host,reader:{read:async()=>{const f=await realRead();if(drift)f.source.revision++;return f;}}};
 const s=await Choices.open(join(dir,'choices'),wrapped);t.after(async()=>{await s.close();await host.close();});
 let v=await s.command({kind:'suggest',revision:0,goal:'感谢周禾'});
 for(const quantity of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1])await assert.rejects(s.command({kind:'save',revision:v.revision,optionId:'gift',quantity,text:giftText(quantity)}),/E6_QUANTITY/);
 await assert.rejects(s.command({kind:'save',revision:v.revision,optionId:'gift',quantity:2,text:'改成服药'}),/E6_EDITED_TEXT/);
 drift=true;assert.equal((await s.view()).stale,true);
 await assert.rejects(s.command({kind:'save',revision:v.revision,optionId:'ask',text:'询问'}),/E6_STALE/);
 drift=false;v=await s.command({kind:'save',revision:v.revision,optionId:'ask',text:'询问'});
 await assert.rejects(s.command({kind:'save',revision:v.revision-1,text:'重复'}),/E6_CONFLICT/);
});
test('HTTP loopback origin boundary; execution endpoints absent',async t=>{
 const directory=await mkdtemp(resolve('.rpg03-work/e6-http-'));
 await assert.rejects(start({directory}),/E6_OFFLINE_ONLY/);
 const app=await start({directory,port:0,offlinePreview:true});t.after(()=>app.close());
 const v=await (await fetch(app.url+'/api/view')).json();assert.equal(v.realCalls,0);
 assert.equal((await fetch(app.url+'/api/execute',{method:'POST'})).status,404);
 assert.equal((await fetch(app.url+'/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,403);
 const response=await fetch(app.url+'/api/command',{method:'POST',headers:{Origin:app.url,'Content-Type':'application/json'},body:JSON.stringify({kind:'suggest',revision:0,goal:'感谢周禾'})});
 assert.equal(response.status,200);assert.equal((await response.json()).result.options.length,3);
});
