import test from 'node:test';
import assert from 'node:assert/strict';
import { blankDocument, capabilityDeclaration, validateCG } from './contracts.mjs';

const special = () => ({ enabled: true, showNameWhenLocked: false, hintEnabled: true, hint: '沿北岸寻找灯塔。' });
const entry = (i, meta = special()) => ({
  id: 'scene-' + i, name: '灯塔值夜', enabled: true, categoryId: null, roles: ['assistant'],
  rule: 'or', keywords: ['beacon-lantern'], target: 'chat', mode: 'regular',
  images: [{ alias: 'lantern', weight: 100 }], ...(meta ? { special: meta } : {}),
});
const cg = entries => ({ categories: [], entries });
test('普通作品保留原始字段与能力，特殊标记与独立提示可往返', () => {
  const old = cg([entry(1, null)]);
  assert.deepEqual(validateCG(old), old);
  const doc = blankDocument();
  doc.enhancements.cg = old;
  assert.ok(!capabilityDeclaration(doc).required.includes('special-cg-gallery'));
  const configured = cg([entry(1)]);
  doc.enhancements.cg = configured;
  assert.deepEqual(validateCG(configured), configured);
  assert.ok(capabilityDeclaration(doc).required.includes('special-cg-gallery'));
});
test('特殊 CG 上限由宿主校验，禁用收藏和旧普通条目不占配额', () => {
  const rows = Array.from({length:20}, (_, i) => entry(i));
  assert.equal(validateCG(cg(rows)).entries.length, 20);
  assert.throws(() => validateCG(cg([...rows,entry(20)])), {code:'AUTHORING_SPECIAL_CG_LIMIT'});
  assert.equal(validateCG(cg([...rows,entry(20,{...special(),enabled:false}),entry(21,null)])).entries.length,22);
  assert.throws(() => validateCG(cg([entry(1,{...special(),showNameWhenLocked:'yes'})])));
  assert.throws(() => validateCG(cg([entry(1,true)])));
  assert.throws(() => validateCG(cg([entry(1,{...special(),hint:'光'.repeat(2001)})])));
});

test('已标记条目切到概率仍占名额；普通图片不因 HTML 类名或描述自动升级', () => {
  const weighted = {...entry(0),mode:'weighted'};
  const others = Array.from({length:19},(_,i)=>entry(i+1));
  assert.throws(()=>validateCG(cg([weighted,...others,entry(20)])),{code:'AUTHORING_SPECIAL_CG_LIMIT'});
  const old = cg([{...entry(0,null),keywords:['<div class="beacon-lantern"></div>']}]);
  assert.deepEqual(validateCG(old),old);
});

test('仅宿主已选中特殊插图可放大，普通图、原始描述和未命中标记不产生图集触发', async()=>{
  const {buildCGPlan,CG_LIMITS}=await import('./cg.mjs');
  const {sha256}=await import('./contracts.mjs');
  const asset={alias:'lantern',fileName:'lantern.png',mediaType:'image/png',size:64,sha256:sha256('test-light')};
  const request=assistant=>({workId:'light',versionId:'v1',operationId:'test',revision:1,cg:cg([entry(1),entry(2,null)]),
    turn:{id:'turn-1',complete:true,user:'beacon-lantern',assistant},assets:[asset],draws:[],
    capacity:{scanUnits:CG_LIMITS.scanUnits,images:32,bytes:8192}});
  assert.deepEqual(buildCGPlan(request('一束光。')).chat,[]);
  const selected=buildCGPlan(request('<div class="beacon-lantern"></div>')).chat;
  assert.equal(selected[0].special,true);assert.equal(Object.hasOwn(selected[1],'special'),false);
});
