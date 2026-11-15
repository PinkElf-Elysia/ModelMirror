import {createRequire} from 'node:module';import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';import {join,resolve} from 'node:path';import assert from 'node:assert/strict';import {fileURLToPath} from 'node:url';
if(!process.env.R9_PLAYWRIGHT_MODULE)throw Error('Set R9_PLAYWRIGHT_MODULE to an existing Playwright installation');
const require=createRequire(join(resolve(process.env.R9_PLAYWRIGHT_MODULE),'package.json')),{chromium}=require('./index.js');
const root=fileURLToPath(new URL('../../../.rpg03-work/r9-validation/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'real-review-'));
const url='http://127.0.0.1:18921/',checks=[],errors=[],external=[];
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const ctx=await browser.newContext({viewport:{width:1440,height:1050}});
 await ctx.route('**/*',route=>{if(new URL(route.request().url()).origin===new URL(url).origin)return route.continue();external.push(route.request().url());return route.abort();});
 const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));const before=await (await ctx.request.get(url+'evidence/task-ledger.json')).text();
 await page.goto(url);assert.match(await page.locator('h1').innerText(),/回应与观察/);checks.push('review-page-loaded');
 assert.equal(await page.locator('#replies article').count(),3);assert.match(await page.locator('#replies').innerText(),/两份/);checks.push('three-original-replies');
 assert.match(await page.locator('body').innerText(),/5 → 3/);assert.match(await page.locator('body').innerText(),/人物采用记录 2/);checks.push('state-and-adoption');
 assert.match(await page.locator('header').innerText(),/\$0.00505950/);assert.equal(await page.locator('#usage tbody tr').count(),3);checks.push('reported-cost-and-three-rows');
 assert.match(await page.locator('#usage').innerText(),/未测得/);checks.push('missing-ttft-not-fabricated');
 await page.getByText('语义核对与残余问题',{exact:true}).click();assert.match(await page.locator('body').innerText(),/物理位置/);await page.getByText('语义核对与残余问题',{exact:true}).click();checks.push('residuals-retained');
 const pre=await (await ctx.request.get(url+'evidence/after-slot-2.json')).json(),post=await (await ctx.request.get(url+'evidence/after-adopt-2.json')).json();assert.equal(pre.scene.entries.length,0);assert.equal(post.scene.entries.length,1);checks.push('adoption-evidence-accessible');
 assert.equal((await ctx.request.post(url+'api/dispatch',{data:{}})).status(),405);assert.equal((await ctx.request.get(url+'evidence/local-auth.bin')).status(),404);checks.push('read-only-and-credential-denied');
 await page.reload();assert.equal(await (await ctx.request.get(url+'evidence/task-ledger.json')).text(),before);checks.push('refresh-ledger-unchanged');
 await page.screenshot({path:join(directory,'desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(directory,'mobile.png'),fullPage:true});checks.push('narrow-layout');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);await writeFile(join(directory,'result.json'),JSON.stringify({checks,errors,external,additionalModelCalls:0},null,2));console.log(JSON.stringify({directory,checks:checks.length,errors,external}));
}finally{await browser.close();}
