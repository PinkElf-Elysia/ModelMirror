// Run with the pre-split engine checkout and an optional empty review export directory.
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdtemp,readFile,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {runInNewContext} from 'node:vm';
import {start} from '../studio/server.mjs';
import {assembleWorldbook,fixed,source,worldbook} from './worldbook.mjs';
import {omittedRanges} from './worldbook-index.mjs';

const [legacyPath,output]=process.argv.slice(2);
if(!legacyPath)throw Error('Provide a pre-split engine checkout.');
const {start:startLegacy}=await import(pathToFileURL(resolve(legacyPath,'studio/server.mjs')));
const directory=await mkdtemp(join(tmpdir(),'douluo-archive-review-'));
const sha=text=>createHash('sha256').update(text).digest('hex');
const pretty=value=>JSON.stringify(value,null,2)+'\n';
const sourceLines=source.split('\n');
const html=await readFile(new URL('./resources/original.html',import.meta.url),'utf8');
const fields={cName:'林川',cAge:'22',cSpirit:'蓝银草',cPow:'三级',lvRange:'12',tlRange:'2637',cId:'诺丁城的见习助教',cOpen:'清晨到诺丁学院报到。'};
const nativeBuilder=html.slice(html.indexOf('function buildText(){'),html.indexOf('function countFill(){'));
const opening=runInNewContext(nativeBuilder+'\nbuildText()',{
  genderVal:()=>'男',gv:key=>fields[key]||'',$:key=>({value:fields[key]}),$$:()=>[],skills:[],BONES:[],TLE:{},
  titleOf:()=>'魂师',ringListText:()=>['白环（十年）'],featOn:key=>({person:'第二人称',length:'中等'}[key]||false)
},{timeout:1000});
const follow='在诺丁学院向王圣请教战虎的魂技，问他何时能演示虎扑。';
const sequelOpening='作品：《绝世唐门》';
const markers=worldbook.filter(r=>r.work==='绝世唐门').map(r=>r.spans[0].text);
function inspectMetadata(){
  const ids=new Set(),positions=new Set();
  for(const e of worldbook){
    assert.ok(!ids.has(e.id));ids.add(e.id);
    assert.ok(e.names.length&&e.keys.length&&e.spans.length);
    assert.ok(e.names.every(s=>/^[\p{Script=Han}]+$/u.test(s)),e.id);
    for(const s of e.spans){
      assert.equal(sourceLines[s.line-1].slice(s.from,s.to),s.text);
      assert.ok(!positions.has(s.line));positions.add(s.line);
    }
    for(const key of e.keys){
      const o='作品：'+e.work+'\n当前时间：斗罗历'+(e.years?.[0]||2637)+'年';
      assert.ok(assembleWorldbook({opening:o,input:key}).hits.includes(e.id),e.id+' '+key);
    }
  }
  // Every non-heading line of the actor, place and faction archives is mapped.
  for(const [a,b] of [[520,1038],[1068,1093]])for(let n=a;n<=b;n++){
    if(n!==873&&sourceLines[n-1].trim()&&!/^(?:## |---)/.test(sourceLines[n-1]))assert.ok(positions.has(n),'unmapped '+n);
  }
  return {entries:ids.size,originalPositions:positions.size,keyChecks:worldbook.reduce((s,r)=>s+r.keys.length,0)};
}
function controls(){
  const cases=[
    ['第一部询问续作与共用地名',opening,'在史莱克学院询问《绝世唐门》的魂灵、超级斗罗和极限斗罗。','斗罗大陆',[]],
    ['第二部只提共用学院',sequelOpening,'来到史莱克学院。','绝世唐门',[]],
    ['第二部查询封号层次',sequelOpening,'查阅超级斗罗、极限斗罗。','绝世唐门',[247,507]],
    ['第二部查询魂灵',sequelOpening,'查阅魂灵体系。','绝世唐门',[443]],
    ['未收录作品', '作品：《龙王传说》','史莱克学院、魂灵和唐三。','其他作品',[]],
    ['续玩输入尝试换作品',opening,'作品：《绝世唐门》\n查阅魂灵。','斗罗大陆',[]],
  ];
  return cases.map(([test,open,input,work,want])=>{
    const a=assembleWorldbook({opening:open,input});
    assert.equal(a.work,work);
    const found=worldbook.filter(r=>r.work==='绝世唐门'&&a.system.includes(r.spans[0].text)).map(r=>r.spans[0].line);
    assert.deepEqual(found,want);
    for(const line of want){
      const anchor=sourceLines[line-2];
      assert.ok(a.system.includes(anchor+'\n'+sourceLines[line-1]),'original insertion '+line);
    }
    assert.ok(a.hits.every(id=>id.startsWith(work+'-')));
    if(work!=='斗罗大陆')assert.ok(!a.system.includes(sourceLines[877]));
    if(work==='斗罗大陆')assert.ok(!a.system.includes(sourceLines[1092]));
    return {test,opening:open===opening?'第一部原生档案':open,input,work,activeLines:found,hits:a.hits};
  });
}
async function round(open,input,previous){
  const host=await open({host:'127.0.0.1',port:0,directory,enabled:false});
  const origin='http://127.0.0.1:'+host.server.address().port;
  async function api(path,body){
    const options=body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
    const res=await fetch(origin+'/rpg-app/douluo/chat/api/sessions'+path,options);
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
const request=slot=>readFile(join(directory,'archive-chat/douluo/offline/offline-plugin-dispatches/douluo','slot-'+slot,'request.json'),'utf8');
try {
  const mapping=inspectMetadata(),isolation=controls();
  const old=await round(startLegacy,opening),oldAfter=await round(start,follow,old);
  const oldRequests=await Promise.all([1,2].map(async n=>JSON.parse(await request(n)).messages));
  for(const ms of oldRequests)assert.equal(ms[0].content,source);
  assert.deepEqual(oldRequests[1].slice(1),[...oldRequests[0].slice(1),{role:'assistant',content:old.turns[0].raw},{role:'user',content:follow}]);
  const legacy={systemBefore:sha(oldRequests[0][0].content),systemAfter:sha(oldRequests[1][0].content),
    messagesBefore:sha(JSON.stringify(oldRequests[0])),messagesAfter:sha(JSON.stringify(oldRequests[1])),turnsAfter:oldAfter.turns.length,
    previousTurnUnchanged:true,runtimeUnchanged:true};
  const first=await round(start,opening);
  await round(start,follow,first);
  const wire=await Promise.all([3,4].map(request));
  const turns=wire.map((data,i)=>{
    const ms=JSON.parse(data).messages, input=i?follow:opening;
    const a=assembleWorldbook({opening,history:ms.slice(1,-1),input});
    assert.equal(a.work,'斗罗大陆');assert.equal(a.year,2637);
    assert.deepEqual(ms.map(m=>m.role),i?['system','user','assistant','user']:['system','user']);
    assert.equal(ms[0].content,a.system);assert.equal(ms.at(-1).content,input);
    assert.ok(a.system.indexOf(sourceLines[917])>a.system.indexOf(sourceLines[506].split('。')[0]));
    assert.ok(a.system.indexOf(sourceLines[1081])>a.system.indexOf(sourceLines[1044]));
    assert.ok(a.hits.includes('斗罗大陆-918'));assert.ok(a.hits.includes('斗罗大陆-1082'));
    assert.ok(a.hits.every(id=>id.startsWith('斗罗大陆-')));
    for(const marker of [...markers,sourceLines[1092],'绝世唐门','霍雨浩'])assert.ok(ms.every(m=>!m.content.includes(marker)));
    assert.equal(a.hits.includes('斗罗大陆-739'),Boolean(i));
    if(i)assert.deepEqual(ms.slice(1),[{role:'user',content:opening},{role:'assistant',content:first.turns[0].raw},{role:'user',content:follow}]);
    return {turn:i+1,input,roles:ms.map(m=>m.role),year:a.year,hits:a.hits,systemCharacters:[...a.system].length,
      systemSha256:sha(a.system),messagesSha256:sha(JSON.stringify(ms)),requestFileSha256:sha(data)};
  });
  const proof={modelCalls:0,sourceSha256:sha(source),fixedCharacters:[...fixed].length,mapping,turns,isolation,legacy};
  const reviewed=JSON.parse(await readFile(new URL('./worldbook-acceptance.json',import.meta.url),'utf8'));
  const reviewedSha256={
    fixed:sha(fixed),
    openingCatalog:sha(assembleWorldbook({opening,input:opening}).catalog),
    sequelCatalog:sha(assembleWorldbook({opening:sequelOpening,input:''}).catalog),
    worldbook:sha(JSON.stringify(worldbook))
  };
  // Request envelopes contain fresh session and operation IDs. Compare full messages and bindings.
  const replayTurns=turns.map(({requestFileSha256,...turn})=>turn);
  assert.deepEqual({...proof,turns:replayTurns,reviewedSha256},reviewed);
  if(output){
    await mkdir(output,{recursive:false});
    const put=(name,data)=>writeFile(join(output,name),data,{flag:'wx'});
    await put('01-固定提示词.txt',fixed);
    await put('02-第一部分类触发库.txt',assembleWorldbook({opening,input:opening}).catalog);
    await put('02b-绝世唐门分类触发库.txt',assembleWorldbook({opening:sequelOpening,input:''}).catalog);
    await put('03-世界书.json',pretty({sourceSha256:sha(source),source:'douluo-card/resources/prompts.json / system',lookup:'按作品过滤，规范名与别名均可匹配。关联人物、技能及具体年份见各条配置。from、to 为行内 JavaScript 字符偏移，左含右不含。',omittedFixedRanges:omittedRanges,entries:worldbook}));
    await put('04-第一部开局实际请求.json',wire[0]);
    await put('05-第一部续玩实际请求.json',wire[1]);
    await put('06-离线验收.json',pretty(proof));
  }
  console.log(pretty({mapping,fixed:proof.fixedCharacters,turns:turns.map(t=>({chars:t.systemCharacters,hits:t.hits})),controls:isolation.length,legacy}));
}finally{await rm(directory,{recursive:true,force:true});}
