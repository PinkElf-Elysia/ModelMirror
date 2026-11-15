import {clone,hash,need,keys,bounded} from '../contracts.mjs';
import {prepare} from './rules.mjs';
export function registry(adapter,rule){
 const tool={type:'function',function:{name:'prepare_action',description:'计算已登记行动的预计变化，形成待确认提案；不执行。',parameters:{type:'object',additionalProperties:false,required:['actionId','itemId','quantity'],properties:{actionId:{type:'string',enum:[rule.id]},itemId:{type:'string',minLength:1,maxLength:80},quantity:{type:'integer',minimum:1,maximum:Number.MAX_SAFE_INTEGER}}}}};
 const entries=[
 {name:'query_local_state',version:'1',effect:'read',maxCalls:1,maxBytes:65536,tool:clone(adapter.query.tool),validate:adapter.query.validate,run:(r,a)=>adapter.query.execute(clone(r.state),r.stateRevision,a)},
 {name:'prepare_action',version:'1',effect:'proposal',maxCalls:1,maxBytes:65536,tool,
 validate:a=>{keys(a,['actionId','itemId','quantity']);need(a.actionId===rule.id&&typeof a.itemId==='string'&&a.itemId.length<=80&&Number.isSafeInteger(a.quantity)&&a.quantity>0,'ACTION_ARGUMENTS');},run:(r,a)=>prepare(r,rule,a)}];
  const result=createRegistry(entries);
  // Preserve the legacy declaration binding; new assembly uses the expanded hash.
  result.hash=hash(entries.map(({tool,version,effect})=>({tool,version,effect})));return result;
}
// Trusted host registration only. Descriptors are not executable plugin files.
export function createRegistry(entries){
 need(Array.isArray(entries)&&entries.length>0&&entries.length<=16,'TOOL_REGISTRY');
 const names=new Set();
 for(const e of entries){
  need(e&&/^[a-z][a-z0-9_]{0,63}$/.test(e.name)&&!names.has(e.name)&&e.tool?.function?.name===e.name,'TOOL_REGISTRY');names.add(e.name);
  need(['read','compute','proposal'].includes(e.effect)&&typeof e.version==='string'&&e.version.length>0&&typeof e.validate==='function'&&typeof e.run==='function','TOOL_REGISTRY');
  need(Number.isSafeInteger(e.maxCalls)&&e.maxCalls>0&&e.maxCalls<=3&&Number.isSafeInteger(e.maxBytes)&&e.maxBytes>0&&e.maxBytes<=65536,'TOOL_REGISTRY');
 }
 entries=entries.map(e=>({...e,tool:clone(e.tool)}));
 return {entries,hash:hash(entries.map(({tool,version,effect,maxCalls,maxBytes})=>({tool,version,effect,maxCalls,maxBytes}))),
 tools(allowed=entries.map(e=>e.name)){return entries.filter(e=>allowed.includes(e.name)).map(e=>clone(e.tool));},
  execute(name,args,r,{allowed,counts={}}){const e=entries.find(e=>e.name===name);need(e&&allowed.includes(name),'TOOL_NOT_ALLOWED');need((counts[name]||0)<e.maxCalls,'TOOL_LIMIT');e.validate(args);const output=e.run(clone(r),clone(args));need(!output?.then,'TOOL_SYNC_ONLY');bounded(output,{bytes:e.maxBytes,depth:24});return {effect:e.effect,output};}};
}
