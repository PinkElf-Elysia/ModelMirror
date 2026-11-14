import {clone,hash,need} from '../contracts.mjs';
export const VERSION='rpg-context/4';
const natures=new Set(['confirmed','character-claim','hypothesis','unset','pending-proposal','instruction','reference']);
// Trusted adapter prepares material; this layer has no card names, state paths or retrieval logic.
export function assemblePrepared(prepared){
 const {materials,scope,selection,messages,replacePrefix}=prepared;
 need(scope&&typeof scope.session==='string'&&Number.isSafeInteger(scope.stateRevision),'CONTEXT_SCOPE');
 need(Array.isArray(materials)&&materials.length>0&&Array.isArray(messages)&&messages[0]?.role==='system','CONTEXT_PREPARED');
 const byId=new Map();
 for(const m of materials){
  need(typeof m.id==='string'&&!byId.has(m.id)&&typeof m.text==='string'&&m.source&&natures.has(m.nature),'CONTEXT_MATERIAL');
  need(m.scope.session===scope.session&&m.scope.stateRevision===scope.stateRevision,'CONTEXT_MATERIAL_SCOPE');byId.set(m.id,m);
 }
 const reasons=new Map(),fallbackReasons=[...(prepared.issues||[])],bindings=prepared.bindings||{};
 const include=(id,reason,stack=[])=>{
  const m=byId.get(id);if(!m){fallbackReasons.push('missing-material:'+id);return;}
  if(stack.includes(id)){fallbackReasons.push('cyclic-dependency:'+id);return;}
  if(reasons.has(id))return;reasons.set(id,reason);
  for(const dep of m.dependencies||[])include(dep,'dependency:'+id,[...stack,id]);
 };
 for(const m of materials)if(m.required)include(m.id,'required');
 if(selection?.mode==='scoped'){
  if(!Array.isArray(selection.refs)||!selection.refs.length)fallbackReasons.push('no-explicit-binding');
  for(const ref of selection.refs||[]){if(!Object.hasOwn(bindings,ref))fallbackReasons.push('unresolved-binding:'+ref);else for(const id of bindings[ref])include(id,'binding:'+ref);}
 }else if(selection?.mode!=='full')fallbackReasons.push('no-explicit-selection');
 const fallback=fallbackReasons.length>0;
 if(fallback||selection?.mode==='full')for(const m of materials)include(m.id,fallback?'full-fallback':'full-selection');
 const chosen=materials.filter(m=>reasons.has(m.id)),header=m=>'【材料 '+m.id+' · '+m.nature+'】\n'+m.text;
 need(typeof replacePrefix==='string'&&messages[0].content.startsWith(replacePrefix),'CONTEXT_PREFIX_CHANGED');
 const out=clone(messages);out[0].content=chosen.filter(m=>m.placement!=='trace-only').map(header).join('\n\n')+messages[0].content.slice(replacePrefix.length);
 const trace={version:VERSION,scope:clone(scope),mode:fallback?'full-fallback':selection.mode,fallbackReasons:[...new Set(fallbackReasons)],selection:clone(selection??null),
  materials:materials.map(m=>({id:m.id,purpose:m.purpose,nature:m.nature,required:!!m.required,source:clone(m.source),scope:clone(m.scope),hash:hash(m.text),characters:m.text.length,bytes:Buffer.byteLength(m.text),selected:reasons.has(m.id),reason:reasons.get(m.id)||'outside-explicit-binding',dependencies:m.dependencies||[],placement:m.placement||'system'})),
  messages:out.map((m,index)=>({index,role:m.role,hash:hash(m),characters:typeof m.content==='string'?m.content.length:0,bytes:Buffer.byteLength(JSON.stringify(m))})),
  diagnostics:clone(prepared.diagnostics||[]),messagesHash:hash(out)};
 return {messages:out,contextTrace:trace};
}
export function material(scope,id,purpose,nature,text,source,extra={}){return {id,purpose,nature,text,source,scope:clone(scope),...extra};}
