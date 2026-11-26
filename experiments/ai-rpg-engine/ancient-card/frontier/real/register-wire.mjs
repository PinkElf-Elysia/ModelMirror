import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {decodeSSE,validateRequest} from '../../../agent-core/actions/transport.mjs';
import {assembleWorldbook} from '../../worldbook.mjs';
import {storySystem,adaptDynastySystem} from '../story-prompt.mjs';
import {assemble} from '../../assembly.mjs';
import {registerTools,registerPair,storyMapRead} from '../place-register.mjs';
import {inputs} from './register-inputs.mjs';
let raw='';for await(const s of process.stdin)raw+=s;
const x=JSON.parse(raw);
if(x.kind==='request'){
  const p=x.request;
  const tools=x.story?[storyMapRead,...registerTools.slice(1)]:registerTools;
  validateRequest(p,tools,{maxTools:3});
  const prefix=x.ordinary?adaptDynastySystem(assembleWorldbook({opening:'【帝王】崇祯帝朱由检',input:p.messages.findLast(m=>m.role==='user').content}).system):x.story?storySystem:assemble({input:'x'})[0].content;
  assert.ok(p.messages[0].content.startsWith(prefix));
  if(x.opening){
    const frozen=JSON.parse(await readFile(new URL('../../../.rpg03-work/ordinary-opening-gemini/browser-request.json',import.meta.url),'utf8'));
    for(const key of ['messages','tools','tool_choice'])assert.deepEqual(p[key],frozen[key]);
    assert.deepEqual(p.messages.map(m=>m.role),['system','user']);
    assert.deepEqual(p.tools,[]);assert.equal(p.tool_choice,'none');
  }else if(x.ningyuan){
    assert.equal(p.messages.findLast(m=>m.role==='user').content,'刚才说宁远欠饷，哪一份文书送到了京师？');
    assert.ok(p.messages.some(m=>m.role==='assistant'&&m.content?.includes('宁远十三营军士缺饷已达四月')));
    assert.equal(p.messages.at(-1).role,'user');assert.deepEqual(p.tools,tools.slice(0,3));
  }else if(x.story){
    const user=p.messages.findLast(m=>m.role==='user').content;
    assert.ok(['接着写朝议。',x.ordinary?'东番现在各归谁？':'翻开青册。东番如今各地归谁，朕凭什么这样记？'].includes(user));
    if(!x.ordinary)assert.ok(p.messages[0].content.includes('以御前朝议口吻续写'));
    if(p.messages.at(-1).role==='tool'){
      assert.deepEqual(p.tools,[]);assert.equal(p.messages.at(-2).tool_calls[0].function.name,'query_map');
    }else assert.deepEqual(p.tools,tools.slice(0,3));
  }else{
  const i=inputs.indexOf(p.messages.findLast(m=>m.role==='user').content);assert.ok(i>=0);
  const last=p.messages.at(-1);
  if(last.role==='tool'){
    assert.equal(p.messages.at(-2).tool_calls[0].function.name,'query_map');
    const data=JSON.parse(last.content);assert.equal(data.kind,'current_register');
    assert.ok(i===1||i===2);
    assert.deepEqual(p.tools,i===1?[registerTools[3]]:[]);
  }else assert.deepEqual(p.tools,registerTools.slice(0,3));
  }
  console.log('{}');
}else{
  assert.equal(x.kind,'sse');
  const r=decodeSSE(x.sse,registerTools,{allowedPair:registerPair});
  let actual=null,usage=null;
  for(const l of x.sse.split('\n').filter(l=>l.startsWith('data:')&&!l.includes('[DONE]'))){
    const f=JSON.parse(l.slice(5));if(f.model){assert.equal(f.model,x.model);actual=f.model;}
    if(f.usage)usage=Object.fromEntries(Object.entries(f.usage).filter(([k])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)));
  }
  console.log(JSON.stringify({actual,usage,message:r.message,finishReason:r.finishReason,raw:r.message.content??''}));
}
