import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {ProposalProvider,MODEL} from './provider.mjs';
test('E3 reserves at most two requests and never retries an unknown result',async()=>{
 const root=fileURLToPath(new URL('../../../.rpg03-work/',import.meta.url));await mkdir(root,{recursive:true});const records=await SessionRecords.open(await mkdtemp(join(root,'e3-provider-')),{scope:'e3-budget-test'});
 let posts=0;
 const p=new ProposalProvider({records,url:new URL('http://127.0.0.1:18882'),serviceToken:'x'.repeat(48),mode:'mock',selection:{model:MODEL,available:true,selectionId:'s',selectionRevision:'r',parameters:{temperature:.7,top_p:.8,max_tokens:8192}},
 authorization:{id:'test-e3',maximumCalls:2,maximumUSD:.4,perCallUSD:.2,expiresAt:new Date(Date.now()+60000).toISOString(),pricing:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'frozen-test'}},
 fetcher:async(url,opts)=>{if(opts.method==='POST')posts++;return new Response('{}',{status:404});}});
 try{const request={messages:[{role:'system',content:'test'},{role:'user',content:'test'}],tools:[],tool_choice:'none'};
 assert.equal((await p.generate('e3-clarification-mock-v1','proposal-1',request)).status,'unknown');await p.generate('e3-clarification-mock-v1','proposal-1',request);assert.equal(posts,1);
 await p.generate('e3-clarification-mock-v1','proposal-2',request);assert.equal(posts,2);
 assert.throws(()=>p.generate('e3-clarification-mock-v1','proposal-3',request));assert.equal(posts,2);
 assert.throws(()=>p.generate('e3-clarification-mock-v1','proposal-2',{...request,tool_choice:'auto'}));
 const row=await p.read();assert.equal(row.data.calls.length,2);assert(row.data.reservedUSD<=.4);
 }finally{await records.close();}
});
test('wrong authorization and route parameters reject before dispatch',()=>{
 const base={authorization:{maximumCalls:3,maximumUSD:.4,perCallUSD:.2},selection:{model:MODEL}};
 assert.throws(()=>new ProposalProvider(base));
});
