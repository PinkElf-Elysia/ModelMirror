import assert from 'node:assert/strict';
import test from 'node:test';
import {chapters,selectWorldbook,worldbook} from './worldbook-catalog.mjs';
import {eligible,sha} from './worldbook-sources.mjs';

test('matches the reviewed worldbook and all 21 chapter catalogs',()=>{
  assert.equal(worldbook.length,436);
  assert.equal(chapters.length,21);
  assert.equal(sha(JSON.stringify(worldbook)),'b137a66b311fa78419acef5953181a7abe5aa85a86fdafff7bdd0405fe97f910');
  const catalogs=chapters.map(c=>{
    const result=selectWorldbook({opening:c.key});
    assert.equal(result.stage,c.key);
    assert.ok([...result.active].every(e=>eligible(e,c)));
    return '【'+c.work+' · '+c.name+(c.phase?' · '+c.phase:'')+'】\n'+result.catalog;
  }).join('\n\n');
  assert.equal(sha(catalogs),'8e98269a2dfecf4d0a4a3ad2d7d3a99624dbdf75c2e4e843983ddfdc1b7309ed');
});

test('each reviewed keyword selects its entry within an eligible chapter',()=>{
  let checked=0;
  for(const entry of worldbook){
    const chapter=entry.selection?chapters.find(c=>c.key==='cassell-entry'):chapters.find(c=>eligible(entry,c));
    assert.ok(chapter,entry.id);
    const opening='■ 时间线篇章\n  '+chapter.key+(entry.selection?'\n■ 掌握言灵（1 项）\n  1. '+entry.name+'\n     '+entry.text:'');
    for(const key of entry.keys){
      assert.ok(selectWorldbook({opening,input:'查阅'+key+'。'}).active.has(entry),entry.id+' '+key);
      checked++;
    }
  }
  assert.equal(checked,1353);
});

test('cross-volume queries respect the selected chapter',()=>{
  const input='询问楚子航关于夏弥、北京地铁、君焰和大地与山之王篇的事。';
  for(const [opening,expected] of [['cassell-entry',false],['shilan-city',false],['beijing-subway',true]]){
    const {active,stage}=selectWorldbook({opening,input});
    assert.equal(stage,opening);
    const text=[...active].map(r=>r.text).join('\n');
    for(const marker of ['大地与山之王双生子陨落','北京为拔七宗罪强开四度','与风王之瞳配合威力翻倍']){
      assert.equal(text.includes(marker),expected,marker);
    }
  }
});
