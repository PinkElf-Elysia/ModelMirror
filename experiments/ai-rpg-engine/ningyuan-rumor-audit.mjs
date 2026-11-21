import {mkdtemp,writeFile,readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {source} from './character/revisions/test-support.mjs';
import {digest} from './character/revisions/contracts.mjs';
import {ContextStore} from './character/manual-memory/store.mjs';
import {Parameters} from './character/long-context/parameters.mjs';
import {defaults} from './character/manual-memory/planner.mjs';
import {opening,newRun,project} from './feature-exploration/ancient-map/world.mjs';
import {turn,initialize,examples,fixture} from './feature-exploration/ancient-map/agent.mjs';
const root='C:/tmp/mm-ningyuan-rumor-evidence';
await mkdir(root,{recursive:true});
const dir=await mkdtemp(join(root,'run-'));
const seen='我在值房门口看见营中聚集。', rumor='一名同袍说宁远失城了，我没有亲见失城。', thought='我私下怀疑他是在推卸责任。', falsehood='宁远已被攻陷。';
const result=[],packets={};
function record(name,expected,actual,ok){result.push({name,expected,actual,ok});if(!ok)console.error(JSON.stringify(packets[Object.keys(packets).at(-1)]));assert.ok(ok,name);}
async function chat(kind){
 const s=source(),actor=s.current.members[1];actor.name='陆守义';s.cards.actor.document.character.card.data.name='陆守义';
 s.kind=kind;s.derived=[];s.current.scene='宁远营中';
 if(kind==='group'){s.current.members.push({...structuredClone(actor),id:'peer',name:'旁听者'});s.cards.peer=structuredClone(s.cards.actor);s.cards.peer.document.character.card.data.name='旁听者';}
 s.initial=structuredClone(s.current);
 for(const m of s.messages)m.boundary=structuredClone(s.current);
 s.messages[1].content='你看见什么，听说什么？';s.messages[2].content=seen+rumor;
 s.messages[4].content=thought;s.messages[4].visibility={kind:'limited',memberIds:['actor']};
 delete s.sourceHash;s.sourceHash=digest(s);
 const opts={directory:join(dir,kind),sources:{read:async()=>structuredClone(s),verify:async()=>structuredClone(s)},parameters:new Parameters({})};
 let store=await ContextStore.open(opts);
 try{
 const {id,branchId}=await store.importSource({operationId:'import',expectedRevision:null,reference:{kind:'context',familyId:'original',branchId:'branch'}});
 let seq=0;
 const cmd=async payload=>({operationId:'audit-'+(++seq),expectedRevision:(await store.read(id)).revision,payload:{branchId,...payload}});
 const preview=async(label,targetId='actor',short=false)=>{const p=await store.preview(id,await cmd({kind:'send',targetId,input:null,settings:short?{policy:{...defaults(),historyLimit:0},parameters:{modelId:'fixture',values:{}}}:null}));packets[kind+' '+label]=p.plan.messages;return JSON.stringify(p.plan.messages);};
 const own=await preview('本人正文重放');record(kind+' 本人正文','亲见、转述、私念各自保留',seen+rumor+thought,[seen,rumor,thought].every(x=>own.includes(x)));
 if(kind==='group'){const other=await preview('在场听众','peer');record('群聊旁听者','收到公开转述，收不到私念',rumor,other.includes(rumor)&&!other.includes(thought));}
 const note=async(kind,content,ref,owner)=>{await store.mutate(id,'draft',await cmd({actor:'actor',noteId:null,owner,kind,origin:'experience',refs:[{id:'m'+ref,revisionId:'r'+ref}],content,mode:'fixed',words:[]}));const n=(await store.panel(id,branchId,'actor')).records.findLast(x=>x.status==='draft');await store.mutate(id,'adopt',await cmd({actor:'actor',noteId:n.noteId}));return n;};
 await note('summary',rumor,2,'actor');await note('memory',thought,4,'actor');
 const before=await preview('仅采纳摘要与记忆','actor',true);record(kind+' 窗口裁剪','摘要保留转述，私有记忆可用，原亲见正文退出窗口',rumor+thought,before.includes(rumor)&&before.includes(thought)&&!before.includes(seen));
 if(kind==='group'){const other=await preview('旁听者裁剪后','peer',true);record('他人私有记忆','旁听者请求排除私念',String(other.includes(thought)),!other.includes(thought));}
 await store.close();store=await ContextStore.open(opts);
 record(kind+' 读档','重开后请求一致','比较完整消息数组',before===await preview('读档','actor',true));
 await note('summary',falsehood,2,'actor');const bad=await preview('故意采纳失真摘要','actor',true);
 record(kind+' 反例复现','检查引用合法但语义失真的摘要能否进入请求',falsehood,bad.includes(falsehood));
 }finally{await store.close();}
}
await chat('single');await chat('group');
const s=initialize(newRun(opening('soldier')));const controls=x=>project(x).places.map(p=>[p.name,p.control]);const initial=controls(s);
await turn(s,examples.rumor,async p=>{(packets['地图登记']??=[]).push(structuredClone(p));return fixture(p);});
record('地图登记','登记为传闻，带来源，四节点归属相同',JSON.stringify(s.agent.events),s.agent.events[0].kind==='rumor'&&s.agent.events[0].source==='camp-rumor'&&JSON.stringify(initial)===JSON.stringify(controls(s)));
await turn(s,'你看见什么，听说什么？',async()=>({status:'completed',finishReason:'stop',message:{role:'assistant',content:seen+rumor}}));
const checkpoint=join(dir,'world.json');await writeFile(checkpoint,JSON.stringify(s));const loaded=JSON.parse(await readFile(checkpoint));
await turn(loaded,'继续谈',async p=>{packets['地图读档重放']=structuredClone(p);return {status:'completed',finishReason:'stop',message:{role:'assistant',content:falsehood}};});
await turn(loaded,'再继续',async p=>{packets['地图错误正文后续请求']=structuredClone(p);return fixture(p);});
record('地图反例复现','错误正文可进入后续请求，同时城防记录仍为原值',JSON.stringify(controls(loaded)),JSON.stringify(packets['地图读档重放']).includes(rumor)&&JSON.stringify(packets['地图错误正文后续请求']).includes(falsehood)&&JSON.stringify(controls(loaded))===JSON.stringify(initial));
await writeFile(join(dir,'requests.json'),JSON.stringify(packets,null,2));await writeFile(join(dir,'results.json'),JSON.stringify(result,null,2));
const esc=x=>x.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const html='<!doctype html><meta charset=utf-8><title>宁远传闻 · 离线核对</title><style>body{max-width:850px;margin:48px auto;padding:0 20px;font:16px/1.65 system-ui;color:#21343b}section,details{border-top:1px solid #ccc;padding:10px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>宁远失城传闻</h1><p>离线固定样例。单聊和群聊使用角色上下文工作台的真实装配、手工采纳及读档。地图使用四节点 turn 和 JSON 检查点。三者独立，无跨宿主同步。样例正文和错误摘要为人为注入。</p><h2>最早出现偏差的位置</h2><p>删去来源的摘要在人工采纳时进入可用资料。地图中错误的正文在采用时进入日志，下一轮重放。两项反例均已复现；实际模型发生频率未知。自动总结、逐人送达与跨宿主读档未接入。</p>'+result.map(r=>'<section><b>'+esc(r.name)+'</b><br>'+esc(r.expected)+'<br>'+esc(r.actual)+'</section>').join('')+'<h2>实际组装的离线请求</h2>'+Object.entries(packets).map(([k,v])=>'<details><summary>'+esc(k)+'</summary><pre>'+esc(JSON.stringify(v,null,2))+'</pre></details>').join('');
await writeFile(join(dir,'index.html'),html);console.log(dir,result.length);
if(process.argv.includes('--serve'))createServer((req,res)=>{if(req.url!=='/'){res.writeHead(404).end();return;}res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);}).listen(23908,'127.0.0.1');
