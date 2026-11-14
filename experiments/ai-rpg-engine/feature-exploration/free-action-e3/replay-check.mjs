import {readFile,writeFile,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';import assert from 'node:assert/strict';
import {archiveSource,ARCHIVE_HASH} from '../free-action-e2/source.mjs';import {createContextBuilder} from '../free-action-e2/context.mjs';import {validateProposal} from '../free-action-e2/model.mjs';
import {createBuilder} from './context.mjs';import {MockProvider,parseReply} from './provider.mjs';import {proposal} from './intent.mjs';import {hash} from '../../agent-core/contracts.mjs';
const [firstFile,retestFile]=process.argv.slice(2);if(!firstFile||!retestFile)throw Error('Pass two archived E2 raw replies; never dispatch.');
const world=await archiveSource(fileURLToPath(new URL('../../.rpg03-work/e3-source/session-1.json',import.meta.url))).read(),initial=hash(world);
const raw1=await readFile(resolve(firstFile),'utf8'),raw2=await readFile(resolve(retestFile),'utf8'),input='帮我给那人一些丹药，先想想怎么安排，暂时不要动。';
const oldBuild=await createContextBuilder(),oldRequest=oldBuild(world,{text:input,selection:[]});
const oldResults=[raw1,raw2].map((raw,i)=>validateProposal(world,oldRequest,raw,{id:'replay-'+i,revision:1}));
assert(oldResults.every(p=>p.status==='clarification'&&p.refs.length===0&&p.requirements.length===0));
for(const raw of [raw1,raw2])assert.throws(()=>parseReply(raw),{code:'REPLY_FIELDS'});
// Explicitly constructed adversarial patch; not an actual E3 model response.
assert.throws(()=>proposal(world,null,[{field:'target',value:'zhou',quote:'那人'}],{text:input,id:'replay',turnId:'replay'}),{code:'INTENT_REFERENCE_NOT_EXPLICIT'});
assert.throws(()=>proposal(world,null,[{field:'quantity',value:1,quote:'一些'}],{text:input,id:'replay',turnId:'replay'}),{code:'INTENT_QUANTITY_NOT_EXPLICIT'});
const builder=await createBuilder(),provider=new MockProvider(),samples=['给那人一些丹药','周禾','养息丹，两份；如果他不收就算了','改成一份，先问他需不需要'];let current=null;const measurements=[];
for(const [i,text] of samples.entries()){
 const req=builder(world,current,text),result=await provider.generate(req),delta=parseReply(result.raw);
 current=proposal(world,current,delta.changes,{...delta,id:'measure',turnId:'t-'+i,text});
 measurements.push({input:text,systemCharacters:req.messages[0].content.length,systemBytes:Buffer.byteLength(req.messages[0].content),userCharacters:req.messages[1].content.length,userBytes:Buffer.byteLength(req.messages[1].content),outputCharacters:result.raw.length,outputBytes:Buffer.byteLength(result.raw),tokens:null,costUSD:null,provider:'offline-simulated'});
}
assert.equal(current.intent.quantity.value,1);assert.equal(hash(world),initial);
const root=fileURLToPath(new URL('../../.rpg03-work/e3-validation/',import.meta.url));await mkdir(root,{recursive:true});
const report={kind:'offline-replay-and-measurement',sourceHash:ARCHIVE_HASH,e2RawHashes:[hash(raw1),hash(raw2)],checks:['E2 historical replies retain clarification','E2 format cannot silently enter E3','adversarial target and quantity rejected','four-turn simulated continuity','world unchanged'],measurements,realCalls:0,limitations:['Schema rejection is not proof of real-model interpretation quality.','Finite quantity recognition and named-object grounding only.','No tokenizer; no token or cost reduction claim.']};
await writeFile(root+'replay.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({checks:report.checks,evidence:root+'replay.json',realCalls:0},null,2));
