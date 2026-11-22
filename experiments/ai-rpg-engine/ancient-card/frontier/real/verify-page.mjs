// Read back the real save through the existing compiled Ancient chat viewer.
// Only GETs are allowed; the page cannot submit a new operation or call a provider.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {publicSession} from '../session.mjs';
import {visibleText} from '../../../harness/measurement.mjs';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=new URL('../../../.rpg03-work/ancient-map-gemini/real-01/',import.meta.url);
const load=async p=>JSON.parse(await readFile(new URL(p,root),'utf8'));
const r=await load('result.json');
assert.equal(r.reloaded,true);assert.equal(r.passed,true);
const s=publicSession((await load('saves/records/'+r.lastCommitted.id+'.json')).data);
const base='http://127.0.0.1:18975/rpg-app/ancient/chat/';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage();
 await page.route('**/*',route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.origin!==new URL(base).origin||req.method()!=='GET')return route.abort();
  if(url.pathname===new URL(base+'api/sessions/'+s.id).pathname)return route.fulfill({json:s});
  if(url.pathname===new URL(base+'api/sessions').pathname)return route.fulfill({json:[{id:s.id,name:s.name,turns:s.turns.length}]});
  return route.continue();
 });
 await page.goto(base+'?session='+s.id);
 const panel=page.getByRole('region',{name:'京师边务状态'});await panel.waitFor();
 const prose=await page.locator('.conversation article .model-output').last().innerText();
 assert.ok(visibleText(prose).includes('后金')&&visibleText(prose).includes('回执'));
 assert.equal(await page.locator('dialog.ming-atlas[open]').count(),0);
 const registered=await panel.locator('p').first().innerText();assert.match(registered,/后金/);
 await panel.getByRole('button',{name:/展开全国舆图/}).click();
 const places=await page.locator('[data-city]').evaluateAll(nodes=>nodes.map(n=>n.querySelector('title').textContent.replace(' · ','')));
 assert.deepEqual(places,['北京明','山海关明','宁远后金','锦州明']);
 await writeFile(new URL('render-check.json',root),JSON.stringify({save:s.id,turns:s.turns.length,registered,places,openedAfterReloadedNarration:true},null,2));
 console.log(JSON.stringify({registered,places}));
}finally{await browser.close();}
