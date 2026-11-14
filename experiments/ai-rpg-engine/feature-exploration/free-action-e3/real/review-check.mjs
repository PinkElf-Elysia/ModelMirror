import {createRequire} from 'node:module';import {resolve,join} from 'node:path';import {readFile,writeFile} from 'node:fs/promises';import assert from 'node:assert/strict';import {hash} from '../../../agent-core/contracts.mjs';
if(!process.argv[2])throw Error('Pass existing Playwright package directory');
const require=createRequire(resolve(process.argv[2],'package.json'));assert.equal(require('./package.json').name,'playwright');const {chromium}=require('./index.js');
const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e3-clarification-real-v1'),url='http://127.0.0.1:18884/',before=hash(await readFile(join(dir,'transport-evidence.json'),'utf8')),errors=[];
const browser=await chromium.launch({channel:'msedge',headless:true});
try{const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.route('**/*',r=>new URL(r.request().url()).origin===new URL(url).origin?r.continue():r.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(url);
 assert.match(await page.textContent('body'),/0.05499225/);assert.equal(await page.getByRole('heading',{name:/请求 [12] ·/}).count(),2);
 await page.getByText('Gemini 原始 JSON 回复',{exact:true}).first().click();
 assert.match(await page.locator('details[open] pre').first().textContent(),/他已经答应收了/);
 await page.screenshot({path:join(dir,'review-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(dir,'review-mobile.png'),fullPage:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.reload();assert.equal(hash(await readFile(join(dir,'transport-evidence.json'),'utf8')),before);
 assert.equal((await fetch(url,{method:'POST'})).status,403);assert.equal((await fetch(url+'evidence/not-allowed.json')).status,404);
 assert.equal((await fetch(url+'evidence/reply-2.txt')).status,200);assert.deepEqual(errors,[]);
 await writeFile(join(dir,'browser-review.json'),JSON.stringify({checks:['two real turns rendered','raw response inspectable','desktop and mobile','refresh no dispatch','POST rejected','file allowlist','download raw reply'],errors,transportHash:before,realCallsAdded:0},null,2)+'\n',{flag:'wx'});
 console.log('E3 read-only review verified; no new calls.');
}finally{await browser.close();}
