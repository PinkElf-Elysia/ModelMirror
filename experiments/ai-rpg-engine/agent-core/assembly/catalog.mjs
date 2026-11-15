import {actionAdapters} from '../actions/adapters.mjs';
import {createRegistry} from '../actions/registry.mjs';
import {clone,hash,id,need,validateAdapter} from '../contracts.mjs';
import {policy,MODEL_A,MODEL_B} from '../tasks/policy.mjs';
import {countRegistered} from './capabilities.mjs';
import {worldOwner,observationOwner} from './owners.mjs';
export async function exampleCatalog(){
 const worlds=(await actionAdapters()).map(a=>{
  const project=a.id==='cultivation'?s=>s.items.map(x=>({...x,category:x.kind})):s=>s.supplies.map(x=>({...x,category:'物资'}));
  const entries=[...a.tools.entries,countRegistered(project)],assemble=a.assembleTask;
  a.tools=createRegistry(entries);a.continuationTools=entries.filter(e=>e.effect==='proposal').map(e=>e.name);
  a.binding={...a.binding,adapterVersion:'8',effectiveContextHash:hash({prior:a.binding.effectiveContextHash,tools:a.tools.hash,contract:'assembly/8'})};
  a.assembleTask=async(...args)=>{const c=await assemble(...args);
   c.messages[0].content+='\n【R8 登记能力】需要统计登记类别数量时，可调用 count_registered。只以工具覆盖范围说明结果，未知数量不能猜成零。';
   const enabled=args[0].runtimeCapabilities??a.tools.entries.map(e=>e.name);
   c.tools=c.tools.filter(t=>enabled.includes(t.function.name));c.tool_choice=c.tools.length?'auto':'none';
   c.messages[0].content+='\n本次实际开放工具：'+c.tools.map(t=>t.function.name).join('、')+'。未开放的能力不能调用，也不能凭空推断其结果。';
   c.binding=clone(a.binding);c.hash=hash({messages:c.messages,tools:c.tools,purpose:c.purpose});return c;};
  return {adapter:a,project,category:a.id==='cultivation'?'丹药':'物资'};
 });
 return {worlds,owners:[worldOwner('world-dialogue','turn'),worldOwner('world-narration','narrate'),observationOwner()],
  entrypoints:{dialogue:'world-dialogue',narration:'world-narration'},policies:{same:{model:MODEL_A},swift:{model:MODEL_B},backup:{model:MODEL_A,backup:MODEL_B}}};
}
export function compileCatalog(c){
 need(c&&Array.isArray(c.worlds)&&c.worlds.length>0&&Array.isArray(c.owners)&&c.owners.length>0,'ASSEMBLY_CATALOG');
 const worlds=new Map(),owners=new Map();
 for(const w of c.worlds){validateAdapter(w.adapter);need(!worlds.has(w.adapter.id)&&typeof w.project==='function','ASSEMBLY_DUPLICATE');worlds.set(w.adapter.id,w);}
 for(const o of c.owners){need(id(o.id)&&!owners.has(o.id)&&typeof o.version==='string'&&typeof o.execute==='function','ASSEMBLY_OWNER');
  need(o.authority==='world'&&o.adoption==='host'||o.authority==='observation'&&o.adoption==='explicit'&&typeof o.project==='function','ASSEMBLY_OWNER_AUTHORITY');owners.set(o.id,o);}
 const policies=Object.fromEntries(Object.entries(c.policies).map(([k,v])=>{need(id(k),'ASSEMBLY_POLICY');policy(v);return [k,clone(v)];}));need(Object.keys(policies).length>0,'ASSEMBLY_POLICY');
 need(c.entrypoints&&['dialogue','narration'].every(k=>owners.get(c.entrypoints[k])?.authority==='world'),'ASSEMBLY_ENTRYPOINT');
 const descriptor={version:'assembly/8',worlds:[...worlds.values()].map(w=>({id:w.adapter.id,binding:w.adapter.binding,tools:w.adapter.tools.hash,continuation:w.adapter.continuationTools})),
  owners:[...owners.values()].map(({id,version,authority,adoption})=>({id,version,authority,adoption})),policies,entrypoints:clone(c.entrypoints)};
 return {worlds,owners,policies,entrypoints:clone(c.entrypoints),descriptor,hash:hash(descriptor)};
}
