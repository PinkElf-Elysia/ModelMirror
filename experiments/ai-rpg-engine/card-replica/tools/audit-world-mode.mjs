import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadFrozen,hash,defaults,assemble} from '../lib/assembly.mjs';
import {createHost} from '../server.mjs';
import {buildComparison} from '../lib/openrouter-config.mjs';
const base=fileURLToPath(new URL('../',import.meta.url)),work=join(base,'.local/world-mode-audit');
await mkdir(work,{recursive:true});
const {prompts}=loadFrozen();
const regex=/表世界|里世界|世界类型|世界观|深层世界|超自然|超能力|神怪|精怪|邪祟|符箓|道行|修行|灵体|术法|灵感|阴眼|仙家|宗门|山门|境界|法术|民俗|恐怖|读心|物理|科学|永久不变|不可逆转/;
const hits=[];
for(const [key,text] of Object.entries(prompts)){
 await writeFile(join(work,key+'.txt'),text);
 text.split('\n').forEach((text,i)=>{if(regex.test(text))hits.push({source:key,line:i+1,text});});
}
await writeFile(join(work,'all-prompt-matches.json'),JSON.stringify(hits,null,2));
await writeFile(join(work,'ALL_MATCHES.md'),'# World-related literal matches\n\nScope: all four frozen texts; literal index plus manually reviewed adjacent sections. This is not a claim of proving every semantic cause.\n\n'+hits.map(x=>'## '+x.source+':'+x.line+'\n\n'+x.text+'\n').join('\n'));
const code=[];
for(const folder of ['src','lib','tools']){
 for(const name of await readdir(join(base,folder))){
  if(!/\.(tsx?|mjs|py)$/.test(name)||name==='audit-world-mode.mjs')continue;
  const text=await readFile(join(base,folder,name),'utf8');
  text.split('\n').forEach((line,i)=>{if(/表世界|里世界|world:|world=|worldbook|深层世界/.test(line))code.push({source:folder+'/'+name,line:i+1,text:line});});
 }
}
await writeFile(join(work,'code-world-matches.json'),JSON.stringify(code,null,2));
const wires=[];
for(const [name,path] of [['GPT','.local/real/dispatches/slot-2/request.json'],['Gemini','.local/gemini-real/dispatches/slot-4/wire-request.json']]){
 const r=JSON.parse(await readFile(join(base,path),'utf8')),u=r.messages.at(-1).content;
 assert.equal(r.messages.length,2);assert.equal(r.messages[0].content,prompts.system);
 assert.ok(!u.includes(prompts.prefix)&&!u.includes(prompts.suffix)&&!u.includes('【开启深层世界】'));
 const result={name,path,roles:r.messages.map(x=>x.role),officialSystemExact:true,systemHash:hash(r.messages[0].content),activationDeclarationInSystem:r.messages[0].content.includes('【开启深层世界】当前世界为深层世界'),firstUserHasActivation:false,firstUserHasSurfaceLabel:u.includes('表世界'),firstUserPrefix:false,firstUserSuffix:false,worldbookAppended:r.messages.some(m=>m.content.includes(prompts.worldbookSource))};
 wires.push(result);
}
const capture=[];
const host=await createHost({port:0,directory:join(work,'host-sessions-'+Date.now()),generate:async({messages,params})=>{capture.push({messages,params});return '离线占位回复'+capture.length;}});
const origin='http://127.0.0.1:'+host.port,post=async(path,body)=>{const r=await fetch(origin+path,{method:'POST',headers:{origin,'content-type':'application/json'},body:JSON.stringify(body)});assert.ok(r.ok);return r.json();};
try{
 const characterText='姓名：装配核对角色（虚构）',s=await post('/api/sessions',{characterText,world:'表世界',params:defaults,mode:'offline'});
 for(let i=0;i<3;i++)await post('/api/sessions/'+s.id+'/send',{input:['开始','继续','再继续'][i],requestId:'audit-'+i,revision:i});
 assert.deepEqual(capture.map(x=>x.messages.length),[2,4,6]);
 assert.equal(capture[0].messages[1].content,characterText+'\n\n开始');
 assert.equal(capture[1].messages.at(-1).content,prompts.prefix+'\n继续\n'+prompts.suffix);
 assert.equal(capture[2].messages.at(-1).content,prompts.prefix+'\n再继续\n'+prompts.suffix);
 assert.deepEqual(capture[2].messages.slice(0,4),[...capture[1].messages.slice(0,3),capture[1].messages[3]]);
 for(const c of capture)assert.equal(c.messages.filter(m=>m.role==='system').length,1);
 const byMessage=capture[2].messages.map(m=>({role:m.role,prefix:m.content.split(prompts.prefix).length-1,suffix:m.content.split(prompts.suffix).length-1}));
 assert.deepEqual(byMessage.map(x=>x.prefix),[0,0,0,1,0,1]);assert.deepEqual(byMessage.map(x=>x.suffix),[0,0,0,1,0,1]);
 await writeFile(join(work,'actual-host-three-turns.json'),JSON.stringify({capture,byMessage,providerCalls:0},null,2));
}catch(e){throw e;}finally{await new Promise(r=>host.server.close(r));}
const old=JSON.parse(await readFile(join(base,'.local/gemini-real/dispatches/slot-4/request.json'),'utf8'));
const character=await readFile(join(base,'.local/comparison-openrouter-20260917/character.txt'),'utf8'),catalog=JSON.parse(await readFile(join(base,'.local/comparison-openrouter-20260917/catalog.json'),'utf8'));
const next=buildComparison(character,catalog);
assert.equal(next.payload.max_tokens,16384);
assert.deepEqual({...next.payload,max_tokens:old.max_tokens},old);
await writeFile(join(work,'next-request-16384.json'),JSON.stringify(next.payload,null,2));
const counts=Object.fromEntries(Object.keys(prompts).map(key=>[key,hits.filter(x=>x.source===key).length]));
const summary={at:new Date().toISOString(),promptMatchCounts:counts,promptMatches:hits.length,codeMatches:code.length,realFirstTurns:wires,actualHostThreeTurns:'passed; messages2/4/6; no rewrapping',worldMetadataAloneChangesModelMessages:JSON.stringify(assemble({characterText:character,input:'开始这一世。',world:'表世界'}).messages)!==JSON.stringify(assemble({characterText:character,input:'开始这一世。',world:'里世界'}).messages),nextRequest:{onlyChangedField:'max_tokens',before:8192,after:16384,hash:hash(JSON.stringify(next.payload)),dispatched:false},providerCalls:0};
await writeFile(join(work,'summary.json'),JSON.stringify(summary,null,2));console.log(JSON.stringify(summary,null,2));
