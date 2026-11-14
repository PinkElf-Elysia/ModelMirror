import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {ProposalProvider,MODEL} from './retest-provider.mjs';
test('gift retest permits only one request, retains unknown and caps budget at $0.20',async()=>{
 const root=fileURLToPath(new URL('../../../.rpg03-work/',import.meta.url));await mkdir(root,{recursive:true});const records=await SessionRecords.open(await mkdtemp(root+'e4-retest-budget-'),{scope:'e4-retest-budget'});
 let posts=0;const options={records,url:new URL('http://127.0.0.1:18901'),serviceToken:'x'.repeat(48),mode:'mock',selection:{model:MODEL,available:true,selectionId:'s',selectionRevision:'r',parameters:{temperature:.7,top_p:.8,max_tokens:8192}},
 authorization:{id:'test-gift',maximumCalls:1,maximumUSD:.2,perCallUSD:.2,expiresAt:new Date(Date.now()+60000).toISOString(),pricing:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'frozen-test'}},
 fetcher:async(url,o)=>{if(o.method==='POST')posts++;return new Response('{}',{status:404});}};
 try{const p=new ProposalProvider(options),request={messages:[{role:'system',content:'test'},{role:'user',content:'test'}],tools:[],tool_choice:'none'};
 assert.equal((await p.generate('e4-gift-retest-mock-v1','gift-1',request)).status,'unknown');await p.generate('e4-gift-retest-mock-v1','gift-1',request);assert.equal(posts,1);
 assert.throws(()=>p.generate('e4-gift-retest-mock-v1','gift-2',request));assert.throws(()=>new ProposalProvider({...options,authorization:{...options.authorization,maximumCalls:2}}));
 assert.equal((await p.read()).data.calls.length,1);assert((await p.read()).data.reservedUSD<=.2);
 }finally{await records.close();}
});
