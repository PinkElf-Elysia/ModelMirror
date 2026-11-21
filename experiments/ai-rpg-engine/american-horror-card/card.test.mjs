import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {assembleTurn,selectEntries,systemPrompt} from './assembly.mjs';
import {installAmericanHorror,americanHorrorOptions} from './integration.mjs';
import {start} from '../studio/server.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';

const resource=name=>readFile(new URL('./resources/'+name,import.meta.url),'utf8');
const book=JSON.parse(await resource('worldbook.json'));
const manifest=JSON.parse(await resource('source-map.json'));
const fixed=systemPrompt();
const names=(history,input)=>selectEntries(history,input).map(e=>e.title);
const anchor={人物:'## 关系系统',社会:'八、势力体系',战力:'三、突破规则',单作战力:'二、突破规则',起源:'美国恐怖宇宙·世界总览',事件:'📖 单作品宇宙作品库及命名规则'};

test('all source paragraphs round trip without editing, missing or duplicate lines',async()=>{
  const lines=Array(manifest.paragraphs).fill(null);
  const source=(await resource('system.txt')).split('\n');
  function put([a,b],values){
    assert.equal(values.length,b-a+1);
    for(let i=a;i<=b;i++){assert.equal(lines[i-1],null);lines[i-1]=values[i-a];}
  }
  let offset=0;
  for(const [a,b] of manifest.fixedRanges){put([a,b],source.slice(offset,offset+b-a+1));offset+=b-a+1;}
  assert.equal(offset,source.length);
  for(const e of book){put(e.range,e.body.split('\n'));assert.ok(!fixed.includes(e.body));}
  assert.ok(lines.every(x=>x!==null));
  assert.equal(sha(lines.join('\n')),manifest.sourceTextSha256);
});

test('each reviewed key can select its own entry at the specified insertion point',()=>{
  for(const e of book)for(const key of e.keys){
    const mode=e.title==='单作品实力阶位'?'单作品宇宙':'融合宇宙';
    const context='【世界模式】\n'+mode+(e.works?'\n当前触发作品：《'+(e.kind==='起源'?key:e.works[0])+'》':'');
    const input=context+'\n'+(e.group?e.group+'／':'')+key;
    const selected=selectEntries([],input);
    assert.ok(selected.some(row=>row.range[0]===e.range[0]),e.title+' / '+key);
    const text=assembleTurn(fixed,[],input).messages[0].content;
    assert.ok(text.indexOf(e.body)<text.indexOf(anchor[e.slot]),e.title);
    assert.equal(text.split(e.body).length,2);
  }
});
test('单作品模式按名字载入柜魔与哈珀一家，保留原稿分组', () => {
  const characters = ['柜魔', '威尔·哈珀', '萨迪·哈珀', '索耶·哈珀'];
  const others = ['莱斯特·比林斯', '丽塔·比林斯'];
  const opening = work => '【世界模式】\n🎬 单作品宇宙\n即将经历作品：' + work + '（新英格兰）';
  const profiles = (history, input) => selectEntries(history, input).filter(e => e.kind === '人物');
  for (const work of ['柜魔', '我们依然在这']) {
    const history = [{role:'user', content:opening(work)}];
    assert.deepEqual(selectEntries([], opening(work)).map(e => [e.kind, e.title]), [['作品', work]]);
    for (const name of characters) {
      assert.deepEqual(profiles(history, '寻找' + name).map(e => [e.title, e.group]), [[name, '我们依然在这']]);
    }
    const expected = work === '柜魔' ? [] : others;
    assert.deepEqual(profiles(history, others.join('、')).map(e => e.title), expected);
  }
  const history = [{role:'user', content:opening('柜魔')}, {role:'assistant', content:'<p>萨迪·哈珀走进房间。</p>'}];
  assert.deepEqual(profiles(history, '我跟上去。').map(e => e.title), ['萨迪·哈珀']);
  assert.deepEqual(profiles([{role:'user',content:opening('招魂1')}], characters.join('、')), []);
  const workIndex = fixed.split('\n').find(line => line.startsWith('柜魔｜'));
  for (const name of characters) assert.ok(workIndex.split('｜')[1].split('、').includes(name));
});

test('strict titles, standalone worlds, duplicate names, and panel isolation',()=>{
  const origin='【世界模式】\n🎬 单作品宇宙\n即将经历作品：招魂1（新英格兰）';
  assert.deepEqual(names([],origin+'\n埃德·沃伦，迪恩·温彻斯特'),['招魂1','埃德·沃伦']);
  assert.deepEqual(names([],'我想升级，调查信息迷雾。'),[]);
  assert.deepEqual(names([],'进入《招魂》，接着去商店。'),[]);
  assert.deepEqual(names([],'进入《招魂3》'),[]);
  assert.deepEqual(names([],'安妮·威尔克斯'),[]);
  assert.deepEqual(names([],'我拜访沃伦夫妇。'),['埃德·沃伦','洛林·沃伦']);
  const updated=[{role:'user',content:origin},{role:'assistant',content:'当前触发作品：无'}];
  assert.deepEqual(names(updated,'继续'),[]);
  assert.equal(selectEntries([],'危情十日／安妮·威尔克斯')[0].group,'危情十日');
  assert.deepEqual(names([{role:'user',content:origin},{role:'assistant',content:'<details><summary>核对</summary>当前触发作品：《招魂2》\n可能/即将经历作品：《危情十日》\n阿撒兹勒</details><p>洛林·沃伦来了。</p>'}],'继续'),['招魂2','洛林·沃伦']);
  const input='当前触发作品：《安娜贝尔1》\n可能/即将经历作品：《潜伏3》\n梵蒂冈驱魔团，埃德·沃伦，实力阶级';
  assert.equal(new Set(selectEntries([],input).map(e=>e.slot)).size,5);
  const dense=book.map(e=>e.keys[0]).join('，');
  assert.ok(assembleTurn(fixed,[],dense).messages[0].content.length-fixed.length<=12000);
  assert.equal(assembleTurn(fixed,[],'我在街口等车。').messages[0].content,fixed);
});

test('normal HTTP session creation, mock dispatch, isolation and static assets', async t => {
  const directory=await mkdtemp(join(tmpdir(),'american-horror-session-'));
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
  assert.equal(americanHorrorOptions({}),null);
  const {server}=await start({port:0,directory});
  t.after(async()=>{await new Promise(resolve=>server.close(resolve));await rm(directory,{recursive:true,force:true});});
  const origin='http://127.0.0.1:'+server.address().port;
  installAmericanHorror(server,{directory,control:{enabled:true,limit:2,baseURL:'http://server:8000',serviceToken:'synthetic-local-session-test-credential',fetcher:fake}});
  const root='/rpg-app/american-horror/chat/api/sessions';
  async function api(path,body){
    const res=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const json=await res.json();
    assert.ok(res.ok,JSON.stringify(json));
    return json;
  }
  const session=await api(root,{opening:''}), path=root+'/'+session.id;
  assert.equal(received.length,0);
  const memory=await api(path+'/memory-palace'), input='进入《招魂1》，拜访埃德·沃伦。';
  const saved=await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input});
  assert.equal(saved.turns.length,1);
  assert.equal(received[0].messages[0].content,assembleTurn(fixed,[],input).messages[0].content);
  assert.equal((await api(path)).turns.length,1);
  assert.deepEqual(await api('/rpg-app/ancient/chat/api/sessions'),[]);
  for(const [file,mime] of [['','text/html'],['background.jpg','image/jpeg'],['fonts.css','text/css']]){
    const response=await fetch(origin+'/rpg-app/american-horror/'+file);
    assert.equal(response.status,200);
    assert.ok(response.headers.get('content-type').startsWith(mime));
    if(!file)assert.match(await response.text(),/american-horror-opening/);
  }
  assert.equal((await fetch(origin+'/rpg-app/american-horror/resources/system.txt')).status,404);
});
