import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {preview} from './ordinary-preview.mjs';
import {offlineNarrator} from './transport.mjs';
import {empty} from '../../plugins/custom-config/public/config.mjs';
import {entries} from '../worldbook.mjs';
const directory=await mkdtemp(join(tmpdir(),'ancient-ordinary-map-'));
const opening='【人生路线】帝王之路\n【帝王】崇祯帝朱由检\n【参照方式】历史基础上的推演';
const book='御前称地点登记卷为青册。',toolOnly='仅鸡笼关键词触发的测试条目';
const quote='辽东宁远、锦州后金游骑偶有出没，关外诸军请发冬衣银两。';
const config=empty();config.depth=1;
config.worldbook.entries=['东番','鸡笼'].map((word,i)=>({...config.worldbook.defaults,id:'book-'+i,name:word,enabled:true,order:i,depth:0,keywords:[word],categoryId:null,regex:false,chance:100,mode:'regular',body:[book,toolOnly][i],contents:[]}));
const tea='茶毕，再议朝事。',overview='东番现在各归谁？',probe='前一回说宁远游骑出没，京师收到哪份宁远塘报？';
const localActions=['下旨经略台湾，命郑芝龙赴大员办理交接','推进至大员捷报抵京','核对大员交割','确认这份交割'];
function narrator(packet){
  const res=packet.tool_choice==='none' && packet.messages.at(-1).role==='user' && !packet.messages.at(-1).content.includes('【人生路线】')
    ? {status:'completed',finishReason:'stop',message:{role:'assistant',content:'阁臣听候裁示。'}}
    : offlineNarrator(packet);
  if(packet.messages.at(-1).content===tea)res.message.content+='\n【世界风云】'+quote;
  return res;
}
async function exercise(enabled){
  const dir=join(directory,enabled?'map-on':'map-off');
  let app=await preview(dir,0,enabled,narrator);
  let s,record;
  async function api(suffix,data,profile){
    const response=await fetch(app.origin+'/rpg-app/ancient/chat/api/sessions'+suffix,data?{method:'POST',headers:{Origin:app.origin,'Content-Type':'application/json',...(profile?{'X-Rpg-Custom-Config':'1'}:{})},body:JSON.stringify(profile?{profiles:[config],request:data}:data)}:{});
    const value=await response.json();assert.ok(response.ok,JSON.stringify(value));return value;
  }
  const measurements=[];
  async function send(input){
    const route='/'+s.id;
    const memory=await api(route+'/memory-palace'),before=s.turns.length;
    s=await api(route+'/send',{operationId:randomUUID(),input,expectedSessionRevision:s.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision},true);
    assert.equal(s.turns.length,before+1,JSON.stringify(s.operation));
    const turn=s.turns.at(-1);
    record=JSON.parse(await readFile(join(dir,'archive-chat/ancient/offline/atlas-offline-evidence',turn.requestId+'.json'),'utf8'));
    for(const stage of record.stages){
      const sys=stage.input.messages[0].content;
      assert.deepEqual(entries.filter(e=>sys.includes(e.text)).map(e=>e.name),['明朝']);
      assert.equal(sys.includes(book),input===overview);
      assert.ok(!sys.includes(toolOnly));
    }
    const bytes=x=>Buffer.byteLength(typeof x==='string'?x:JSON.stringify(x));
    measurements.push({input,stages:record.stages.map(({input:p,output})=>({roles:p.messages.map(m=>m.role),systemBytes:bytes(p.messages[0].content),cardWorldbookBytes:bytes(entries.find(e=>e.name==='明朝').text),userBookBytes:p.messages[0].content.includes(book)?bytes(book):0,historyBytes:bytes(p.messages.slice(1,p.messages.findLastIndex(m=>m.role==='user'))),currentBytes:bytes(input),toolSchemaBytes:bytes(p.tools),toolResultBytes:p.messages.filter(m=>m.role==='tool').reduce((a,m)=>a+bytes(m.content),0),outputBytes:bytes(output.message)})),reply:turn.raw});
    return turn.raw;
  }
  try{
    s=await api('',{opening:''});await send(opening);
    if(!enabled){for(const input of [...localActions,'接着写朝议。',tea,probe,overview])await send(input);assert.equal(s.frontier,undefined);assert.deepEqual(record.stages[0].input.tools,[]);}
    else{
      for(const input of localActions)await send(input);
      assert.equal(s.frontier.known.filter(f=>f.kind==='settled').length,1);
      assert.match(await send('接着写朝议。'),/大员.*改登为明/);assert.equal(record.stages.length,1);
      await send(tea);assert.ok(!record.stages[0].input.messages[0].content.includes('御前新到记录：'));
      const known=s.frontier.known;
      assert.match(await send(probe),/尚无关于此地的新报/);
      assert.ok(record.stages[0].input.messages.some(m=>m.role==='assistant'&&m.content.includes(quote)));
      assert.equal(JSON.parse(record.stages[1].input.messages.at(-1).content).entries[0].evidence.length,0);
      assert.deepEqual(s.frontier.known,known);
      const previous=s;
      await app.close();app=await preview(dir,0,true,narrator);s=await api('/'+s.id);
      assert.deepEqual(s.turns,previous.turns);assert.deepEqual(s.frontier,previous.frontier);
      await send(overview);assert.equal(record.stages.length,2);
      const [first,last]=record.stages.map(x=>x.input);
      assert.deepEqual(last.messages.slice(0,-2),first.messages);
      assert.equal(last.messages.at(-1).tool_call_id,last.messages.at(-2).tool_calls[0].id);
      assert.deepEqual(JSON.parse(last.messages.at(-1).content).entries.map(e=>e.holder),['明','西班牙','各社自治']);
      assert.deepEqual(s.frontier.known,known);
    }
    return {dir,id:s.id,measurements};
  }finally{await app.close();}
}
console.log('evidence directory',directory);
const on=await exercise(true),off=await exercise(false);
assert.deepEqual(on.measurements.map(t=>t.input),off.measurements.map(t=>t.input));
await writeFile(join(directory,'comparison.json'),JSON.stringify({providerCalls:0,on,off},null,2));
console.log(JSON.stringify({directory,id:on.id,on:on.measurements.length,off:off.measurements.length}));
