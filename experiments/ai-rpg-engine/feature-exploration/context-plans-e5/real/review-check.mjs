import {createRequire} from 'node:module';import {readFile,writeFile,mkdir} from 'node:fs/promises';import assert from 'node:assert/strict';import {hash} from '../../../agent-core/contracts.mjs';
const {chromium}=createRequire(import.meta.url)(process.argv[2]);
const dir='experiments/ai-rpg-engine/.rpg03-work/e5-plans-real-v1',out=dir+'/page-validation',url='http://127.0.0.1:18919';await mkdir(out,{recursive:true});
const report=JSON.parse(await readFile(dir+'/review.json','utf8')),before=hash(await readFile(dir+'/transport-evidence.json','utf8')),source=hash(await readFile(dir+'/source-after.json','utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[],external=[];
try{
 const context=await browser.newContext({viewport:{width:1440,height:1080}});await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==url){external.push(route.request().url());return route.abort();}return route.continue();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));const response=await page.goto(url);assert.equal(response.status(),200);
 await page.getByRole('heading',{name:'Gemini 连续计划修订'}).waitFor();assert.equal(await page.getByRole('button').count(),0);checks.push('readonly-no-call-or-execution-controls');
 for(const o of report.outputs){assert.ok((await page.textContent('body')).includes(o.input));if(o.parsed?.limit)assert.ok((await page.textContent('body')).includes(o.parsed.limit));const reply=await context.request.get(url+'/evidence/reply-'+o.slot+'.txt');assert.equal(await reply.text(),await readFile(dir+'/reply-'+o.slot+'.txt','utf8'));}checks.push('original-reply-download-byte-content-preserved');
 await page.getByText('Gemini 原始回复（未修补）',{exact:true}).first().click();assert.equal(await page.locator('details[open]').count(),1);checks.push('expand-raw-reply');
 if(report.failure){assert.ok((await page.textContent('body')).includes('第2次未派发'));assert.ok((await page.textContent('body')).includes('未重试'));checks.push('failure-and-skipped-call-visible');}
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:out+'/desktop.png',fullPage:true});
 await page.reload();assert.equal(await page.locator('details[open]').count(),0);await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:out+'/mobile.png',fullPage:true});checks.push('refresh-desktop-mobile-no-overflow');
 for(const [path,status] of [['/evidence/cleanup.json',200],['/evidence/router.sqlite3',404],['/execute',404]])assert.equal((await context.request.get(url+path)).status(),status);
 assert.equal((await context.request.post(url+'/')).status(),403);assert.equal((await context.request.get(url,{headers:{Origin:'https://example.invalid'}})).status(),403);checks.push('readonly-route-and-origin-boundaries');
 assert.equal(hash(await readFile(dir+'/transport-evidence.json','utf8')),before);assert.equal(hash(await readFile(dir+'/source-after.json','utf8')),source);assert.equal(errors.length,0);assert.equal(external.length,0);checks.push('no-dispatch-source-change-or-external-requests');
 const result={passed:true,checks,errors,external,sourceUnchanged:true,newModelCalls:0};await writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
