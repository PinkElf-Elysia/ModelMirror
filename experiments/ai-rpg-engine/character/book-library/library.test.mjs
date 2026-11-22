import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startPreview} from './preview.mjs';
import {source as sample} from '../revisions/test-support.mjs';
import {apply,copy,digest} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {defaults} from '../long-context/planner.mjs';
import {prepare} from '../long-context/request.mjs';
import {freezeGrants,resolve,pin} from './bindings.mjs';
import {assemble} from './assembly.mjs';
import {id} from '../revisions/contracts.mjs';

const root=process.env.CHARACTER_R5A_TEST_ROOT??tmpdir();
const entry=(content,extra={})=>({id:9,name:'同名条目',keys:['渡口'],content,enabled:true,insertion_order:0,extensions:{},...extra});
const book=(...entries)=>({extensions:{},entries});
function version(bookId,book,number=1){
  const title='同名手册',hash=digest({title,book});
  return {bookId,number,title,book,hash,bookVersionId:id('bv',bookId,number,hash)};
}
function library(...versions){const out={};for(const v of versions)(out[v.bookId]??={archived:false,versions:{}}).versions[v.bookVersionId]=v;return out;}
function family(s){delete s.sourceHash;s.sourceHash=digest(s);return apply(null,{action:'create',payload:{source:s},operationId:'create',expectedRevision:null});}
function project(f,grants,lib,settings=defaults(),input='渡口',targetId='actor'){
  return assemble(f,'preview',{kind:'send',input,targetId,branchId:f.rootId},fixtureModel(),settings,grants,lib);
}
const grant=(owner,v,readers=['actor'])=>({owner,ref:pin(v),readers});

test('跨层同书只选一次，较高层整版覆盖；同层冲突与篡改拒绝',()=>{
  const v=version('one',book(entry('第一版'))),v2=version('one',book(entry('第二版')),2),lib=library(v,v2),s=sample(),f=family(s);
  const gs=[grant('player',v),grant('session',v2),grant('role',v2)];
  const r=project(f,gs,lib);assert.equal(r.books.sources.length,1);assert.equal(r.books.sources[0].bindings.length,3);
  assert.equal(r.request.context[0].content.split('第二版').length,2);assert.doesNotMatch(r.request.context[0].content,/第一版/);
  assert.deepEqual(project(f,gs.toReversed(),lib),r);
  assert.throws(()=>resolve(lib,[grant('role',v),grant('role',v2)],'actor'),/LAYER_CONFLICT/);
  const broken=copy(lib);broken.one.versions[v.bookVersionId].book.entries[0].content='更改';
  assert.throws(()=>resolve(broken,[grant('role',v)],'actor'),/BOOK_HASH/);
  assert.throws(()=>resolve({},[grant('role',v)],'actor'),/VERSION_UNAVAILABLE/);
  assert.deepEqual(resolve({},[grant('role',v,['other'])],'actor'),[]);
});
test('异书同名同 ID 并存，递归仅限于本书，预算、顺序和位置稳定',()=>{
  const f=family(sample()),a=version('a',book(entry('甲', {position:'before_char'}))),b=version('b',book(entry('乙',{position:'after_char'}),entry('丙',{keys:['甲']})));
  const c=version('c',{...book(entry('长'.repeat(40))),token_budget:1}),d=version('d',book(entry('丁')));
  const lib=library(a,b,c,d),g=[grant('session',a),grant('role',b),grant('role',c),grant('player',d)];
  const r=project(f,g,lib,{...defaults(),bookTokens:2});
  const sys=r.request.context[0].content;
  assert.ok(sys.indexOf('甲')<sys.indexOf('原创测试角色')&&sys.indexOf('乙')>sys.indexOf('原创测试角色'));
  assert.doesNotMatch(sys,/丙|丁|长长/);assert.deepEqual(r.books.entries.map(e=>e.status),['triggered','triggered','not-triggered','book-budget','request-budget']);
  assert.equal(new Set(r.books.entries.map(e=>e.ref.bookVersionId+':'+e.index)).size,5);
});
test('按成员参与区间及接收范围扫描，当前全部角色固定，同名不共享',()=>{
  const s=sample();s.kind='group';s.current.members[1].intervals=[{from:2,until:3},{from:5,until:null}];
  s.messages.forEach((m,i)=>{m.content=['加入前','曾参与','密送','缺席','重入'][i];});s.messages[2].visibility={kind:'restricted',memberIds:['player']};
  const other={...copy(s.current.members[1]),id:'other',intervals:[{from:1,until:null}]};s.current.members.push(other);s.cards.other=copy(s.cards.actor);
  const v=version('private',{...book(...['加入前','曾参与','密送','缺席','重入'].map(k=>entry(k+'知识',{keys:[k]}))),scan_depth:16});
  const lib=library(v),gs=freezeGrants(lib,[{ref:pin(v),readers:'all'}],s.current.members.slice(0,2),'player'),f=family(s);
  const a=project(f,gs,lib,defaults(),'查阅'),b=project(f,gs,lib,defaults(),'查阅','other');
  assert.deepEqual(a.books.entries.map(x=>x.status),['not-triggered','triggered','not-triggered','not-triggered','triggered']);
  assert.deepEqual(b.books,{sources:[],entries:[]});assert.doesNotMatch(JSON.stringify(b.request.context),/知识/);
  assert.throws(()=>freezeGrants(lib,[{ref:pin(v),readers:['alien']}],s.current.members,'player'),/BOOK_READER/);
  f.branches[f.rootId].current.members[1].intervals.at(-1).until=5;assert.throws(()=>project(f,gs,lib),/MEMBER_INACTIVE/);
});
test('仅卡内书与原装配字节一致，精确重生成不改原上下文',()=>{
  const s=sample();s.cards.actor.document.character.card.data.character_book=book(entry('卡内书'));const f=family(s),p={kind:'send',input:'渡口',branchId:f.rootId,targetId:'actor'};
  const r=assemble(f,'one',p,fixtureModel(),defaults()),expected=prepare(f,'one',p,fixtureModel(),defaults());
  assert.deepEqual(r.request,expected.request);assert.deepEqual(r.plan,expected.plan);
  const regen=assemble(f,'other',{branchId:f.rootId,kind:'regenerate',messageId:'m2'},fixtureModel(),defaults(),[grant('role',version('missing',book()))],{});
  assert.deepEqual(regen.request.context,s.messages[2].generation.context);
});
test('真实本地宿主：角色封存、推荐与授权、原请求、历史分支、并发、归档和重启',async t=>{
  await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'books-'));let server=await startPreview(dir);
  t.after(()=>server.close());const api=(...args)=>server.api(...args),write=(...args)=>server.write(...args);
  let catalog=await api('catalog'),f=catalog.families[0],parent={familyId:f.id,branchId:f.branches[0].id};
  const read=(where,targetId=null)=>api('view',{...where,targetId});
  const ref=(b,n=0)=>pin(b.versions[n]);
  const pub=catalog.books.find(b=>b.title==='渡口手册'),priv=catalog.books.find(b=>b.title==='行旅笔记'),v1=ref(pub),secret=ref(priv);
  const info=await read(parent),[a,b]=info.members.map(m=>m.id);
  assert.equal(info.bindings.length,1);assert.equal(info.bindings[0].sources.length,2);
  const group=catalog.sessions.find(s=>s.kind==='group'),groupFile=join(dir,'groups','ledger','records','groups.json'),original=await readFile(groupFile);
  assert.deepEqual((await api('source',{kind:'group',sourceId:group.id})).recommended,[secret]);
  const single=catalog.sessions.find(s=>s.kind==='single'),si=await api('source',{kind:'single',sourceId:single.id});
  const singleBranch=await write('open',{kind:'single',sourceId:single.id,sourceHash:si.sourceHash,session:[],player:[]});assert.equal((await read(singleBranch)).bindings[0].selected.hash,v1.hash);
  const fork=(where,changes={})=>({...where,title:'续聊',point:null,player:null,session:null,...changes});
  const child=await write('fork',fork(parent,{player:[{ref:secret,readers:[a]}]}));
  const preview=(where,targetId,op,input='渡口',messageId=null)=>{const intent={branchId:where.branchId,targetId,input,messageId};return api('preview',{familyId:where.familyId,intent,operationId:op}).then(result=>({intent,result}));};
  const p=await preview(child,a,'send-1'),other=await preview(child,b,'private-test');
  assert.match(p.result.messages[0].content,/白棋/);assert.doesNotMatch(JSON.stringify(other.result),new RegExp('白棋|'+secret.hash+'|行旅笔记'));
  const send={expectedRevision:p.result.revision,operationId:'send-1',payload:{familyId:f.id,intent:p.intent,previewHash:p.result.previewHash}};
  const sent=await api('send',send);assert.deepEqual(await api('send',send),sent);
  const state=await read(child,a),message=state.messages.at(-1);assert.ok(message.canRegenerate);
  const rr=await preview(child,a,'regen',null,message.id);assert.deepEqual(rr.result.messages,p.result.messages);assert.equal(rr.result.requestHash,p.result.requestHash);
  assert.deepEqual((await api('recover',{familyId:f.id,requestId:sent.requestId})),{requestId:sent.requestId});
  const pre=await write('fork',fork(child,{point:state.messages[0].id})),post=await write('fork',fork(child,{point:message.id}));
  assert.equal((await read(pre,a)).bindings.length,1);assert.equal((await read(post,a)).bindings.length,2);assert.equal((await read(parent,a)).bindings.length,1);
  const sameRevision=(await api('catalog')).revision;
  const next={bookId:pub.id,title:pub.title,book:book(entry('新版本：青石桥'))};
  await api('save',{operationId:'change',expectedRevision:sameRevision,payload:next});
  await assert.rejects(api('save',{operationId:'stale',expectedRevision:sameRevision,payload:next}),/REVISION_CONFLICT/);
  const second=await write('seal',{bookId:pub.id});
  await assert.rejects(write('fork',fork(parent,{session:[{ref:v1,readers:[a]},{ref:second,readers:[a]}]})),/LAYER_CONFLICT/);
  await assert.rejects(write('fork',fork(parent,{player:[{ref:secret,readers:[si.members[0].id]}]})),/BOOK_READER/);
  const replace=fork(child,{session:[{ref:second,readers:'all'}]});
  const drycmd={operationId:'replace',expectedRevision:(await api('catalog')).revision,payload:replace},review=await api('inspect-fork',drycmd);
  assert.deepEqual(review.actors[0].books[0].sources.map(s=>s.hash),[second.hash,v1.hash]);
  const newer=await api('fork',drycmd);await write('archive',{bookId:pub.id});
  await server.close();server=await startPreview(dir);
  assert.match((await preview(newer,a,'fresh')).result.messages[0].content,/青石桥/);
  assert.match((await preview(parent,a,'old')).result.messages[0].content,/铜铃/);
  assert.doesNotMatch((await preview(child,a,'old-child')).result.messages[0].content,/青石桥/);
  const origin=server.url.split('/characters')[0],res=await fetch(origin+'/rpg-app/api/authoring/characters/export',{method:'POST',headers:{origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify({workId:catalog.roles[0].id,versionId:null,format:'json',version:'original',acceptLoss:false})});
  assert.equal(res.status,200);const exported=Buffer.from((await res.json()).base64,'base64').toString();assert.doesNotMatch(exported,/白棋|bookVersionId|readers|青石桥|行旅笔记/);
  assert.deepEqual(await readFile(groupFile),original);
});
