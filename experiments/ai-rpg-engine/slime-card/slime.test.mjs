import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {assembleTurn, selectEntries, systemPrompt} from './assembly.mjs';
import {reviewedPrompt,reviewedTurn} from './reviewed-assembly.mjs';
import {installSlime, slimeOptions} from './integration.mjs';
import {start} from '../studio/server.mjs';
import {canonical, sha} from '../plugins/catalog.mjs';

const resource = name => readFile(new URL('./resources/' + name, import.meta.url), 'utf8');
const book = JSON.parse(await resource('worldbook.json'));
const chapters = JSON.parse(await resource('chapters.json'));
const manifest = JSON.parse(await resource('manifest.json'));
const fixed = systemPrompt();
const names = (input, history = []) => selectEntries(history, input).map(e => e.title);

test('fixed rules, document coverage, activation keys and chapter names agree', async () => {
  const wordText = await resource('system.txt');
  assert.equal(sha(wordText), manifest.systemTextSha256);
  assert.ok(fixed.startsWith(wordText + '\n\n【世界书触发表】'));
  const spans = [...manifest.systemParagraphs, ...manifest.excludedParagraphs.map(i => [i,i])];
  for (const e of book) {
    spans.push(...(e.sourceSpans ?? [e.sourceParagraphs]));
    assert.ok(fixed.includes(e.kind + '｜' + e.title + '｜' + e.keywords.join('、')));
    assert.ok(!fixed.includes(e.body));
    for (const key of e.keywords) assert.ok(names(key).includes(e.title), key);
  }
  const all = spans.toSorted((a,b) => a[0]-b[0]).flatMap(([a,b]) => Array.from({length:b-a+1},(_,i)=>a+i));
  assert.deepEqual(all, Array.from({length:manifest.paragraphCount},(_,i)=>i+1));
  assert.equal(chapters.length,24);
  for (const c of chapters) {
    assert.ok(fixed.includes(c.title));
    assert.ok(!fixed.includes(c.body));
    assert.deepEqual(names('【时间线】'+c.title+'（王历）'),[c.title]);
  }
});

test('six insertion anchors, stable order, budgets and empty turns', () => {
  const input='进入封魔洞窟篇。紫苑与朱菜在朱拉大森林讨论魔素、战斗和自由组合。';
  const {messages} = assembleTurn(fixed, [], input);
  const text = messages[0].content;
  const pairs = [['紫苑','## 关系系统'],['朱拉大森林 / 魔国联邦坦派斯特','📖 史莱姆篇章系统'],['魔素','## 信息迷雾系统'],['战斗与代价','### 战斗演出'],['自由组合','## 日常与节奏'],['封魔洞窟篇','【世界书触发表】']];
  for (const [name,anchor] of pairs) {
    const e = [...book,...chapters].find(item => item.title===name);
    assert.ok(text.includes(e.body), name);
    assert.ok(text.indexOf(e.body)<text.indexOf(anchor));
    assert.equal(text.split(e.body).length,2);
  }
  assert.ok(text.indexOf(book.find(e=>e.title==='紫苑').body)<text.indexOf(book.find(e=>e.title==='朱菜').body));
  assert.equal(assembleTurn(fixed, [], '我坐下。').messages[0].content,fixed);
  const dense = book.flatMap(e=>e.keywords).join('，');
  assert.ok(assembleTurn(fixed,[],dense).messages[0].content.length - fixed.length <= 12000);
});

test('longest entity names, visible narrative, empty form labels and chapter continuity', () => {
  assert.deepEqual(names('尤拉萨尼亚与傀儡国吉斯塔夫'),['兽王国尤拉萨尼亚','傀儡国吉斯塔夫']);
  assert.deepEqual(names('尤拉与吉斯'),['尤拉','吉斯']);
  assert.deepEqual(names('黄金乡艾尔多拉德；螺旋王城'),['黄金乡艾尔多拉德','艾尔多拉德']);
  assert.deepEqual(names('【时间线】哥布林村与矮人王国篇\n综合战力：—\n存在值EP：—'),['哥布林村与矮人王国篇']);
  const history=[{role:'user',content:'【时间线】封魔洞窟篇'}, {role:'assistant',content:'<details><summary>检查</summary>当前时间线篇章：开国祭篇\n未来：天魔大战篇 紫苑</details><p title="吉斯">朱<span>菜</span>走来。</p>'}];
  assert.deepEqual(names('继续',history),['开国祭篇','朱菜']);
  assert.deepEqual(names('切换到苍海之泪篇',history),['苍海之泪篇','朱菜']);
});

test('normal HTTP session creation, mock dispatch, isolation and static assets', async t => {
  const directory=await mkdtemp(join(tmpdir(),'slime-session-'));
  const received=[];
  const model='google/gemini-3.8-flash';
  const parameters={temperature:.7,top_p:.8,max_tokens:16384};
  const fake=async (_url, options) => {
    if(options.method==='GET') return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
    const base=JSON.parse(options.body);
    received.push(base);
    const raw='测试叙事。',sse='data: [DONE]\n\n';
    return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical(base)),rawHash:sha(raw),sseHash:sha(sse)}});
  };
  assert.equal(slimeOptions({}),null);
  const {server}=await start({port:0,directory});
  t.after(async()=>{await new Promise(resolve=>server.close(resolve));await rm(directory,{recursive:true,force:true});});
  const origin='http://127.0.0.1:'+server.address().port;
  installSlime(server,{directory,control:{enabled:true,limit:2,baseURL:'http://server:8000',serviceToken:'synthetic-local-session-test-credential',fetcher:fake}});
  const root='/rpg-app/slime/chat/api/sessions';
  async function api(path,body){
    const res=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const json=await res.json();
    assert.ok(res.ok,JSON.stringify(json));
    return json;
  }
  const session=await api(root,{opening:''}), path=root+'/'+session.id;
  assert.equal(received.length,0);
  const memory=await api(path+'/memory-palace'), input='【时间线】开国祭篇。我拜访紫苑。';
  const saved=await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input});
  assert.equal(saved.turns.length,1);
  assert.equal(received[0].messages[0].content,reviewedTurn(reviewedPrompt(),[],input).messages[0].content);
  assert.equal((await api(path)).turns.length,1);
  assert.deepEqual(await api('/rpg-app/ancient/chat/api/sessions'),[]);
  for(const [file,mime] of [['','text/html'],['opening.gif','image/gif'],['background.jpg','image/jpeg'],['fonts.css','text/css']]){
    const response=await fetch(origin+'/rpg-app/slime/'+file);
    assert.equal(response.status,200);
    assert.ok(response.headers.get('content-type').startsWith(mime));
    if(!file)assert.match(await response.text(),/slime-opening/);
  }
  assert.equal((await fetch(origin+'/rpg-app/slime/resources/system.txt')).status,404);
});
