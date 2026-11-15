import {createRequire} from 'node:module';import {mkdir,writeFile} from 'node:fs/promises';import {join} from 'node:path';import assert from 'node:assert/strict';import {startReview} from './review.mjs';
const require=createRequire(import.meta.url),{chromium}=require(process.env.R8_PLAYWRIGHT_MODULE),dir=process.argv[2],app=await startReview(dir,0),errors=[],external=[],checks=[];
await mkdir(join(dir,'browser'),{recursive:true});let browser;
try{browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>{if(new URL(r.request().url()).origin!==new URL(app.url).origin){external.push(r.request().url());return r.abort();}return r.continue();});
 await page.goto(app.url);await page.getByRole('heading',{name:'公共装配、行动确认与独立观察',exact:true}).waitFor();assert.equal(await page.locator('article').count(),3);checks.push('three-results');
 await page.getByText('原生工具调用',{exact:true}).click();assert.match(await page.locator('body').innerText(),/prepare_action/);checks.push('native-call-visible');
 assert.match(await page.locator('body').innerText(),/确认后 3/);assert.match(await page.locator('body').innerText(),/其余状态不变/);checks.push('state-receipt');
 assert.equal((await fetch(app.url+'evidence/stage-1.json')).status,200);assert.equal((await fetch(app.url+'evidence/local-auth.bin')).status,404);checks.push('evidence-allowlist');
 assert.equal((await fetch(app.url,{method:'POST'})).status,403);checks.push('read-only');await page.reload();checks.push('refresh-no-dispatch');
 await page.screenshot({path:join(dir,'browser','desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(dir,'browser','mobile.png'),fullPage:true});checks.push('narrow-layout');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);checks.push('no-errors-or-external-requests');await writeFile(join(dir,'browser','result.json'),JSON.stringify({checks,errors,external},null,2));console.log(JSON.stringify({checks:checks.length,errors,external}));
}finally{await browser?.close();await app.close();}
