import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mkdtemp,rm} from 'node:fs/promises';
import {openHost} from './server.mjs';
import {startRegister,registerTurn,registerView,overview} from './place-register.mjs';
import {offlineNarrator} from './transport.mjs';

const scroll='【人生路线】帝王之路\n【帝王】崇祯帝朱由检\n【参照方式】历史基础上的推演';
const refs=s=>s.frontier.locations.map(p=>p.control);
test('双地点共用交割：签旨、送达、核准、重送与重开；各社和鸡笼保留登记',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'frontier-register-'));
  let host=await openHost(directory,{placeRegister:true});
  try{
    let s=await host.create();
    const req=input=>({requestId:randomUUID(),revision:s.revision,input});
    const send=async input=>s=await host.send(s.id,req(input));
    await send(scroll);
    const before=refs(s);
    await send('朕下旨经略东番，命郑芝龙往大员办理接收。');
    assert.ok(s.frontier.actions.includes('签发这份旨意'));assert.deepEqual(s.frontier.known,[]);
    await send('签发这份旨意');
    await send(overview);
    assert.deepEqual(refs(s),before);assert.deepEqual(s.frontier.known.map(e=>e.kind),['order']);
    await send('推进至大员捷报抵京');
    const receipt=s.frontier.known.at(-1);assert.ok(receipt.receivedAt>receipt.occurredAt);
    await send('核对大员交割');await send('撤回这份提案');
    assert.deepEqual(refs(s),before);
    await send('核对大员交割');const confirm=req('确认这份交割');
    await Promise.all([host.send(s.id,confirm),host.send(s.id,confirm)]);
    await host.close();host=await openHost(directory,{placeRegister:true});
    s=await host.get(s.id);
    const settled=s.frontier.known.filter(e=>e.kind==='settled');assert.equal(settled.length,1);assert.equal(settled[0].source,receipt.id);
    assert.deepEqual(refs(s),['明','明','明','明','明','西班牙','各社自治']);
    await send(overview);assert.match(s.turns.at(-1).raw,/大员，簿上登记为明/);
    await send('开局时大员由谁登记？');assert.match(s.turns.at(-1).raw,/大员·荷兰/);
    for(const q of ['签发宁远移交旨意','推进至宁远接防回执抵京','核对宁远交割','确认这份交割'])await send(q);
    assert.deepEqual(refs(s),['明','明','后金','明','明','西班牙','各社自治']);
  }finally{await host.close();await rm(directory,{recursive:true,force:true});}
});
test('正文不能写入地图，收到大员文书也不能据此交割鸡笼',async()=>{
  const w=startRegister(),raw='本档已经管辖全岛。';
  const fake=async()=>({status:'completed',finishReason:'stop',message:{role:'assistant',content:raw}});
  assert.equal(await registerTurn(w,'续写朝会',fake),raw);assert.deepEqual(w.facts,[]);
  const ask=async text=>{w.clock++;return registerTurn(w,text,offlineNarrator);};
  await ask('下旨经略台湾，命郑芝龙赴大员办理交接');await ask('推进至大员捷报抵京');
  const malicious=async packet=>{
    const out=offlineNarrator(packet);
    if(out.message.tool_calls?.[0].function.name==='propose_map_change'){
      const fn=out.message.tool_calls[0].function;
      fn.arguments=JSON.stringify({...JSON.parse(fn.arguments),node:'鸡笼'});
    }
    return out;
  };
  await assert.rejects(()=>registerTurn(w,'核对大员交割',malicious),/同地/);
  assert.equal(w.pending,null);assert.equal(registerView(w).locations[5].control,'西班牙');
});