import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, mkdtemp, writeFile} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startServer} from './server.mjs';

const require = createRequire(join(resolve(process.env.R10_PLAYWRIGHT_MODULE), 'package.json'));
const {chromium} = require('./index.js');
const root = fileURLToPath(new URL('../../.rpg03-work/r10-browser/', import.meta.url));
await mkdir(root,{recursive:true});
const evidence = await mkdtemp(join(root,'check-'));
const review = process.argv.includes('--save-review');
const app = review ? null : await startServer({directory:join(evidence,'data'),port:0,offlinePreview:true,sceneChoices:true});
const url = app?.url ?? 'http://127.0.0.1:18924/';
let browser;
try {
  browser = await chromium.launch({channel:'msedge',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[], external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
    if(new URL(route.request().url()).origin===new URL(url).origin)return route.continue();
    external.push(route.request().url());return route.abort();
  });
  const click = name => page.getByRole('button',{name,exact:true}).click();
  const settled = () => page.waitForFunction(()=>document.querySelector('#notice')?.textContent.includes('已保存'));
  const view = () => page.evaluate(async()=>{
    const id=new URL(location.href).searchParams.get('run');
    return (await fetch('/api/runs/'+id)).json();
  });
  const submit = async (owner,input)=>{
    await page.selectOption('#owner',owner);await page.fill('#input',input);await click('发送');await settled();
  };
  await page.goto(url);
  await page.waitForFunction(()=>document.querySelector('#scene')?.value==='cultivation');
  await click('开始新场景');await settled();
  assert.equal((await view()).world.resources[0].quantity,5);
  await page.fill('#goal','我想向他表达谢意。');await click('看看建议');await settled();
  await page.fill('#clarify-choice','周禾');await click('补充后再看');await settled();
  await click('先表达感谢，保留丹药');
  const input='我决定自己服用 2 份养息丹。';
  await page.fill('#choice-text',input);await click('保存想法');await settled();
  await page.reload();await settled();assert.equal((await view()).tasks.length,0);
  await page.screenshot({path:join(evidence,'choice.png'),fullPage:true});
  await click('用这句话继续');await settled();
  let v=await view();assert.equal(v.tasks[0].input,input);assert.equal(v.world.resources[0].quantity,5);
  await click('确认执行一次');await settled();
  assert.match(await page.locator('#receipt').innerText(),/5 → 3/);
  await click('叙述已确认结果');await settled();
  await submit('participant-keeper','查询登记余量多少？');
  assert.match(await page.locator('#review').innerText(),/3/);
  await click('采用这段回应');await settled();
  await submit('participant-visitor','查询登记余量多少？');
  assert.match(await page.locator('#review').innerText(),/不知道/);
  await click('不采用');await settled();
  await submit('world-dialogue','继续眼前的场景');
  const before=await view();await page.reload();await settled();
  v=await view();assert.equal(v.budget.calls,before.budget.calls);assert.equal(v.world.resources[0].quantity,3);
  assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
  assert.match(v.world.turns.at(-1).body,/沈砚/);assert.match(v.world.turns.at(-1).body,/3/);
  const reviewURL=page.url();
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:join(evidence,'reopened-mobile.png'),fullPage:true});
  await page.selectOption('#scene','lighthouse');await click('开始新场景');await settled();
  assert.ok(await page.locator('.choice-panel').isHidden());await submit('world-dialogue','添油 2');
  await click('确认执行一次');await settled();await click('叙述已确认结果');await settled();
  await page.reload();await settled();assert.equal((await view()).world.resources[0].quantity,3);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const summary={reviewURL,lighthouseURL:page.url(),evidence,originalSuggestion:v.choices.saved[0].modelSuggestion.text,
    input,taskId:v.choices.saved[0].taskId,eventId:v.choices.saved[0].eventId,initial:5,final:3,
    dialogueAdopted:v.scene.entries.length,mode:'offline-fixture',realCalls:0,external,errors};
  await writeFile(join(evidence,'result.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary));
} finally {await browser?.close();await app?.close();}
