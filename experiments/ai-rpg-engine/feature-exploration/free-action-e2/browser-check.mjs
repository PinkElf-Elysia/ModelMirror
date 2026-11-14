// Uses only an already installed Playwright and a fresh headless Edge profile.
import {createRequire} from 'node:module';import {resolve,join} from 'node:path';import {fileURLToPath} from 'node:url';
import {mkdir,mkdtemp,writeFile,readFile} from 'node:fs/promises';import assert from 'node:assert/strict';
import {startServer} from './server.mjs';import {archiveSource,ARCHIVE_HASH} from './source.mjs';import {hash} from '../../agent-core/contracts.mjs';
if(!process.argv[2])throw Error('Pass existing playwright package directory.');
const require=createRequire(resolve(process.argv[2],'package.json')),pkg=require('./package.json');assert.equal(pkg.name,'playwright');
const {chromium}=require('./index.js'),root=fileURLToPath(new URL('../../.rpg03-work/e2-validation/',import.meta.url));
await mkdir(root,{recursive:true});const output=await mkdtemp(join(root,'browser-'));
const file=fileURLToPath(new URL('../../.rpg03-work/e2-source/session-1.json',import.meta.url)),source=archiveSource(file);
const before=await readFile(file,'utf8'),world=await source.read();assert.equal(hash(before),ARCHIVE_HASH);
const app=await startServer({port:0,directory:join(output,'drafts'),source});
const checks=[],errors=[];let browser;
try{
 browser=await chromium.launch({channel:'msedge',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(app.url).origin?route.continue():route.abort());
 const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 const click=name=>page.getByRole('button',{name,exact:true}).click();
 const waitStatus=label=>page.waitForFunction(x=>document.querySelector('#proposal .state-label')?.textContent===x,label);
 const check=async(name,fn)=>{await fn();checks.push(name);console.log('PASS '+name);};
 await page.goto(app.url);await page.locator('#objects .object').first().waitFor();
 await check('saved real session is identified; actual count 3 and conditional count 1',async()=>{
  assert.match(await page.locator('#source-kind').textContent(),/只读副本/);
  await click('生成模拟提案');await waitStatus('待核对');
  assert.equal(await page.locator('.changes tbody tr').textContent(),'养息丹321');
  assert.match(await page.locator('#objects').innerText(),/3 份/);
  await page.screenshot({path:join(output,'desktop-review.png'),fullPage:true});
 });
 await check('confirmation export refresh never executes or regenerates',async()=>{
  await click('确认方案，暂不执行');await waitStatus('已确认，待执行');
  const download=page.waitForEvent('download');await click('导出交接');await (await download).saveAs(join(output,'handoff.json'));
  const h=JSON.parse(await readFile(join(output,'handoff.json'),'utf8'));assert.equal(h.executionStatus,'not_executed');assert.equal(h.proposal.expected[0].after,1);
  await page.reload();await waitStatus('已确认，待执行');assert.equal(app.provider.calls,1);
  await page.screenshot({path:join(output,'desktop-confirmed.png'),fullPage:true});
 });
 await check('quantity edit uses local calculation and invalidates prior confirmation',async()=>{
  await page.locator('#quantity').fill('1');await click('本地重算');await waitStatus('待核对');
  assert.equal(await page.locator('.changes tbody tr').textContent(),'养息丹312');assert.equal(app.provider.calls,1);
  assert(await page.locator('#export').isDisabled());await page.locator('#quantity').fill('4');await click('本地重算');await waitStatus('条件不足');assert(await page.locator('#confirm').isDisabled());
 });
 await check('ambiguous and unknown actions remain questions; compound action has no item mutation',async()=>{
  await click('澄清对象');await click('生成模拟提案');await waitStatus('需要澄清');assert.match(await page.locator('#proposal').innerText(),/指谁/);
  await click('未知能力');await click('生成模拟提案');await waitStatus('需要澄清');
  await click('帮忙与交谈');await click('生成模拟提案');await waitStatus('待核对');assert.match(await page.locator('#proposal').innerText(),/不使用丹药/);
  assert.equal(await page.locator('.changes').count(),0);
 });
 await check('editing an input disables confirmation until regenerated',async()=>{
  await page.locator('#intent').fill('另外一个要求');assert(await page.locator('#confirm').isDisabled());
  await click('拟赠丹药');await click('生成模拟提案');await waitStatus('待核对');
 });
 await check('mobile remains usable without horizontal overflow',async()=>{
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await click('确认方案，暂不执行');await waitStatus('已确认，待执行');await click('撤销确认');await waitStatus('已撤销确认');
  await page.screenshot({path:join(output,'mobile.png'),fullPage:true});
 });
 assert.deepEqual(errors,[]);assert.equal(await readFile(file,'utf8'),before);assert.deepEqual(await source.read(),world);
 const result={status:'passed',checks,errors,sourceHash:ARCHIVE_HASH,sourceSession:world.session,sourceKind:world.provenance.kind,realCalls:0,simulatedCalls:app.provider.calls,playwright:pkg.version,output};
 await writeFile(join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}catch(e){await writeFile(join(output,'failure.txt'),e.stack);throw e;}finally{await browser?.close();await app.close();}
