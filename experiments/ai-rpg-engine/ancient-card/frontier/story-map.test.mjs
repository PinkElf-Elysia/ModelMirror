import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {openHost} from './server.mjs';
import {offlineNarrator} from './transport.mjs';
import {empty} from '../../plugins/custom-config/public/config.mjs';
import {installCustomConfig} from '../../plugins/custom-config/host.mjs';

test('normal send envelope freezes the configured book; only confirmed events reach the next scene across reload',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'ancient-story-map-'));
 const packets=[];
 const options={storyMap:true,placeRegister:true,generate:async p=>{packets.push(p);return offlineNarrator(p);}};
 let host=await openHost(directory,options);
 let session=await host.create();
 const server=createServer(async(req,res)=>{
   try{let raw='';for await(const chunk of req)raw+=chunk;session=await host.send(session.id,JSON.parse(raw));res.end('{}');}
   catch(error){res.writeHead(400);res.end(error.message);}
 });
 const origins=[];installCustomConfig(server,origins,directory);
 server.listen(0,'127.0.0.1');await once(server,'listening');
 const origin='http://127.0.0.1:'+server.address().port;origins.push(origin);
 const config=empty();config.depth=1;config.system='REVIEW_NARRATOR';
 const entry=(id,word,body)=>({...config.worldbook.defaults,id,name:id,enabled:true,order:0,depth:0,keywords:[word],categoryId:null,regex:false,chance:100,mode:'regular',body,contents:[]});
 config.worldbook.entries=[entry('book','东番','REVIEW_BOOK'),entry('tool','鸡笼','TOOL_ONLY')];
 async function send(input){
   packets.length=0;
   const res=await fetch(origin+'/rpg-app/ancient/chat/api/sessions/'+session.id+'/send',{method:'POST',
     headers:{origin,'Content-Type':'application/json','X-Rpg-Custom-Config':'1'},
     body:JSON.stringify({profiles:[config],request:{input,revision:session.revision,requestId:randomUUID()}})});
   assert.equal(res.status,200,await res.text());
 }
 try{
   await send('【人生路线】帝王之路\n【帝王】崇祯帝朱由检\n【参照方式】历史基础上的推演');
   for(const q of ['闲谈茶事','请将今日的卷宗称作青册','下旨经略台湾，命郑芝龙赴大员办理交接','推进至大员捷报抵京','核对大员交割','确认这份交割'])await send(q);
   assert.equal(packets.length,0);
   await host.close();host=await openHost(directory,options);
   await send('接着写朝议');assert.equal(packets.length,1);
   const direct=packets[0];
   assert.equal(direct.tool_choice,'auto');
   assert.match(packets[0].messages[0].content,/御前新到记录：[\s\S]*大员.*改登为明/);
   assert.match(session.turns.at(-1).raw,/改登为明/);
   await send('朝中茶事如何');assert.equal(packets.length,1);
   assert.doesNotMatch(packets[0].messages[0].content,/御前新到记录/);
   await send('东番现在各归谁？');
   assert.equal(packets.length,2);
   const first=packets[0].messages,second=packets[1].messages;
   assert.doesNotMatch(first[0].content,/思维链|PHASE|Checking|即时内心反应/);
   const order=['剧情正文（1200-1500字）','【时间与环境】+【玩家状态】+【当前交互角色】','【财产状况】','【世界风云】','【关系网络】','【星记忆回廊】','【接下来】'];
   for(const packet of [direct,...packets]){
     const system=packet.messages[0].content;
     assert.ok(system.includes(order.map((name,i)=>(i+1)+'. '+name).join(system.includes('\r\n')?'\r\n':'\n')));
     assert.match(system,/可在第六组面板【接下来】之后单独附 <map_delta>/);
     assert.match(system,/无新增传闻时仅省略 <map_delta> 区块/);
     assert.doesNotMatch(system,/无新增传闻时仅写正文/);
   }
   assert.equal(packets[0].tool_choice,'auto');
   assert.deepEqual(packets[0].tools,direct.tools);
   assert.equal(packets[1].tool_choice,'none');
   assert.deepEqual(packets[1].tools,[]);
   assert.match(first[0].content,/状态栏折叠格式/);
   assert.equal(first.length,4);assert.doesNotMatch(JSON.stringify(first),/闲谈茶事/);
   assert.match(first[0].content,/REVIEW_NARRATOR/);assert.match(first[0].content,/REVIEW_BOOK/);
   assert.deepEqual(second.slice(0,-2),first);
   assert.equal(second.at(-2).tool_calls[0].id,second.at(-1).tool_call_id);
   assert.doesNotMatch(JSON.stringify(second),/TOOL_ONLY/);
   assert.deepEqual(JSON.parse(second.at(-1).content).entries.map(e=>e.holder),['明','西班牙','各社自治']);
   await send('记下传闻：商人说鸡笼换了旗，尚待核实。');
   const rumor=session.frontier.known.at(-1);assert.equal(rumor.kind,'rumor');
   assert.equal(rumor.source,session.turns.at(-1).requestId);assert.doesNotMatch(session.turns.at(-1).raw,/map_delta/);
   assert.equal(session.frontier.locations.find(e=>e.name==='鸡笼').control,'西班牙');
   await send('接着写朝议');assert.match(packets[0].messages[0].content,/御前新到记录：未经核实的转述/);
   assert.equal(session.frontier.known.filter(e=>e.kind==='rumor').length,1);
 }finally{server.closeAllConnections();await new Promise(done=>server.close(done));await host.close();await rm(directory,{recursive:true,force:true});}
});
