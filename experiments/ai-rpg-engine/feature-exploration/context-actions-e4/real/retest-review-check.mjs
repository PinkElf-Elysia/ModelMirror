import {createRequire} from 'node:module';import {mkdir,readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),{chromium}=require('C:/Users/21547/AppData/Local/npm-cache/_npx/51691537fc71f2b0/node_modules/playwright');
const root=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-gift-retest-real-v1'),out=join(root,'browser');await mkdir(out,{recursive:true});
const url='http://127.0.0.1:18903',browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],external=[];
page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>{if(new URL(r.request().url()).origin!==url){external.push(r.request().url());return r.abort();}return r.continue();});
const files=['review.json','transport-evidence.json','source-state.json','source-after.json'];const before=await Promise.all(files.map(n=>readFile(join(root,n),'utf8')));
try{
 const response=await page.goto(url);assert.equal(response.status(),200);const text=await page.locator('body').innerText();
 for(const fragment of ['1 次真实请求','0.00259500','条件值未逐字保留玩家原文','旧接受已失效','实际库存始终为 5','已删除'])assert(text.includes(fragment),fragment);
 await page.getByText('Gemini 原始回复（未修补）',{exact:true}).click();assert((await page.locator('pre').first().innerText()).startsWith('```json'));
 await page.screenshot({path:join(out,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(out,'mobile.png'),fullPage:true});
 const raw=await fetch(url+'/evidence/reply-1.txt');assert.equal(await raw.text(),await readFile(join(root,'reply-1.txt'),'utf8'));
 const artifact=await fetch(url+'/evidence/seed.json');assert.equal((await artifact.json()).draft.action,'use_medicine');
 assert.equal((await fetch(url+'/evidence/router.sqlite3')).status,404);assert.equal((await fetch(url,{method:'POST'})).status,403);
 await page.reload();assert.deepEqual(await Promise.all(files.map(n=>readFile(join(root,n),'utf8'))),before);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 const result={checks:9,errors,external,sourceAndEvidenceUnchanged:true,realCallsAdded:0,readOnly:true};await writeFile(join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
