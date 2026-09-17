import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
export const hash = value => createHash('sha256').update(value).digest('hex');
const read = name => JSON.parse(readFileSync(new URL(`../resources/${name}`,import.meta.url),'utf8').replace(/^\uFEFF/,''));
export function loadFrozen(){
  const prompts=read('prompts.json'), manifest=read('MANIFEST.json');
  for(const key of Object.keys(manifest.prompts)) if(hash(prompts[key])!==manifest.prompts[key].sha256) throw Error(`Frozen text changed: ${key}`);
  return {prompts,manifest};
}
export const defaults=Object.freeze({temperature:0.7,top_p:0.8,top_k:0,presence_penalty:0,frequency_penalty:0,max_tokens:8192,context:8192,thinking_budget:0});
export function validateParameters(input={}){
  const p={...defaults,...input};
  const ranges={temperature:[0,2],top_p:[0,1],top_k:[0,1000],presence_penalty:[-2,2],frequency_penalty:[-2,2],max_tokens:[1,131072],context:[1,1048576],thinking_budget:[0,131072]};
  for(const key of Object.keys(p))if(!ranges[key]||!Number.isFinite(p[key])||p[key]<ranges[key][0]||p[key]>ranges[key][1])throw Error('Invalid parameter '+key);
  for(const key of ['top_k','max_tokens','context','thinking_budget'])if(!Number.isInteger(p[key]))throw Error('Integer parameter required');
  return p;
}
// No silent omission or substitution of provider parameters.
export function providerParameters(params,supported){
  const p=validateParameters(params);const result={},unsupported=[];
  for(const [key,value] of Object.entries(p)){
    if(key==='context')continue; // UI preference only: never a truncation algorithm.
    if(supported.includes(key))result[key]=value;else unsupported.push(key);
  }
  return {parameters:result,unsupported,contextPolicy:'retain-all; capacity requires explicit provider admission'};
}
const {prompts}=loadFrozen();
// User-authorized single-sentence deletion; the author originals and frozen bundle remain intact.
export const removedWorldDeclaration='【开启深层世界】当前世界为深层世界';
if(prompts.system.split(removedWorldDeclaration).length!==2)throw Error('Authorized deletion target drift');
export const activeSystem=prompts.system.replace(removedWorldDeclaration,'');
export function worldbookRules(){
  const sections=prompts.worldbookSource.split(/(?=【(?:警告|世界类型强制校验))/).filter(Boolean);
  const specs=[
    {id:'world-drift',world:'里世界',terms:['修仙','仙侠','玄幻']},
    {id:'foreign-elements',world:'里世界',terms:['查克拉','魔法','MP','HP','妖怪(日式)','精灵','矮人','吸血鬼','圣光','元素魔法']},
    {id:'realm-drift',world:'里世界',terms:['练气','金丹','元婴','分神','合体','大乘','渡劫','真仙','金仙','大罗']},
    {id:'world-consistency',pending:'正文完全日常/无超自然暗示是语义条件；缺少作者触发配置，不自动推断'},
    {id:'surface-leak',world:'表世界',terms:['精怪','邪祟','符箓','术法','修行者','阴眼','仙家附体','鬼影','阴风']},
    {id:'world-drift-duplicate',pending:'原文重复第 1 块；保留原件，不重复注入'}
  ];
  return sections.map((text,i)=>({...specs[i],text,sha256:hash(text),position:'after-suffix',scope:'last-assistant-body',enabled:false,pending:specs[i]?.pending||'候选词匹配已实现，但扫描范围、大小写、世界条件和触发时点未由作者元数据确认；默认关闭'}));
}
// Explicitly enabled rules can be exercised offline. Production defaults remain off until trigger metadata is confirmed.
export function matchWorldbook({body='',world,enabledIds=[]},rules=worldbookRules()){
  if(!Array.isArray(enabledIds)||enabledIds.some(id=>!rules.some(r=>r.id===id&&r.terms)))throw Error('Unknown/incomplete worldbook rule');
  const seen=new Set();
  return rules.filter(r=>enabledIds.includes(r.id)&&r.world===world&&r.terms.some(term=>body.includes(term))).filter(r=>{if(seen.has(r.sha256))return false;seen.add(r.sha256);return true;});
}
export function assemble({characterText,input,history=[],world='表世界',enabledRuleIds=[]}){
  if(typeof characterText!=='string'||!characterText.trim()||typeof input!=='string'||!input.trim())throw Error('Character and input required');
  if(!Array.isArray(history)||history.some((m,i)=>m.role!==(i%2?'assistant':'user')||typeof m.content!=='string')||history.length%2)throw Error('Only complete ordered history can continue');
  const triggered=matchWorldbook({world,body:history.at(-1)?.content||'',enabledIds:enabledRuleIds});
  const current=history.length ? prompts.prefix+'\n'+input+'\n'+prompts.suffix+triggered.map(r=>'\n'+r.text).join('') : characterText+'\n\n'+input;
  return {messages:[{role:'system',content:activeSystem},...history.map(m=>({role:m.role,content:m.content})),{role:'user',content:current}],current,triggered:triggered.map(r=>({id:r.id,sha256:r.sha256})),sourceHash:hash(activeSystem)};
}
