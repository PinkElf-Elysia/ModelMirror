import {readFileSync} from 'node:fs';
import {clone,need,validateState,ITEM_KINDS,ABILITY_KINDS} from '../definition.mjs';
export const TOOL=JSON.parse(readFileSync(new URL('./query-tool.json',import.meta.url),'utf8'));
export function validateQueries(args){
 need(args&&typeof args==='object'&&!Array.isArray(args)&&Object.keys(args).join()==='queries','TOOL_ARGUMENTS');
 need(Array.isArray(args.queries)&&args.queries.length>=1&&args.queries.length<=3,'TOOL_QUERY_COUNT');
 for(const q of args.queries){
  need(q&&typeof q==='object'&&!Array.isArray(q)&&Object.keys(q).every(k=>['domain','id','name','kind','limit'].includes(k)),'TOOL_QUERY_FIELDS');
  need(TOOL.function.parameters.properties.queries.items.properties.domain.enum.includes(q.domain),'TOOL_DOMAIN');
  for(const [key,max] of [['id',80],['name',100],['kind',30]])if(q[key]!==undefined)need(typeof q[key]==='string'&&q[key].trim()&&q[key].length<=max,'TOOL_FILTER');
  if(q.limit!==undefined)need(Number.isInteger(q.limit)&&q.limit>=1&&q.limit<=50,'TOOL_LIMIT');
  if(q.kind!==undefined)need((q.domain==='items'?ITEM_KINDS:q.domain==='abilities'?ABILITY_KINDS:[]).includes(q.kind),'TOOL_KIND');
  if(['balances','equipment','traits'].includes(q.domain))need(q.name===undefined&&q.kind===undefined,'TOOL_FILTER');
 }
 return args;
}
export function queryLocalState(state,stateRevision,args){
 validateState(state);validateQueries(args);
 need(Number.isSafeInteger(stateRevision)&&stateRevision>=0,'TOOL_STATE_VERSION');
 return {stateRevision,scope:'current-session-registered-state',notice:'完整仅指选定的已登记范围；未登记事实未知。不表示物品容器归属。',results:args.queries.map(q=>{
  const collection=Array.isArray(state[q.domain]);
  let values=collection?clone(state[q.domain]):Object.entries(state[q.domain]).map(([id,value])=>({id,value}));
  if(q.id)values=values.filter(e=>e.id===q.id);
  if(q.name)values=values.filter(e=>e.name?.normalize('NFKC').toLowerCase().includes(q.name.normalize('NFKC').toLowerCase()));
  if(q.kind)values=values.filter(e=>e.kind===q.kind);
  const total=values.length,entries=values.slice(0,q.limit??50),unset=[];
  for(const e of entries)for(const [key,value] of Object.entries(e))if(value===null)unset.push({id:e.id,field:key});
  return {query:clone(q),coverage:total>entries.length?'partial':'complete-registered-scope',status:total===0?'no-match':unset.length===entries.length&&entries.every(e=>'value'in e)?'unset':'found',total,entries,unset};
 })};
}
