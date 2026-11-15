import {createRequire} from 'node:module';import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';import assert from 'node:assert/strict';import {startServer} from './server.mjs';
if(!process.env.R9_PLAYWRIGHT_MODULE)throw Error('Set R9_PLAYWRIGHT_MODULE to an existing Playwright installation');
const require=createRequire(join(resolve(process.env.R9_PLAYWRIGHT_MODULE),'package.json')),{chromium}=require('./index.js');
const root=fileURLToPath(new URL('../../.rpg03-work/r9-validation/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'browser-'));
const app=await startServer({directory:join(dir,'data'),port:0,offlinePreview:true,delayMs:0}),checks=[],errors=[],external=[];let browser,page;
try{
 browser=await chromium.launch({channel:'msedge',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===new URL(app.url).origin)return route.continue();external.push(route.request().url());return route.abort();});
 page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const wait=()=>page.waitForFunction(()=>!document.querySelector('#create').disabled);
 const state=()=>page.evaluate(async()=>await(await fetch('/api/runs/'+new URL(location.href).searchParams.get('run'))).json());
 const say=async(owner,text)=>{await page.locator('#owner').selectOption(owner);await page.locator('#input').fill(text);await page.locator('#send').click();await wait();};
 await page.goto(app.url);await page.locator('#create').click();await wait();let v=await state();assert.equal(v.adapterId,'lighthouse');assert.equal(v.world.resources[0].quantity,5);checks.push('independent-lighthouse');
 await say('participant-keeper','现在如何值守？');v=await state();assert.equal(v.scene.entries.length,0);assert.equal(v.tasks[0].adoption,'awaiting-adoption');assert.equal(await page.locator('#send').isDisabled(),true);checks.push('draft-before-adoption');
 await page.getByRole('button',{name:'采用这段回应',exact:true}).click();await wait();v=await state();assert.equal(v.scene.entries.length,1);assert.match(await page.locator('#story').innerText(),/沈砚/);checks.push('explicit-adoption');
 await page.locator('#checkpoint').click();await wait();const checkpoint=v.id;
 await say('world-dialogue','添油 2');v=await state();assert.equal(v.world.resources[0].quantity,5);checks.push('proposal-before-execution');
 await page.getByRole('button',{name:'确认执行一次',exact:true}).click();await wait();v=await state();assert.equal(v.world.resources[0].quantity,3);assert.match(await page.locator('#receipt').innerText(),/5 → 3/);
 await page.getByRole('button',{name:'叙述已确认结果',exact:true}).click();await wait();v=await state();assert.match(v.world.turns.at(-1).body,/沈砚/);checks.push('single-action-and-receipt-prose');
 await say('participant-keeper','查询登记余量多少？');v=await state();assert.equal(v.tasks.at(-1).stages.length,2);assert.match(await page.locator('#review').innerText(),/3/);
 await page.getByRole('button',{name:'采用这段回应',exact:true}).click();await wait();checks.push('scoped-native-query-after-action');
 await say('participant-visitor','刚才处理好了吗？');assert.match(await page.locator('#review').innerText(),/不知道/);await page.getByRole('button',{name:'不采用',exact:true}).click();await wait();checks.push('unobserved-participant-and-discard');
 await say('world-dialogue','继续眼前的场景');v=await state();assert.equal(v.world.resources[0].quantity,3);assert.match(v.world.turns.at(-1).body,/3/);assert.equal(v.scene.entries.length,2);checks.push('world-continuation-uses-adopted-speech');
 const calls=v.budget.calls;await page.reload();await wait();assert.equal((await state()).budget.calls,calls);checks.push('refresh-no-dispatch');
 await page.screenshot({path:join(dir,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(dir,'mobile.png'),fullPage:true});checks.push('mobile-no-overflow');
 await page.setViewportSize({width:1440,height:1000});await page.locator('#fork').click();await wait();v=await state();assert.notEqual(v.id,checkpoint);assert.equal(v.world.resources[0].quantity,5);assert.equal(v.scene.entries.length,1);assert.equal(v.budget.calls,calls);checks.push('checkpoint-restores-world-and-dialogue');
 await page.locator('#diagnostics > summary').click();await page.locator('#fault').selectOption('unknown');await say('participant-keeper','你好');
 v=await state();const unknownCalls=v.budget.calls;assert.equal(v.tasks.at(-1).adoption,'unknown');await page.getByRole('button',{name:'核对原请求',exact:true}).click();await wait();v=await state();assert.equal(v.budget.calls,unknownCalls);assert.equal(v.tasks.at(-1).adoption,'awaiting-adoption');await page.getByRole('button',{name:'不采用',exact:true}).click();await wait();checks.push('unknown-recovery-without-redispatch');
 await page.locator('#fault').selectOption('');await page.locator('#scene').selectOption('cultivation');await page.locator('#create').click();await wait();await say('participant-keeper','查询库存多少？');v=await state();assert.equal(v.tasks.at(-1).stages.length,2);assert.equal(v.world.resources[0].quantity,5);await page.getByRole('button',{name:'采用这段回应',exact:true}).click();await wait();checks.push('cultivation-reuses-participant-port');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 await writeFile(join(dir,'result.json'),JSON.stringify({checks,errors,external,realCalls:0,actualUSD:0,mode:'offline-fixture'},null,2));
 console.log(JSON.stringify({directory:dir,checks:checks.length,errors,external}));
}catch(e){if(page){await page.screenshot({path:join(dir,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(join(dir,'page.txt'),await page.locator('body').innerText());}console.error('Evidence: '+dir);throw e;}
finally{await browser?.close();await app.close();}
