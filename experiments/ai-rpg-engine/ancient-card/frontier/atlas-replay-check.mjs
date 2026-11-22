// Only local GETs reach the saved game. The Shanhaiguan case uses an in-memory projection.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {emptySession,play,publicSession} from './session.mjs';
import {randomUUID} from 'node:crypto';

const base=new URL(process.env.ATLAS_SAVED_URL);
assert.equal(base.hostname,'127.0.0.1');
const sessionId=base.searchParams.get('session');
assert.ok(sessionId);
const api=new URL('api/sessions/'+sessionId,base);
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage({viewport:{width:1280,height:720}});
  const response=await fetch(api);assert.equal(response.status,200);
  const saved=await response.json();assert.match(saved.turns.at(-1).raw,/后金/);
  const requests=[];
  let sample;
  await page.route('**/*',route=>{
    const req=route.request();
    if(req.method()!=='GET' || new URL(req.url()).origin!==base.origin){requests.push(req.method());return route.abort();}
    if(sample && req.url()===api.href)return route.fulfill({json:sample});
    return route.continue();
  });
  base.searchParams.set('atlas','1');
  await page.goto(base.href);
  const dialog=page.getByRole('dialog',{name:'大明舆图'});
  await dialog.getByRole('button',{name:'定位辽东'}).click();
  assert.deepEqual(await dialog.locator('[data-holder]').evaluateAll(nodes=>nodes.map(n=>n.dataset.holder)),['明','明','后金','明']);
  const basis=dialog.locator('.atlas-basis');
  assert.match(await basis.innerText(),/第 8 刻确认.*明.*后金/);
  assert.ok(await basis.evaluate(el=>el.getBoundingClientRect().bottom<=el.closest('aside').getBoundingClientRect().bottom));
  if(process.env.ATLAS_CAPTURE_DIR){
    await mkdir(process.env.ATLAS_CAPTURE_DIR,{recursive:true});
    await page.screenshot({path:join(process.env.ATLAS_CAPTURE_DIR,'05-saved-gemini.png')});
  }
  async function capture(name){
    if(process.env.ATLAS_CAPTURE_DIR)await page.screenshot({path:join(process.env.ATLAS_CAPTURE_DIR,name+'.png')});
  }
  const range=dialog.getByRole('button',{name:'周边形势',exact:true});
  const selector=dialog.getByLabel('查阅地点');
  await range.click();
  assert.equal(await dialog.locator('[data-polity]').count(),13);
  assert.equal(await dialog.locator('[data-reference-city]').count(),59);
  const outside=await dialog.locator('[data-polity] text').evaluateAll(labels=>labels.filter(el=>{
    const a=el.getBoundingClientRect(),b=el.closest('svg').getBoundingClientRect();
    return a.left<b.left || a.right>b.right || a.top<b.top || a.bottom>b.bottom;
  }).map(el=>el.textContent));
  assert.deepEqual(outside,[]);
  assert.equal(await dialog.locator('[data-range]').count(),42);
  // Test the actual rendered polygons in sheet coordinates, independently of the city markers.
  const inArea=(id,x,y)=>dialog.locator('[data-range="'+id+'"] > g > path:first-child').evaluateAll(
    (paths,[x,y])=>paths.some(path=>path.isPointInFill(new DOMPoint(x,y))),[x,y]);
  assert.ok(await inArea('later-jin',739.6,274.2));
  for(const [x,y] of [[707.6,302.5],[711.3,292.6]]){
    assert.ok(await inArea('ming',x,y));assert.equal(await inArea('later-jin',x,y),false);
  }
  for(const id of ['dutch','spanish'])assert.equal(await inArea(id,779,600),false);
  assert.equal(await dialog.locator('[data-city="ningyuan"]').getAttribute('data-holder'),'后金');
  await capture('06-neighbors');
  for(const [extent,label,id,place] of [
    ['northeast','详看东北','khorchin','科尔沁'],['northwest','详看西北','turfan','吐鲁番']
  ]){
    await dialog.getByRole('button',{name:label}).click();
    assert.ok(await dialog.locator('[data-area-label="'+id+'"]').isVisible());
    assert.ok(await dialog.locator('.atlas-rivers path').count()>10);
    await capture('09-'+extent);
    await selector.selectOption(id);
    assert.match(await dialog.locator('aside h3').innerText(),new RegExp(place));
    assert.ok(await dialog.getByRole('link',{name:'范围资料'}).isVisible());
    await range.click();
  }
  for(const id of ['guge','ladakh','kharachin','taiwan-communities','ryukyu','mak','amur','hami']){
    await selector.selectOption(id);
    assert.ok(await dialog.locator('aside .atlas-range-caption').isVisible());
  }
  await range.click();
  await dialog.locator('[data-polity="later-jin"] text').click();
  assert.match(await dialog.locator('aside').innerText(),/皇太极驻沈阳/);
  assert.equal(await dialog.locator('aside .atlas-site-links button').count(),9);
  await capture('07-later-jin');
  for(const [id,label] of [['keelung','鸡笼'],['tayouan','大员'],['trinh','郑主'],['nguyen','阮主'],['pegu','勃固'],['edo','江户'],['xian','西安府']]){
    await selector.selectOption(id);
    assert.match(await dialog.locator('h3').innerText(),new RegExp(label));
    if(await dialog.locator('[data-reference-city="'+id+'"]').count()){
      assert.ok(await dialog.locator('[data-reference-city="'+id+'"]').evaluate(el=>{
        const a=el.getBoundingClientRect(),b=el.closest('svg').getBoundingClientRect();
        return a.left>b.left && a.right<b.right && a.top>b.top && a.bottom<b.bottom;
      }));
    }
  }
  await page.setViewportSize({width:390,height:844});
  await range.click();await capture('08-mobile-neighbors');
  await selector.selectOption('later-jin');
  assert.equal(await dialog.locator('aside .atlas-site-links button').count(),9);
  await page.setViewportSize({width:1280,height:720});
  const simulated=emptySession();
  await play(simulated,{requestId:randomUUID(),revision:0,input:'【人生路线】帝王之路\n【帝王】崇祯帝朱由检'});
  simulated.world.facts.push({id:'gate-letter',kind:'report',node:'山海关',text:'关门点验已毕。',occurredAt:0,deliveries:[{recipient:'court',at:1}]});
  simulated.world.clock=1;
  sample={...publicSession(simulated),id:sessionId};
  await page.reload();
  await dialog.getByLabel('查阅地点').selectOption('shanhaiguan');
  assert.match(await dialog.locator('aside').innerText(),/关门点验已毕/);
  await dialog.getByLabel('查阅地点').selectOption('ningyuan');
  assert.doesNotMatch(await dialog.locator('aside').innerText(),/关门点验已毕/);
  assert.equal(await dialog.locator('[data-city=ningyuan]').getAttribute('data-holder'),'明');
  assert.deepEqual(requests,[]);
  assert.equal((await (await fetch(api)).json()).revision,saved.revision);
  console.log('真实存档只读回放通过；山海关离线夹具只显示于该城，守方未变；页面全程仅本地 GET。');
}finally{await browser.close();}
