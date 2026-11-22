// Offline replay of an archived provider response. No provider, key or network is required.
import assert from 'node:assert/strict';
import {randomUUID,createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {readFile,writeFile,mkdtemp,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {decodeSSE} from '../../../agent-core/actions/transport.mjs';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {registerTools,registerView,overview} from '../place-register.mjs';
import {play} from '../session.mjs';

const archive=resolve(process.argv[2]), destination=resolve(process.argv[3]);
const read=async path=>JSON.parse(await readFile(join(archive,path),'utf8'));
const request=await read('request-1.json');
const result=await read('result.json');
const saved=(await read('saves/records/'+request.sessionId+'.json')).data;
assert.equal(saved.turns.length,1);
const s=structuredClone(saved);s.id=randomUUID();
const wire=await readFile(join(archive,'provider-1.sse'),'utf8');
assert.throws(()=>decodeSSE(wire,registerTools),/TRANSPORT_CALL/);
const decode=JSON.parse(execFileSync(process.execPath,[fileURLToPath(new URL('./register-wire.mjs',import.meta.url))],
  {input:JSON.stringify({kind:'sse',sse:wire,model:result.model}),encoding:'utf8'}));
const packet={input:request.messages.at(-1).content,revision:s.revision,requestId:request.requestId};
const controls=()=>registerView(s.world).locations.map(r=>r.control);
const before=controls();
await play(s,packet,async assembled=>{
  for(const key of ['messages','tools','tool_choice'])assert.deepEqual(assembled[key],request[key]);
  return {status:'completed',finishReason:decode.finishReason,message:decode.message,usage:decode.usage};
});
const audit=s.dispatches.at(-1);
assert.deepEqual(audit.toolResults.map(r=>r.tool_call_id),decode.message.tool_calls.map(c=>c.id));
assert.deepEqual(audit.toolResults[0].result.entries.map(e=>e.node),['大员','鸡笼','台湾原住民各社']);
assert.deepEqual(s.world.pending,{node:'大员',kind:'order'});
assert.deepEqual(s.world.facts,[]);assert.deepEqual(controls(),before);
await play(s,packet,()=>assert.fail('重复派发'));
await mkdir(destination,{recursive:true});
const dir=await mkdtemp(join(destination,'replay-'));
const db=await SessionRecords.open(dir,{scope:'ancient-frontier-v1'});
try{await db.put(s.id,null,s);}finally{await db.close();}
const draftId=s.id;
const steps=[];
for(const input of ['签发这份旨意','推进至大员捷报抵京','核对大员交割','确认这份交割',overview]){
  await play(s,{input,revision:s.revision,requestId:randomUUID()});
  steps.push({input,body:s.turns.at(-1).raw,controls:controls()});
  const expected=before.slice();if(steps.length>=4)expected[4]='明';
  assert.deepEqual(controls(),expected);
}
const [order,receipt,event]=s.world.facts;
assert.equal(receipt.source,order.id);assert.equal(event.source,receipt.id);
assert.deepEqual(s.world.facts.map(e=>[e.node,e.kind]),[['大员','order'],['大员','receipt'],['大员','settled']]);
assert.match(steps.at(-1).body,/大员，簿上登记为明/);
await writeFile(join(dir,'evidence.json'),JSON.stringify({mode:'archived Gemini SSE + offline host',newProviderCalls:0,
  wireSHA256:createHash('sha256').update(wire).digest('hex'),draftId,toolResults:audit.toolResults,steps,events:s.world.facts},null,2));
console.log(JSON.stringify({dir,draftId,toolIds:audit.toolResults.map(r=>r.tool_call_id),final:controls()}));
