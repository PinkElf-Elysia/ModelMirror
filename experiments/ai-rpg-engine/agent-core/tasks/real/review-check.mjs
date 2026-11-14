import {createRequire} from 'node:module';import {readFile,writeFile,mkdir} from 'node:fs/promises';import {join,resolve} from 'node:path';import assert from 'node:assert/strict';
const dir=resolve(process.argv[2]||'.');if(!dir.startsWith(resolve('C:/tmp/mm-r7-live','real-')))throw Error('R7_SCOPE');
const require=createRequire('C:/tmp/mm-r3a-pw/node_modules/playwright/package.json'),{chromium}=require('./index.js');const url='http://127.0.0.1:18898/',errors=[],external=[],checks=[];await mkdir(join(dir,'browser'),{recursive:true});
const read=n=>readFile(join(dir,n),'utf8'),before=await read('dispatch-audit.json');const browser=await chromium.launch({channel:'msedge',headless:true});
try{const c=await browser.newContext({viewport:{width:1440,height:1000}});await c.route('**/*',r=>new URL(r.request().url()).origin===new URL(url).origin?r.continue():(external.push(r.request().url()),r.abort()));
 const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));assert.equal((await p.goto(url)).status(),200);checks.push('read-only-page');
 assert.equal(await p.locator('article').count(),3);checks.push('three-saved-replies');await p.locator('details summary').first().click();checks.push('expand-evidence');
 assert.equal((await p.request.get(url+'evidence/stage-1.json')).status(),200);checks.push('raw-receipt-download');
 assert.equal((await p.request.get(url+'evidence/local-auth.bin')).status(),404);assert.equal((await p.request.get(url+'evidence/router/router.sqlite3')).status(),404);checks.push('credentials-not-served');
 assert.equal((await p.request.post(url)).status(),403);checks.push('writes-refused');await p.screenshot({path:join(dir,'browser/desktop.png'),fullPage:true});
 await p.setViewportSize({width:390,height:844});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:join(dir,'browser/mobile.png'),fullPage:true});checks.push('mobile-no-overflow');
 await p.reload();assert.equal(await read('dispatch-audit.json'),before);checks.push('refresh-no-dispatch');assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 await writeFile(join(dir,'browser/result.json'),JSON.stringify({checks,errors,external},null,2));console.log(JSON.stringify({passed:checks.length,errors,external}));
}finally{await browser.close();}
