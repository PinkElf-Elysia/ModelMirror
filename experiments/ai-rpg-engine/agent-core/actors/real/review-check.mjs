import {createRequire} from 'node:module';import {mkdir,readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import assert from 'node:assert/strict';
const require=createRequire(join(resolve(process.env.R6_PLAYWRIGHT_MODULE),'package.json')),{chromium}=require('./index.js');
const directory=resolve(process.argv[2]);assert(directory.startsWith(resolve('experiments/ai-rpg-engine/.rpg03-work/actors-r6-real-')));const dest=join(directory,'browser');await mkdir(dest,{recursive:true});
const before=await readFile(join(directory,'dispatch-audit.json'),'utf8'),errors=[],blocked=[],url='http://127.0.0.1:18894/';
const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const context=await browser.newContext({viewport:{width:1440,height:1080}});await context.route('**/*',r=>{if(r.request().url().startsWith(url))return r.continue();blocked.push(r.request().url());return r.abort()});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));assert.equal((await page.goto(url)).status(),200);
 assert.match(await page.locator('#story').innerText(),/并未看到/);assert.match(await page.locator('#story').innerText(),/余量为三份/);assert.match(await page.locator('.result').innerText(),/5 → 3/);
 await page.getByText('原生查询及返回值',{exact:true}).click();assert.match(await page.locator('#story').innerText(),/query_local_state/);
 await page.screenshot({path:join(dest,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.reload();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(dest,'mobile.png'),fullPage:true});
 const response=await context.request.get(url+'evidence/assessment.json');assert.equal(response.status(),200);assert.equal((await response.json()).verified.dispatches,3);
 assert.equal((await context.request.get(url+'evidence/local-auth.bin')).status(),404);assert.equal((await context.request.post(url)).status(),403);
 assert.equal(await readFile(join(directory,'dispatch-audit.json'),'utf8'),before);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
 const result={passed:true,url,checks:['saved-role-prose','world-state','native-query-details','desktop','mobile-no-overflow','evidence-download','credential-path-rejected','writes-rejected','refresh-no-dispatch'],pageErrors:errors,outboundAttempts:blocked,additionalModelCalls:0};
 await writeFile(join(dest,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close()}
