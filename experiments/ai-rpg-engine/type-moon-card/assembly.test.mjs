import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {assembleTurn, catalogEntries, nameLibrary, selectEntries, servantBody, systemPrompt, timelineNames, worldbookEntries} from './assembly.mjs';

const base=systemPrompt();
const source=JSON.parse(readFileSync(new URL('./resources/source-segments.json',import.meta.url),'utf8'));
const turns=(history,input,opening='')=>assembleTurn(base,history,input,opening).messages;
const entries=(history,input,opening='')=>{
  const hits=selectEntries(history,input,opening);
  return worldbookEntries.filter(s=>hits.entries.has(s.start)).map(s=>s.title);
};

test('source covers every OOXML paragraph in order and the fixed prompt contains the actual published key library',()=>{
  let start=1;
  for(const s of source.segments){
    assert.equal(s.start,start);
    assert.ok(s.end>=s.start);
    start=s.end+1;
  }
  assert.equal(start-1,1626);
  assert.equal(catalogEntries.length,461);
  assert.ok(base.includes(nameLibrary()));
  const rows=nameLibrary().split('\n').filter(line=>/^No\.\d+ /.test(line));
  assert.deepEqual(rows,catalogEntries.map(c=>c.numberedName));
  assert.equal(new Set(catalogEntries.map(c=>c.displayName)).size,461);
  for(const c of catalogEntries) {
    assert.ok(!c.numberedName.includes('｜'));
    assert.deepEqual([...selectEntries([],c.numberedName).servants],[c.no],c.numberedName);
  }
  assert.equal(rows[0],'No.1 玛修·基列莱特');
  assert.equal(rows[169],'No.170 武则天（暗匿者）');
  assert.equal(rows[357],'No.358 武则天（术士）');
  for(const spelling of ['盾娘','Saber Alter','メルトリリス','Assassin 武则天'])assert.ok(!rows.join('\n').includes(spelling));

  const worldRows=nameLibrary().split('【世界书条目】\n')[1].split('\n【英灵图鉴】')[0].split('\n');
  assert.deepEqual(worldRows,worldbookEntries.map(e=>e.displayName));
  assert.equal(new Set(worldRows).size,134);
  for(const name of worldRows)assert.doesNotMatch(name,/[A-Za-z｜／]/);
  for(const key of ['Excalibur','Fate/Zero','UBW','英灵卫宫'])assert.ok(!nameLibrary().includes(key));
  for(const n of [1,2,76,126,413]){
    const s=source.segments.find(w=>w.no===n);
    assert.ok(!base.includes(s.body.trim()),n);
    assert.ok(turns([],'图鉴编号'+n)[0].content.includes(s.body),n);
  }
});
test('matching records occupy their original slots between the semantic anchors',()=>{
  const out=turns([],'FGO，藤丸立香在时钟塔查阅图鉴 No.3。');
  const system=out[0].content;
  const lore=source.segments.find(s=>s.title==='藤丸立香').body;
  const third=readFileSync(new URL('./resources/reviewed-no3.txt',import.meta.url),'utf8');
  const clock=source.segments.find(s=>s.title==='魔术协会·时钟塔').body;
  const markers=['<Worldline_Divergence_Protocol>',lore,'###英灵图鉴（全）至 No.461',third,clock,'<Memory_rules>','<OutputFormat>'];
  let previous=-1;
  for(const marker of markers){const at=system.indexOf(marker);assert.ok(at>previous,marker.slice(0,45));previous=at;}
  assert.deepEqual(out.map(m=>m.role),['system','user']);
});
test('chapter continuity, timeline changes and private panels do not cause unrelated matches',()=>{
  const history=[{role:'user',content:'第四次圣杯战争，我在码头等候。'},{role:'assistant',content:'<!-- No.3 --><p>码头安静了下来。</p><details>第五次圣杯战争、No.413、远坂凛</details>'}];
  assert.ok(entries(history,'去找肯尼斯').includes('第四次·Lancer阵营'));
  assert.ok(!entries(history,'第五次圣杯战争，去找库·丘林。').some(s=>s.startsWith('第四次')));
  assert.deepEqual([...selectEntries(history,'继续。').servants],[]);
  assert.ok(!entries([],'终章，断章，南极').includes('FGO终章·南极'));
  assert.deepEqual([...selectEntries([],'Lostbelt No.4').servants],[]);
  assert.deepEqual([...selectEntries([],'No.4611').servants],[]);
  assert.deepEqual([...selectEntries([],'No.3 与 No.413').servants],[3,413]);
  assert.deepEqual([...selectEntries([],'Saber').servants],[]);
  const switched=turns(history,'去找肯尼斯');
  assert.deepEqual(switched.slice(1,-1),history);
});
test('canonical forms and unambiguous aliases resolve one record and do not activate sibling forms',()=>{
  assert.deepEqual([...selectEntries([],'Saber 阿尔托莉雅·潘德拉贡〔Alter〕').servants],[3]);
  assert.deepEqual([...selectEntries([],'Saber Alter').servants],[3]);
  assert.deepEqual([...selectEntries([],'月姬R，青子').servants],[]);
  for(const c of catalogEntries) {
    const r=selectEntries([], '英灵编号'+c.no);
    assert.deepEqual([...r.servants],[c.no],c.no);
    const sample = c.nameOnly ? c.name : (c.keys.find(k => k.toLowerCase().startsWith(c.className.toLowerCase()+' ')) || c.keys[0]);
    assert.deepEqual([...selectEntries([],sample).servants],[c.no],sample);
  }
});
test('ARDS rules remain in place while character-specific illustrations require their own matches',()=>{
  for(const marker of ['【ARDS-Driven Synthesis Protocol v4.0】','记忆连续性审查','NPC表情选择校验','**在场NPC清单:**'])assert.ok(base.includes(marker),marker);
  const illustration=source.segments.find(s=>s.title==='玩家行动意图·绮礼');
  assert.ok(!base.includes(illustration.body));
  const selected=turns([],'第四次圣杯战争，言峰绮礼出现在教堂。')[0].content;
  assert.ok(selected.includes(illustration.body));
  const example=source.segments.find(s=>s.kind==='inline'&&s.start===1295);
  assert.ok(base.includes(example.before+example.after));
  assert.ok(!base.includes(example.body));
});
test('only current timeline and chapter panel fields restore the present arc',()=>{
  const scene=(line,chapter,next)=>'<details><summary>当前信息</summary><pre>🕓时间线：'+line+'\n📅 当前篇章：'+chapter+'\n📅 预计下一篇章：'+next+'\n</pre></details>';
  const history=[{role:'assistant',content:'<p>风停了。</p>'+scene('Fate Grand Order','第一章：南极','No.413')}];
  assert.ok(entries(history,'继续前进。').includes('FGO终章·南极'));
  assert.deepEqual([...selectEntries(history,'继续前进。').servants],[]);
  history.push({role:'assistant',content:'<p>雪融了。</p>'+scene('FGO','幕间','第一章：南极')});
  assert.ok(!entries(history,'继续。').includes('FGO终章·南极'));
  assert.ok(entries([],'冬木市').includes('公共传闻·冬木市'));
});
test('verified appearance stays inside each active supplement, with one heading per new record',()=>{
  const reviewed=new Set([1,2,3,76,126,413]);
  for(const c of catalogEntries.filter(c=>!reviewed.has(c.no))){
    const body=servantBody(c);
    for(const heading of ['外貌与灵基状态（默认初始灵基）','性格与御主相处｜','宝具演绎｜']){
      assert.equal(body.split(heading).length-1,1,c.no+' '+heading);
    }
    assert.ok(c.sourceRevision>0 && c.atlasCollectionNo===c.no,c.no);
  }
  const card=catalogEntries.find(c=>c.no===301);
  const body=servantBody(card);
  assert.ok(!base.includes(body));
  assert.ok(turns([],'英灵编号301')[0].content.includes(body));
});
test('published Remnant names resolve by identity and class without granting an NPC that knowledge',()=>{
  const unique = new Map([['黑森·罗伯',158], ['燕青',159], ['山鲁佐德',169], ['彭忒西勒亚',171], ['克里斯托弗·哥伦布',172], ['望月千代女',185], ['喀耳刻',192], ['示巴女王',194]]);
  const shared = new Map([['Archer 詹姆斯·莫里亚蒂',156],['Ruler 詹姆斯·莫里亚蒂',346],
    ['Assassin 武则天',170],['Caster 武则天',358],['Archer 巴御前',184],['Saber 巴御前',290]]);
  for (const [key,no] of [...unique,...shared]) {
    assert.deepEqual([...selectEntries([],key).servants],[no],key);
    assert.ok(catalogEntries[no-1].keys.includes(key),key);
  }
  for (const key of ['詹姆斯·莫里亚蒂','武则天','巴御前','言峰绮礼','间桐樱']) assert.deepEqual([...selectEntries([],key).servants],[],key);
  assert.deepEqual([...selectEntries([],'去找武则天（杀阶）。').servants],[170]);
  assert.deepEqual([...selectEntries([],'新宿的Assassin正向我走来。').servants],[159]);
  assert.ok(systemPrompt().includes('章节中尚未揭晓时，叙述、对白及面板沿用已知代称'));
});

test('all canonical worldbook names find their original source slots inside their own scene',()=>{
  const baseIndex=nameLibrary();
  for(const entry of worldbookEntries) {
    const input=entry.displayName;
    const opening=timelineNames[entry.scope] || '';
    const result=selectEntries([],input,opening);
    assert.ok(result.entries.has(entry.start),input);
    for(const other of worldbookEntries) {
      if(other.baseName===entry.baseName && other.start!==entry.start)
        assert.ok(!result.entries.has(other.start),input+' / '+other.displayName);
    }
    const expected=source.segments.flatMap(s=>{
      if(s.kind==='servant')return [];
      if(s.kind==='inline')return [s.before+(result.entries.has(s.start)?s.body:'')+s.after];
      if(s.kind!=='fixed')return result.entries.has(s.start)?[s.body]:[];
      const fixed=s.body.trim()?[s.body]:[];
      if(s.start!==917)return fixed;
      return [...fixed,baseIndex,...catalogEntries.filter(c=>result.servants.has(c.no)).map(c=>'【No.'+c.no+' '+c.className+' '+c.name+'】\n'+servantBody(c))];
    }).join('\n');
    assert.equal(turns([],input,opening)[0].content,expected,entry.displayName);
  }
});

test('a reference to the fourth war relic inherits the active scene and an explicit switch still wins',()=>{
  const relic='第四次圣杯战争的遗物';
  const check=(history,input,opening='')=>[...selectEntries(history,input,opening).entries];
  assert.ok(check([],relic,'第五次圣杯战争').includes(710));
  assert.ok(check([{role:'assistant',content:'<p>第五次圣杯战争，士郎在冬木。</p>'}],relic).includes(710));
  assert.ok(check([],relic).includes(554));
  const switched=check([],'转入第四次圣杯战争，谈起'+relic,'第五次圣杯战争');
  assert.ok(switched.includes(554) && !switched.includes(710));
  assert.ok(check([],'藤丸立香（冠位指定·南极终章）').includes(1134));
  assert.ok(!check([],'藤丸立香（冠位指定·南极终章）').includes(876));
  const unqualified=check([],'藤丸立香','FGO终章·南极');
  assert.ok(unqualified.includes(876) && unqualified.includes(1134));
});
