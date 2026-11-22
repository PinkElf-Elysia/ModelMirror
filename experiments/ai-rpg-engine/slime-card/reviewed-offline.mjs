// Capture only local synthetic traffic through the mounted card endpoint.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,rm,readFile,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {canonical,sha} from '../plugins/catalog.mjs';

const [mode, directory, output, channel] = process.argv.slice(2);
if (!['baseline','verify'].includes(mode) || !directory || !output) throw Error('baseline|verify <session-dir> <output-dir>');
await mkdir(output,{recursive:true});
const native=channel==='native-offline';
const reference='ff5fd9fd071cbe83df9663ce66614fb602fd0a71';
const engine=fileURLToPath(new URL('../',import.meta.url));
let baselineDir;
async function mount(){
  if(mode==='verify')return (await import('./integration.mjs')).installSlime;
  baselineDir=await mkdtemp(join(engine,'.slime-reference-'));
  const folder='experiments/ai-rpg-engine/slime-card/';
  const files=['integration.mjs','assembly.mjs','session-host.mjs','host.mjs','resources/system.txt','resources/worldbook.json','resources/chapters.json','transport/memory-budget.mjs','transport/memory-task-provider.mjs','transport/controlled-provider.mjs','transport/offline-store.mjs'];
  for(const name of files){
    const target=join(baselineDir,name);
    await mkdir(dirname(target),{recursive:true});
    await writeFile(target,execFileSync('git',['show',reference+':'+folder+name],{cwd:engine}));
  }
  return (await import(pathToFileURL(join(baselineDir,'integration.mjs')).href)).installSlime;
}
const installSlime=await mount();
const requests=[];
const model='google/gemini-3.8-flash';
const parameters={temperature:.7,top_p:.8,max_tokens:16384};
let reply='见习记录已经记下。';
const fetcher=async (_url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'本地验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const b=JSON.parse(options.body);
  requests.push(b);
  const raw=reply,sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),effectiveRequestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
};
const save=(file,obj)=>writeFile(join(output,file),JSON.stringify(obj,null,2)+'\n');
const server=createServer();
installSlime(server,{directory,autoSummaryV2:false,control:native?null:{enabled:true,limit:40,baseURL:'http://127.0.0.1:1',serviceToken:'synthetic-local-session-test-credential',fetcher}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port,root='/rpg-app/slime/chat/api/sessions';
async function api(path, data) {
  const res=await fetch(origin+root+path,data===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await res.json();assert.ok(res.ok,JSON.stringify(result));return result;
}
async function send(session,input,expectedFailure=false){
  const path='/'+session.id,mem=await api(path+'/memory-palace');
  const result=await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:mem.memoryRevision,expectedContextPolicyRevision:mem.effectivePolicy.revision,input});
  if(expectedFailure){assert.equal(result.operation.status,'failed');assert.equal(result.turns.length,session.turns.length);}
  else assert.equal(result.turns.length,session.turns.length+1,JSON.stringify(result.operation));
  return result;
}
async function stored(id){
  return JSON.parse(await readFile(join(directory,'archive-chat','slime',native?'offline':'real',id+'.json'),'utf8'));
}
const oldFile=native?'旧档基线-离线模式.json':'旧档基线.json';
const open='【时间线】封魔洞窟篇', first='与维尔德拉说话。', next='打听阿皮托和复活腕轮。';
try {
  if(mode==='baseline'){
    let a=await api('',{opening:open});
    if(native){
      const attempted=await send(a,first,true),old=await stored(a.id);
      await save(oldFile,{reference,id:a.id,runtime:old.runtime,history:old.history,turns:old.turns,baselineOperation:attempted.operation});
    }else{
    let b=await api('',{opening:open});
    a=await send(a,open+'。'+first);b=await send(b,open+'。'+first);
    await send(b,next);
    const bound=await stored(a.id);
    assert.equal(bound.runtime.descriptor.worldbookRevision,undefined);
    const expected=requests.at(-1)?.messages,assemblyHash=Object.values((await stored(b.id)).requests).at(-1).assemblyHash;
    await save(oldFile,{reference,id:a.id,firstTurn:a.turns[0],runtime:bound.runtime,expected,assemblyHash,requestSha:expected?sha(canonical(expected)):undefined});
    }
  }else{
    const base=JSON.parse(await readFile(join(output,oldFile),'utf8'));
    const restored=await api('/'+base.id);
    if(native)assert.deepEqual(restored.turns,base.turns);
    else assert.deepEqual(restored.turns[0],base.firstTurn);
    const after=await send(restored,next);
    assert.equal(after.turns.length,native?1:2);
    const reopened=await stored(base.id);
    assert.deepEqual(reopened.runtime,base.runtime);
    if(!native)assert.equal(Object.values(reopened.requests).at(-1).assemblyHash,base.assemblyHash);
    if(native){
      await save('旧档续玩-离线模式.json',{reference,sessionId:base.id,turns:after.turns.length,runtimeHash:reopened.runtime.hash,restoredHistory:base.history,baselineOperation:base.baselineOperation,operation:after.operation,assemblyHash:Object.values(reopened.requests).at(-1).assemblyHash});
    }else{
    assert.deepEqual(requests.at(-1).messages,base.expected);
    await save('旧档续玩.json',{sessionId:base.id,turns:after.turns.length,messages:requests.at(-1).messages,requestSha:sha(canonical(requests.at(-1).messages))});
    const {selectReviewed}=await import('./reviewed-assembly.mjs');
    const evidence=[];
    for(const [label,chapter,inputs] of [
      ['洞窟入场','封魔洞窟篇\n【入场时机】篇章开始',['在洞内找希波库特草，四处看看。']],
      ['帝国入场','帝国侵攻篇\n【入场时机】篇章开始',['阿皮托与哥布藏的情况如何？']],
      ['洞窟','封魔洞窟篇',['采集希波库特草，问利姆鲁。','我问夏尔、时空尽头与复活腕轮。']],
      ['开国祭','开国祭篇',['阿皮托与泽奇恩负责哪里？','我佩戴复活腕轮进入坦派斯特地下迷宫。']],
      ['西议会','西议会与玛莉安篇',['问阿皮托上次对阵日向的楼层。']],
      ['帝国','帝国侵攻篇',['打听阿皮托和哥布藏的战斗。']],
      ['候选一','封魔洞窟篇\n【入场时机】篇章开始',['我在洞里找希波库特草。现在能找夏尔带我去时空尽头吗？']],
      ['候选二','开国祭篇',['我问托蕾妮，阿皮托和泽奇恩分到哪片虫区，八十层推荐谁镇守？']],
      ['候选三','开国祭篇',['我在迷宫入口问：复活腕轮放在行囊里能救我吗？戴着它走到地面再遇险呢？']],
      ['候选四','帝国侵攻篇',['我向守备方打听阿皮托在哪一层迎敌、米尼兹是否生还，又问哥布藏在地面战中伤到哪里。']],
      ['黄金乡','音乐会篇',['阿尔罗斯向谁报告了魔界的门？']],
      ['圣魔','圣魔激战篇',['尼古拉斯对谁施放灵子崩坏？']],
      ['终盘','终焉之神篇',['时空尽头和天星宫有何关系？']]
    ]){
      const opening='【时间线】'+chapter;
      let session=await api('',{opening}),history=[];
      for(const [i,input] of inputs.entries()){
        const hits=selectReviewed(history,input,opening);
        session=await send(session,input);
        const packet=requests.at(-1);
        for(const hit of hits)assert.ok(packet.messages[0].content.includes(hit.body),hit.title);
        await save(label+'-'+(i+1)+'.json',packet);
        const lore=hits.map(h=>h.body).join('\n');
        evidence.push({label,turn:i+1,input,chapter,hits:hits.map(({id,title,slot})=>({id,title,slot})),loreCharacters:lore.length,requestBytes:Buffer.byteLength(JSON.stringify(packet.messages)),hash:sha(canonical(packet.messages))});
        history.push({role:'user',content:input},{role:'assistant',content:reply});
      }
    }
    await save('请求清单.json',evidence);
    }
  }
}finally{server.closeAllConnections();await new Promise(r=>server.close(r));
  if(baselineDir && dirname(baselineDir)===dirname(join(engine,'reference')))await rm(baselineDir,{recursive:true,force:true});
}
