import {createRequire} from 'node:module';import {readFile,writeFile,readdir} from 'node:fs/promises';import {resolve,join} from 'node:path';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),{chromium}=require('C:/tmp/mm-r3a-pw/node_modules/playwright');
const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e6-suggestions-real-v1'),base='http://127.0.0.1:18937';
const browser=await chromium.launch({channel:'msedge',headless:true});const checks={},errors=[],external=[];
try{
 const page=await browser.newPage({viewport:{width:1440,height:1040}});page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(base))external.push(r.url());});
 await page.goto(base);await page.waitForSelector('form');
 assert.equal(await page.locator('form').count(),3);checks.actualAlternatives=true;
 const question=await page.locator('section').nth(1).textContent();assert.match(question,/周禾.*另有其人/);assert.equal(await page.locator('section').nth(1).locator('form').count(),0);checks.clarificationNoPrefill=true;
 const form=page.locator('form').first(),edited='我选择先口头表达谢意，暂时保留丹药；先确认能否联系到周禾。';
 await form.locator('textarea').fill(edited);await form.locator('input[name=actor]').evaluate(el=>{el.value='automated-browser-check';});
 await form.locator('button').click();await page.waitForURL('**/#choices');assert.match(await page.locator('#choices').textContent(),/先确认能否联系到周禾/);checks.saveEditedIntent=true;
 await page.reload();assert.match(await page.locator('#choices').textContent(),/自动浏览器验收样例/);checks.reloadPreservesSource=true;
 const names=await readdir(join(dir,'choices'));const records=await Promise.all(names.map(async n=>JSON.parse(await readFile(join(dir,'choices',n),'utf8'))));
 const c=records.find(c=>c.playerText===edited&&c.actor==='automated-browser-check');assert(c);assert.equal(c.executionStatus,'not_executed');assert.equal(c.modelCalls,0);assert.deepEqual(c.sourceBefore,c.sourceAfter);assert.notEqual(c.originalSuggestion.text,c.playerText);checks.choiceSeparateAndNoExecution=true;
 const deny=await page.request.post(base+'/execute',{data:{}});assert.equal(deny.status(),405);checks.noExecuteEndpoint=true;
 await page.screenshot({path:join(dir,'review-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.reload();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(dir,'review-mobile.png'),fullPage:true});checks.mobileNoOverflow=true;
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);checks.noBrowserErrorsOrExternalRequests=true;
 const before=JSON.parse(await readFile(join(dir,'source-before.json'),'utf8'));assert.equal(before.frame.facts.resources.find(x=>x.id==='medicine').quantity,5);checks.medicineFive=true;
 await writeFile(join(dir,'browser-check.json'),JSON.stringify({checks,errors,external,automatedSelectionId:c.id,modelCallsAdded:0},null,2));console.log(JSON.stringify(checks));
}finally{await browser.close();}
