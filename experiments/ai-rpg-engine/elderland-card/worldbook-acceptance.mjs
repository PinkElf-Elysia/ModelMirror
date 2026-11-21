import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {render} from './build.mjs';
import {assembleWorldbook as assemble,catalogOf,worldbook,regions,source,sourceHash,fixed,omitted} from './worldbook.mjs';
import {positions,lines,locations,sha} from './worldbook-sources.mjs';
import {start} from '../studio/server.mjs';
const [baseline,output]=process.argv.slice(2);
if(!baseline)throw Error('Provide a trusted pre-split engine checkout.');
const {start:startLegacy}=await import(pathToFileURL(resolve(baseline,'studio/server.mjs')));
const {JSDOM}=createRequire(new URL('../../../client/package.json',import.meta.url))('jsdom');
const directory=await mkdtemp(join(tmpdir(),'elderland-review-'));
const marker=lines[1126]; // first substantive line of western overview
assert.ok(marker.includes('核心概念'));
const stormLandmark=lines[1184],stormBoard=lines[1216];
async function nativeOpening(){
  const html=await render();
  assert.equal(html,await readFile(new URL('./public/index.html',import.meta.url),'utf8'));
  const observers=[];
  const dom=new JSDOM(html,{url:'http://localhost/rpg-app/elderland/?chat=1',runScripts:'dangerously',beforeParse(w){
    const Observer=w.MutationObserver;
    w.MutationObserver=class extends Observer{constructor(callback){super(callback);observers.push(this);}};
    w.matchMedia=()=>({matches:true});w.HTMLElement.prototype.scrollIntoView=()=>{};w.alert=()=>{};
  }});
  try{
    await new Promise(done=>dom.window.addEventListener('load',done,{once:true}));
    const d=dom.window.document;
    d.getElementById('opening').click();
    d.querySelector('[data-tab="north"]').click();
    d.querySelector('#north-countries .country-card').click();
    d.querySelector('#north-loc-list .location-item').click();
    d.getElementById('char-name').value='林澈';d.getElementById('char-age').value='26';
    d.getElementById('background').value='清晨抵达冬堡城，先在城门登记，找一间住处。';
    d.querySelector('[data-tab="preview"]').click();
    const opening=d.getElementById('preview-box').textContent;
    assert.ok(opening.includes('北境·永冬荒原 > 南部文明区 > 冬堡城'));
    return opening;
  }finally{for(const observer of observers)observer.disconnect();dom.window.close();}
}
async function round(open,input,previous){
  const host=await open({host:'127.0.0.1',port:0,directory,enabled:false});
  const origin='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body){
    const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
    const res=await fetch(origin+'/rpg-app/elderland/chat/api/sessions'+path,options);
    assert.ok(res.ok,'archive HTTP '+res.status);return res.json();
  }
  try{
    const session=previous?await api('/'+previous.id):await api('',{opening:''});
    assert.equal(session.mode,'offline');assert.ok(session.runtime.compatible);
    if(previous){assert.deepEqual(session.turns,previous.turns);assert.deepEqual(session.runtime,previous.runtime);}
    const path='/'+session.id,memory=await api(path+'/memory-palace');
    const saved=await api(path+'/send',{operationId:randomUUID(),input,expectedSessionRevision:session.revision,
      expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
    assert.equal(saved.turns.length,session.turns.length+1);
    assert.deepEqual(saved.turns.slice(0,-1),session.turns);
    return saved;
  }finally{await host.plugins?.close();await new Promise(done=>host.server.close(done));}
}

const lookup=new Map(worldbook.map(e=>[e.id,e]));
const membership=a=>a.hits.map(id=>lookup.get(id));
const summary=a=>({location:a.location,consulted:a.consulted,hits:membership(a).map(e=>({id:e.id,region:e.region,name:e.name,category:e.category,positions:e.spans,origin:e.origin})),
  catalogSha256:sha(a.catalog),systemSha256:sha(a.system),systemCharacters:[...a.system].length});
function checkSources(){
  const required=[[760,764],[812,819],[829,954],[1014,2843]];
  for(const [a,b] of required)for(let n=a;n<=b;n++)assert.ok(positions.has(n),'unmapped source line '+n);
  const claimed=new Set();
  for(const e of worldbook){
    assert.ok(/^[\p{Script=Han}·]+$/u.test(e.name),e.id+' '+e.name);
    assert.ok(e.keys.includes(e.name));assert.ok(e.body||e.spans.length);
    for(const s of e.spans)for(let n=s.start;n<=s.end;n++){
      assert.equal(positions.get(n),e);assert.ok(!claimed.has(n));claimed.add(n);
    }
  }
  for(const loc of locations){
    const t=assemble({opening:'【出生地】\n'+regions[loc.region][0]+' > '+loc.country+' > '+loc.name,input:'登记。'});
    assert.deepEqual(t.location,{city:loc.name,region:loc.region},loc.name);assert.deepEqual(t.consulted,[]);
    assert.ok(membership(t).every(e=>['common',loc.region].includes(e.region)));
  }
  let triggerChecks=0;
  for(const e of worldbook){
    const r=e.region==='common'?'north':e.region;
    const c=locations.find(loc=>loc.region===r&&loc.name===e.parent)||locations.find(loc=>loc.region===r);
    const opening='【出生地】\n'+regions[r][0]+' > '+c.country+' > '+c.name;
    for(const key of e.keys){
      const input='查阅'+(e.parent||'')+'的'+key+(e.mode==='secret'?'秘闻':'')+'。';
      assert.ok(assemble({opening,input}).hits.includes(e.id),e.id+' '+key);triggerChecks++;
    }
  }
  return {entries:worldbook.length,coveredSourceLines:claimed.size,formLocations:locations.length,regions:Object.keys(regions).length,triggerChecks};
}
const request=n=>readFile(join(directory,'archive-chat/elderland/offline/offline-plugin-dispatches/elderland','slot-'+n,'request.json'),'utf8');
try{
  const mapping=checkSources();
  const opening=await nativeOpening();
  const board='我去冒险者公会问任务，看看冬堡分部今天贴出了哪些委托。';
  const inquiry='我仍在冬堡城，向公会会长打听风暴群岛的概况。';
  const inputs=[opening,board,inquiry];
  const north={region:'north',city:'冬堡城'};
  const controls=[];
  function verify(input,location,consulted,history=[]){
    const a=assemble({opening,history,input});
    assert.deepEqual(a.location,location);assert.deepEqual(a.consulted,consulted);
    assert.equal(a.system.includes(marker),location.region==='west'||consulted.includes('west'));
    controls.push({input,...summary(a)});
  }
  verify('听人提起风暴群岛的名字，我继续在这里休息。',north,[]);
  verify('我没有打听西境，只是去公会。',north,[]);
  verify('我计划前往风暴群岛。',north,[]);
  verify('如何抵达风暴群岛？',north,['west']);
  verify('查阅 Stormreach Isles。',north,['west']);
  verify('那里有哪些委托？',north,['west'],[{role:'user',content:inquiry},{role:'assistant',content:'本地离线回执'}]);
  verify('回旅店休息。',north,[],[{role:'user',content:inquiry},{role:'assistant',content:'本地离线回执'}]);
  verify('我已抵达风暴群岛。',{region:'west',city:''},[]);
  const old=await round(startLegacy,opening),oldAfter=await round(start,board,old);
  const oldWire=await Promise.all([1,2].map(async slot=>JSON.parse(await request(slot)).messages));
  for(const ms of oldWire)assert.equal(ms[0].content,source);
  assert.deepEqual(oldAfter.runtime,old.runtime);assert.deepEqual(oldAfter.turns[0],old.turns[0]);
  assert.deepEqual(oldWire[1].slice(1),[...oldWire[0].slice(1),{role:'assistant',content:old.turns[0].raw},{role:'user',content:board}]);
  let saved;const turns=[],wire=[],catalogs=[];
  for(const [i,input] of inputs.entries()){
    const before=saved;
    saved=await round(start,input,before);
    if(i)assert.equal(saved.id,before.id);
    const raw=await request(i+3),ms=JSON.parse(raw).messages;
    const history=(before?.turns||[]).flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
    const a=assemble({opening,history,input});
    assert.deepEqual(ms,[{role:'system',content:a.system},...history,{role:'user',content:input}]);
    assert.deepEqual(a.location,north);assert.deepEqual(a.consulted,i===2?['west']:[]);
    assert.ok(a.system.includes('【本轮所在地】霜寒领 ＞ 冬堡城'));
    assert.ok(a.system.includes(lines[1072]));
    assert.ok(ms.every(m=>!m.content.includes(stormLandmark)&&!m.content.includes(stormBoard)));
    if(i<2){
      assert.ok(ms.every(m=>!m.content.includes(marker)));
      assert.ok(membership(a).every(e=>['north','common'].includes(e.region)));
      assert.ok(!a.catalog.includes('七海盗王同盟'));
    }else{
      assert.ok(a.system.includes(marker));assert.ok(a.system.includes(lines[1124]));
      assert.ok(membership(a).filter(e=>e.region==='west').every(e=>e.mode==='region'));
      assert.ok(a.system.indexOf(lines[1072])<a.system.indexOf(marker));
    }
    assert.equal(a.system.includes(lines[1104]),i===1);
    if(i===1){
      assert.ok(a.system.includes(lines[1084]));
      assert.ok(a.system.includes('冒险者公会 — 会长弗蕾雅·银弓'));
    }
    wire.push(raw);catalogs.push(a.catalog);
    turns.push({turn:i+1,input,roles:ms.map(m=>m.role),messagesSha256:sha(JSON.stringify(ms)),requestSha256:sha(raw),...summary(a)});
  }
  const legacy={preSplitFirstSystem:sha(oldWire[0][0].content),resumedSystem:sha(oldWire[1][0].content),messageHashes:oldWire.map(ms=>sha(JSON.stringify(ms))),turns:oldAfter.turns.length,bindingUnchanged:true,originalTurnUnchanged:true};
  const proof={realModelCalls:0,sourceSha256:sourceHash,fixedCharacters:[...fixed].length,mapping,sameSessionForThreeTurns:true,turns,controls,legacy};
  if(output){
    await mkdir(output);
    const put=(file,data)=>writeFile(join(output,file),data,{flag:'wx'});
    const json=data=>JSON.stringify(data,null,2)+'\n';
    await put('01-固定提示词.txt',fixed);
    await put('02-冬堡城分类触发库.txt',catalogs[0]);await put('02b-打听西境时的分类触发库.txt',catalogs[2]);
    await put('02c-分区域分类触发库.txt',Object.keys(regions).map(r=>'【'+regions[r][0]+'】\n'+catalogOf([r])).join('\n\n'));
    await put('03-世界书.json',json({sourceSha256:sourceHash,regions,omittedFixedLines:omitted,entries:worldbook.map(e=>({...e,spans:e.spans.map(s=>({...s,text:lines.slice(s.start-1,s.end).join('\n')}))}))}));
    for(const [i,name] of ['04-冬堡城开局请求.json','05-公会问任务请求.json','06-打听风暴群岛请求.json'].entries())await put(name,wire[i]);
    await put('07-离线验收.json',json(proof));
  }
  console.log(JSON.stringify({mapping,fixed:proof.fixedCharacters,turns:turns.map(t=>[t.hits.length,t.systemCharacters,t.location,t.consulted]),controls:controls.length,legacy}));
}finally{await rm(directory,{recursive:true,force:true});}
