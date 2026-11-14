// Replays already saved replies only. Never opens a provider bridge or credential store.
import {createRequire} from 'node:module';import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';import {readFile,mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {archiveSource,ARCHIVE_HASH} from './source.mjs';import {createContextBuilder} from './context.mjs';
import {validateProposal} from './model.mjs';import {startServer} from './server.mjs';
import {hash} from '../../agent-core/contracts.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/',import.meta.url));
const historical=join(root,'e2-proposal-real-v1'),sourceFile=join(root,'e2-source/session-1.json');
const source=archiveSource(sourceFile),world=await source.read(),build=await createContextBuilder();
const sourceBefore=await readFile(sourceFile,'utf8');assert.equal(hash(sourceBefore),ARCHIVE_HASH);
const beforeTransport=await readFile(join(historical,'transport-evidence.json'),'utf8');
const out=await mkdtemp(join(root,'e2-ambiguity-offline-')),results=[];
for(const slot of [1,2]){
 const raw=await readFile(join(historical,'reply-'+slot+'.txt'),'utf8');
 const old=JSON.parse(await readFile(join(historical,'proposal-'+slot+'.json'),'utf8'));
 const request=build(world,{text:old.input,selection:[]});
 const proposal=validateProposal(world,request,raw,{id:'replay-'+slot,revision:1});
 if(slot===1){assert.equal(proposal.requirements[0].quantity,2);assert.equal(proposal.expected[0].after,1);}
 else{assert.equal(proposal.status,'clarification');for(const k of ['refs','requirements','expected','steps','basis'])assert.deepEqual(proposal[k],[]);}
 results.push({slot,kind:'offline-replay-not-new-generation',rawHash:hash(raw),oldProposalHash:old.proposal.hash,proposal,requestHash:request.contextTrace.messagesHash});
}
const app=await startServer({port:0,directory:join(out,'drafts'),source});let browser;
const checks=[],errors=[];
try{
 const req=createRequire(resolve(process.argv[2],'package.json'));const {chromium}=req('./index.js');
 browser=await chromium.launch({channel:'msedge',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(app.url).origin?route.continue():route.abort());
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const originalRaw=await readFile(join(historical,'reply-2.txt'),'utf8');
 app.provider.generate=async()=>{app.provider.calls++;return {status:'completed',raw:originalRaw,provider:'offline-saved-reply',realCalls:0};};
 await page.goto(app.url);await page.locator('#profile').filter({hasText:'林清'}).waitFor();
 await page.locator('#intent').fill('帮我给那人一些丹药，先想想怎么安排，暂时不要动。');
 await page.getByRole('button',{name:'生成模拟提案',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.state-label')?.textContent==='需要澄清');
 assert(await page.locator('#confirm').isDisabled());assert(await page.locator('#export').isDisabled());
 assert.equal(await page.locator('.changes').count(),0);assert.equal(await page.locator('#quantity').count(),0);
 assert(!/布囊/.test(await page.locator('#proposal').innerText()));
 const v=await app.store.view();
 assert.equal(v.draft.receipt.raw,originalRaw);assert.deepEqual(v.draft.current.requirements,[]);
 await assert.rejects(app.store.action('confirm',v.recordHash,v.draft.current.hash),{code:'NOT_READY'});
 await assert.rejects(app.store.export(),{code:'NOT_CONFIRMED'});
 checks.push('actual failed reply becomes clarification; no guessed refs/quantity/steps; raw preserved; cannot confirm or export');
 await page.screenshot({path:join(out,'desktop.png'),fullPage:true});
 await page.reload();await page.waitForFunction(()=>document.querySelector('.state-label')?.textContent==='需要澄清');
 assert.equal(app.provider.calls,1);checks.push('refresh preserves clarification without regenerating');
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:join(out,'mobile.png'),fullPage:true});checks.push('desktop/mobile render without overflow');
 assert.deepEqual(errors,[]);
 assert.equal(await readFile(sourceFile,'utf8'),sourceBefore);
 assert.equal(await readFile(join(historical,'transport-evidence.json'),'utf8'),beforeTransport);
 for(const r of results)assert.equal(hash(await readFile(join(historical,'reply-'+r.slot+'.txt'),'utf8')),r.rawHash);
 await writeFile(join(out,'result.json'),JSON.stringify({realCalls:0,mode:'offline-saved-reply',sourceHash:ARCHIVE_HASH,results,checks,errors,sourceUnchanged:true,historicalEvidenceUnchanged:true},null,2));
 console.log(JSON.stringify({output:out,realCalls:0,checks}));
}finally{await browser?.close();await app.close();}
