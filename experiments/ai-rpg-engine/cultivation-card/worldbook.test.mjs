import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {assembleWorldbook as assemble,entries,library,libraryGuide,libraryNames,source,slots} from './worldbook.mjs';
import {createStory,playTurn} from './story.mjs';
import {frozenCardAssembly} from '../studio/generation-cards.mjs';

test('all original slots reconstruct the author file and the catalog contains only scoped Chinese names',()=>{
  assert.equal(slots.map(s=>s.body).join(''),source);
  assert.equal(createHash('sha256').update(source).digest('hex'),'9a1e707828659b3ee1daa9628c334c1782b6526b066287836e7f8db7806a5dc2');
  assert.equal(new Set(libraryNames).size,libraryNames.length);
  assert.ok(libraryNames.every(n=>/^[\p{Script=Han}·]+(?:（(?:凡界|仙界|神界)）)?$/u.test(n)));
  assert.ok(!libraryNames.includes('百花谷')&&['百花谷（凡界）','百花谷（仙界）'].every(n=>libraryNames.includes(n)));
  assert.ok(!libraryNames.includes('锤千锻')&&!libraryNames.includes('YHWH'));
});
test('the model index preserves every canonical key and shows what the names refer to',()=>{
  const names=new Set(entries.flatMap(e=>[e.name,...(e.rows??[]).flatMap(r=>r.names)]));
  const expected=new Set();
  for(const name of names){
    const realms=new Set(entries.filter(e=>e.name===name||e.rows?.some(r=>r.names.includes(name))).map(e=>e.realm));
    for(const realm of realms)expected.add(realms.size===1?name:name+'（'+realm+'）');
  }
  const indexed=library.split('\n').filter(line=>line.includes('｜')).flatMap(line=>line.split('｜')[1].split('、'));
  assert.deepEqual(new Set(indexed),expected);
  assert.equal(indexed.length,expected.size);
  assert.ok(library.startsWith(libraryGuide));
  const mortal=library.split('【凡界】')[1].split('【仙界】')[0];
  for(const [category,name] of [['地点','青牛镇'],['势力','青云宗'],['事件','天机楼预警'],['规则','丹师品级'],['势力兼地点','巫神殿'],['  青牛镇','任重']]){
    const row=mortal.split('\n').find(line=>line.startsWith(category+'｜'));
    assert.ok(row?.split('｜')[1].split('、').includes(name),category+' '+name);
  }
});
test('all explicit author conditions preserve their dossiers and require divine context',()=>{
  const conditional=entries.filter(e=>e.originalTrigger);
  assert.equal(conditional.length,49);
  assert.equal([...source.matchAll(/当对话中出现「[^」]+」且涉及神界相关内容时触发此词条。/g)].length,conditional.length);
  for(const e of conditional){
    for(const [realm,input,want] of [['神界',e.name,true],['凡界',e.name,false],['仙界',e.name,false],['凡界','查阅神界典籍中的'+e.name,true]]){
      const built=assemble({opening:'当前所在界：'+realm,input});
      assert.equal(built.realm,realm);assert.equal(built.system.includes(e.body),want,e.id+' '+input+' '+realm);
    }
  }
});
test('scenes, aliases, qualified names, hidden and future destinations',()=>{
  const town='出身界：神界\n当前所在界：凡界\n所在区域：东域\n具体地点：青牛镇';
  const a=assemble({input:town});
  assert.deepEqual(a.hits.map(h=>h.id),['L688','L1234','L3351']);
  const expected=slots.filter(s=>s.kind==='fixed'||['L688','L1158','L1234','L2892','L3351'].includes(s.id)).map(s=>(s.id==='L688'?'【世界书中文规范名】\n'+library+'\n\n':'')+s.body).join('');
  assert.equal(a.system,expected);
  const history=[{role:'user',content:town},{role:'assistant',content:'镇上开市了。<details><summary>世界</summary>【世界信息】\n当前界域：凡界\n【当前位置】\n凡界 ＞ 东域 ＞ 青牛镇\n【可前往】\n神界的女娲，仙界百花谷</details><think>查阅神界的女娲</think>'}];
  assert.equal(assemble({history,input:'去集市。'}).system,expected);
  assert.ok(assemble({history,input:'我已飞升至仙界，在百花谷投帖。'}).hits.every(h=>h.realm==='仙界'));
  assert.ok(assemble({history,input:'百花谷（仙界）'}).hits.every(h=>h.name!=='百花谷'));
  for(const realm of ['凡界','仙界']){
    const hits=assemble({opening:'当前所在界：'+realm,input:'前往百花谷'}).hits.filter(h=>h.name==='百花谷');
    assert.equal(hits.length,1);assert.equal(hits[0].realm,realm);
  }
  assert.ok(assemble({input:'请教锤千锻'}).hits.some(h=>h.name==='钟千锤'));
  assert.ok(assemble({opening:'当前所在界：神界',input:'YHWH'}).hits.some(h=>h.name==='上帝'));
  const row=assemble({input:'拜访李存仁。'}).hits.find(h=>h.id==='L1234');
  assert.ok(row.selectedLines.includes(1238));
});
test('classic and optional generation hosts replay legacy and split new saves independently',async()=>{
  const input='当前所在界：凡界\n具体地点：青牛镇';
  const fresh=await createStory('sample',input,false);
  const legacy={...structuredClone(fresh),system:{role:'system',content:source}};
  delete legacy.worldbookVersion;
  for(const [s,want] of [[fresh,assemble({input}).system],[legacy,source]]){
    await playTurn(s,input,{generate:async(_,n,m)=>{assert.equal(m[0].content,want);return '早市开张。';},save:async()=>{}});
    await playTurn(s,'去集市',{generate:async(_,n,m)=>{assert.equal(m[0].content,want);return '买好麻绳。';},save:async()=>{}});
  }
  const frozen=await frozenCardAssembly('cultivation');
  for(const [descriptor,expected] of [[{},source],[{hostArtifacts:{'cultivation-card/worldbook.mjs':'versioned'}},assemble({input}).system]]){
    const s={characterText:input,history:[],runtime:{descriptor}};
    assert.equal(frozen(s,input).messages[0].content,expected);
    s.history=[{role:'user',content:input},{role:'assistant',content:'早市开张。'}];
    assert.equal(frozen(s,'去集市').messages[0].content,expected);
  }
});
