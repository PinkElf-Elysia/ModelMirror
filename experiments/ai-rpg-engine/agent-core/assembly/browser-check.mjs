import {createRequire} from 'node:module';import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';import assert from 'node:assert/strict';import {startServer} from './server.mjs';
if(!process.env.R8_PLAYWRIGHT_MODULE)throw Error('Set R8_PLAYWRIGHT_MODULE to an existing Playwright installation');
const require=createRequire(join(resolve(process.env.R8_PLAYWRIGHT_MODULE),'package.json')),{chromium}=require('./index.js');
const root=fileURLToPath(new URL('../../.rpg03-work/r8-validation/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'browser-'));
const app=await startServer({directory:join(dir,'data'),port:0,offlinePreview:true,limits:{calls:60,microUSD:4000000}}),errors=[],external=[],checks=[];let browser,page;
try{
 browser=await chromium.launch({channel:'msedge',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===new URL(app.url).origin)return route.continue();external.push(route.request().url());return route.abort();});
 page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const wait=()=>page.waitForFunction(()=>!document.querySelector('#create').disabled);
 const state=()=>page.evaluate(async()=>await(await fetch('/api/runs/'+new URL(location.href).searchParams.get('run'))).json());
 await page.goto(app.url);await page.locator('#create').click();await wait();assert.match(await page.locator('#resources').innerText(),/5/);checks.push('create-independent-run');
 await page.getByRole('button',{name:'统计数量',exact:true}).click();await wait();let v=await state();assert.equal(v.tasks[0].stages.length,2);assert.equal(v.queries[0].name,'count_registered');checks.push('registered-native-compute');
 await page.getByRole('button',{name:'查询并拟定方案',exact:true}).click();await wait();v=await state();assert.equal(v.world.resources[0].quantity,5);assert.equal(v.tasks.at(-1).adoption,'proposed');checks.push('proposal-does-not-execute');
 await page.getByRole('button',{name:'确认执行',exact:true}).click();await wait();v=await state();assert.equal(v.world.resources[0].quantity,3);assert.match(await page.locator('#receipt').innerText(),/5 → 3/);
 await page.getByRole('button',{name:'生成结果叙事',exact:true}).click();await wait();assert.match(await page.locator('#story').innerText(),/确认的行动/);checks.push('confirm-once-and-narrate');
 await page.locator('#checkpoint').click();await wait();const checkpointState=await state();
 await page.locator('#input').fill('下一段自由输入');await page.locator('#send').click();await wait();const beforeBranch=await state();
 await page.locator('#fork').click();await wait();v=await state();assert.equal(v.world.turns.length,checkpointState.world.turns.length);assert.equal(v.budget.calls,beforeBranch.budget.calls);assert.notEqual(v.id,beforeBranch.id);checks.push('exact-checkpoint-and-shared-allowance');
 await page.locator('#owner').selectOption('observation-note');await page.locator('#input').fill('这里发生过什么？');await page.locator('#send').click();await wait();v=await state();assert.equal(v.tasks.at(-1).adoption,'awaiting-adoption');
 await page.getByRole('button',{name:'采用观察记录',exact:true}).click();await wait();v=await state();assert.equal(v.tasks.at(-1).adoption,'completed');assert.equal(v.world.resources[0].quantity,3);checks.push('independent-consumer-explicit-adoption');
 const calls=v.budget.calls;await page.reload();await wait();assert.equal((await state()).budget.calls,calls);checks.push('refresh-without-dispatch');
 await page.locator('#owner').selectOption('world-dialogue');await page.getByText('离线故障样例',{exact:true}).click();await page.locator('#fault').selectOption('unknown');await page.getByRole('button',{name:'交流',exact:true}).click();await wait();v=await state();const unknownCalls=v.budget.calls;assert.equal(v.tasks.at(-1).adoption,'unknown');
 await page.getByRole('button',{name:'核对原请求',exact:true}).click();await wait();assert.equal((await state()).budget.calls,unknownCalls);checks.push('unknown-lookup-only');
 await page.locator('#fault').selectOption('');await page.screenshot({path:join(dir,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(dir,'mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.push('narrow-layout');
 await page.locator('#scene').selectOption('lighthouse');await page.locator('#create').click();await wait();await page.getByRole('button',{name:'统计数量',exact:true}).click();await wait();v=await state();assert.equal(v.queries[0].output.coverage,'unset-quantities');checks.push('second-state-shape-unset-coverage');
 await page.getByText('启用的本地能力',{exact:true}).click();await page.getByLabel('统计登记数量',{exact:true}).uncheck();await page.locator('#save-capabilities').click();await wait();await page.getByRole('button',{name:'统计数量',exact:true}).click();await wait();v=await state();assert.equal(v.tasks.at(-1).stages.length,1);checks.push('capability-disable-retains-state');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);await writeFile(join(dir,'result.json'),JSON.stringify({checks,errors,external,mode:'offline-fixture',realCalls:0,actualUSD:0},null,2));console.log(JSON.stringify({directory:dir,checks:checks.length,errors,external}));
}catch(e){if(page){await page.screenshot({path:join(dir,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(join(dir,'page.txt'),await page.locator('body').innerText());}console.error('Evidence: '+dir);throw e;}
finally{await browser?.close();await app.close();}
