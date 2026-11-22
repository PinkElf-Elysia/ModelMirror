import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {sha} from '../plugins/catalog.mjs';
import {start} from '../studio/server.mjs';
import {frozenCardAssembly} from '../studio/generation-cards.mjs';
import {entries, source, fixed, catalog, assembleWorldbook, versionHash} from './worldbook.mjs';

const [previousEngine, output] = process.argv.slice(2);
if (!previousEngine || !output) throw Error('Usage: node worldbook-acceptance.mjs <pre-split engine> <new export directory>');
const baseline = resolve(previousEngine);
const {start:previousStart} = await import(pathToFileURL(join(baseline,'studio/server.mjs')));
const {JSDOM} = createRequire(join(baseline,'../../client/package.json'))('jsdom');
const html = await readFile(new URL('./index.html', import.meta.url),'utf8');
function openingFor(name, period, person, place) {
  const dom = new JSDOM(html,{runScripts:'dangerously',url:'http://localhost/rpg-app/ancient/'});
  try {
    const {document:d} = dom.window;
    d.querySelector('[onclick*="pickRoute(\'common\')"]').click();
    const section = [...d.querySelectorAll('#dynList .acc-item')].find(el=>el.querySelector('h4').textContent===name);
    section.click();
    [...section.querySelectorAll('.era-opt')].find(el=>el.textContent.startsWith(period)).click();
    d.getElementById('cName').value=person;
    d.getElementById('openExtra').value=place;
    dom.window.genPreview();
    const opening=d.getElementById('pvBox').textContent;
    assert.ok(opening.includes('【朝代】'+name)&&opening.includes('【起始时期】'+period)&&opening.includes(place));
    return opening;
  } finally { dom.window.close(); }
}
const han=openingFor('西汉','开国初期','陈禾','我住在长安城外，家中以种粟为生。');
const song=openingFor('南宋','中期承平','沈宁','隆兴和议之后，我在临安一间茶坊做事。');
const market='我进长安集市，打听今年粟价，再问哪里收麻布。';
const inquiry='我仍在临安茶坊，向北来的客商打听金国的情形。';
const dir=await mkdtemp(join(tmpdir(),'ancient-books-'));
const wires=[];
async function round(run,input,prior,opening='') {
  const host=await run({host:'127.0.0.1',port:0,directory:dir,enabled:false});
  const origin='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body) {
    const response=await fetch(origin+'/rpg-app/ancient/'+path,body?{method:'POST',headers:{
      Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
    const data=await response.json();
    assert.ok(response.ok,JSON.stringify(data));return data;
  }
  try {
    const root='chat/api/sessions';
    const session=prior?await api(root+'/'+prior.id):await api(root,{opening});
    assert.equal(session.mode,'offline');assert.ok(session.runtime.compatible);
    if(prior) {assert.deepEqual(session.runtime,prior.runtime);assert.deepEqual(session.turns,prior.turns);}
    const path=root+'/'+session.id, memory=await api(path+'/memory-palace');
    const saved=await api(path+'/send',{operationId:randomUUID(),input,expectedSessionRevision:session.revision,
      expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
    assert.equal(saved.id,session.id);assert.deepEqual(saved.turns.slice(0,-1),session.turns);
    assert.equal(saved.turns.length,session.turns.length+1);
    const wire=JSON.parse(await readFile(join(dir,'archive-chat/ancient/offline/offline-plugin-dispatches/ancient','slot-'+(wires.length+1),'request.json'),'utf8'));
    wires.push(wire);
    const history=session.turns.flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
    assert.deepEqual(wire.messages.slice(1),[...history,{role:'user',content:input}]);
    if(!prior&&run===start){
      const receipt=await api('api/opening',{input});
      assert.equal(receipt.providerEnabled,false);
      assert.equal(receipt.requestHash,sha(JSON.stringify(wire.messages)));
    }
    return saved;
  } finally {await host.plugins?.close();await new Promise(resolve=>host.server.close(resolve));}
}
const checks=[];
function memberships(system) {return entries.filter(e=>system.includes(e.text)).map(e=>e.name);}
function check(system,wanted) {
  assert.deepEqual(memberships(system),wanted);
  const [before,after]=fixed.split('\n\n📖 朝代锚定系统\n');
  const expected=[before,catalog,...wanted.map(n=>entries.find(e=>e.name===n).text),'📖 朝代锚定系统\n'+after].join('\n\n');
  assert.equal(system,expected);
}
try {
  const old=await round(previousStart,han,null,han);
  const resumed=await round(start,market,old);
  assert.equal(wires[0].messages[0].content,source);assert.equal(wires[1].messages[0].content,source);
  assert.deepEqual(resumed.runtime,old.runtime);
  const first=await round(start,han,null,''); // The blank archive takes its opening from the first user turn.
  await round(start,market,first);
  const southern=await round(start,song,null,song);
  await round(start,inquiry,southern);
  const expected=[['西汉'],['西汉'],['南宋'],['南宋','金']];
  const typed=await frozenCardAssembly('ancient');
  const histories=[[],wires[3].messages.slice(1,-1),[],wires[5].messages.slice(1,-1)];
  for(const [i,wire] of wires.slice(2).entries()){
    const system=wire.messages[0].content;check(system,expected[i]);
    const session={characterText:i<2?han:song,history:histories[i],runtime:{descriptor:{hostArtifacts:{'ancient-card/worldbook.mjs':versionHash()}}}};
    const input=wire.messages.at(-1).content;
    assert.deepEqual((await typed(session,input)).messages,wire.messages);
    assert.equal((await typed({...session,runtime:{descriptor:{hostArtifacts:{}}}},input)).messages[0].content,source);
    checks.push({session:wire.sessionId,input,hits:expected[i],roles:wire.messages.map(m=>m.role),systemSha256:sha(system),messagesSha256:sha(JSON.stringify(wire.messages))});
  }
  const controls=[
    [han,inquiry,['西汉']],[song,'我去金陵买金饰，再问辽东来的茶商。',['南宋']],
    [song,'我想打听北宋。',['南宋']],[song,'在茶坊打听西夏。',['南宋','西夏']],
    ['【朝代】北宋','查阅辽国。',['北宋','辽']],['【帝王】汉武帝刘彻','出行。',['西汉']],
    ['【历史人物】辛弃疾','启程。',['南宋']],['【人生路线】自定义世界\n【世界背景】风俗近西汉','打听金国。',[]]
  ];
  for(const [opening,input,hits] of controls)check(assembleWorldbook({opening,input}).system,hits);
  for(const entry of entries.filter(e=>e.category==='朝代')){
    for(const name of [entry.name,...entry.aliases])check(assembleWorldbook({opening:'【朝代】'+name,input:'开局。'}).system,[entry.name]);
  }
  const report={providerCalls:0,turns:checks,controls:controls.map(([opening,input,hits])=>({opening:opening.split('\n').find(l=>l.includes('朝代'))||opening.split('\n')[0],input,hits})),
    oldSave:{session:old.id,turnsBefore:old.turns.length,turnsAfter:resumed.turns.length,preSplitSystemSha256:sha(wires[0].messages[0].content),resumedSystemSha256:sha(wires[1].messages[0].content),bindingUnchanged:true,previousTurnUnchanged:true},
    catalogEntries:entries.length,versionHash:versionHash()};
  await mkdir(output,{recursive:true});
  const put=(n,v)=>writeFile(join(output,n),v,{flag:'wx'});
  const json=v=>JSON.stringify(v,null,2)+'\n';
  await put('01-固定提示词.txt',fixed);
  await put('02-分类触发库.txt',catalog);
  await put('03-世界书.json',await readFile(new URL('./worldbook.json',import.meta.url)));
  for(const [i,n] of ['04-西汉开局','05-西汉续玩','06-南宋开局','07-南宋打听金国'].entries())await put(n+'.json',json(wires[i+2]));
  await put('08-离线验收.json',json(report));
  console.log(json({turns:checks.map(t=>t.hits),legacy:report.oldSave,controls:controls.length}));
} finally {await rm(dir,{recursive:true,force:true});}
