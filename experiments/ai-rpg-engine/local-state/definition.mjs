import {createHash} from 'node:crypto';
import {canonical} from '../authoring/contracts.mjs';
export const FORMAT='modelmirror.rpg.local-state/1';
export const ADAPTER='cultivation-local-state/1';
export const SOURCE_HASH='9a1e707828659b3ee1daa9628c334c1782b6526b066287836e7f8db7806a5dc2';
export const clone=structuredClone;
export const hash=v=>createHash('sha256').update(typeof v==='string'?v:canonical(v)).digest('hex');
export const fail=(code,status=400)=>Object.assign(new Error(code),{code,status});
export function need(ok,code='STATE_INVALID'){if(!ok)throw fail(code);}
export const id=v=>typeof v==='string'&&/^[a-zA-Z][a-zA-Z0-9-]{0,79}$/.test(v);
export const text=(v,max=2000)=>typeof v==='string'&&v.length<=max;
export const number=v=>Number.isSafeInteger(v)&&v>=0;
export function keys(v,names){need(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...names].sort().join('|'),'STATE_FIELDS');}
export const PROFILE=['name','gender','initialAge','personality','belief','root','constitution','appearance'];
export const TRAITS=['age','affiliation','reputation','clothing','realm','lifespan','cultivationTime','breakthrough','awakening','goldenCore','nascentSoul','awareness','practice','energyForm','requirements','alchemy','crafting','furnace','energy','stamina','injury','mental','goal','innerThought'];
export const ACCOUNTS=['low','mid','high','top','contribution'];
export const SLOTS=['natal','external','defense','storage'];
export const ABILITY_KINDS=['功法','术法','神通','体质能力','丹方','器谱','炼丹炼器术','天赋','弱点'];
export const ITEM_KINDS=['法宝','装备','丹药','材料','物资'];
export const RELATION_FIELDS=['type','affiliation','group','impression','lastContact'];
export const RELATION_RULES=Object.freeze({
 relation:{daily:[1,5],help:[5,15],rescue:[15,30],friction:[-8,-1],harm:[-30,-10],betrayal:[-60,-30]},
 trust:{promise:[1,5],hardship:[5,15],entrust:[15,30],lie:[-15,-5],betrayal:[-60,-30]}
});
export function blankState(){
 return {format:FORMAT,profile:Object.fromEntries(PROFILE.map(k=>[k,null])),marks:[],
 traits:Object.fromEntries(TRAITS.map(k=>[k,null])),balances:Object.fromEntries(ACCOUNTS.map(k=>[k,null])),
 abilities:[],items:[],equipment:Object.fromEntries(SLOTS.map(k=>[k,null])),relations:[],factions:[]};
}
export function validateState(s){
 keys(s,['format','profile','marks','traits','balances','abilities','items','equipment','relations','factions']);need(s.format===FORMAT);
 for(const [v,ks] of [[s.profile,PROFILE],[s.traits,TRAITS]]){keys(v,ks);need(Object.values(v).every(x=>x===null||text(x)));}
 keys(s.balances,ACCOUNTS);need(Object.values(s.balances).every(x=>x===null||number(x)));
 keys(s.equipment,SLOTS);need(Object.values(s.equipment).every(x=>x===null||id(x)));
 need(Array.isArray(s.marks)&&s.marks.length<=100&&s.marks.every(x=>text(x)&&x.trim()));
 for(const name of ['abilities','items','relations','factions']){
  const list=s[name];need(Array.isArray(list)&&list.length<=300);const ids=new Set();
  for(const e of list){need(id(e.id)&&!ids.has(e.id),'STATE_ENTRY_ID');ids.add(e.id);need(text(e.name,100)&&e.name.trim(),'STATE_ENTRY_NAME');}
 }
 for(const a of s.abilities){keys(a,['id','kind','name','description','progress','source']);need(ABILITY_KINDS.includes(a.kind)&&[a.description,a.progress,a.source].every(x=>text(x))&&a.source.trim());}
 for(const i of s.items){keys(i,['id','kind','name','description','quantity','source','condition']);need(ITEM_KINDS.includes(i.kind)&&[i.description,i.source,i.condition].every(x=>text(x))&&i.source.trim());need(i.quantity===null||number(i.quantity),'STATE_QUANTITY');}
 for(const r of s.relations){keys(r,['id','name','relation','trust',...RELATION_FIELDS]);need(Number.isInteger(r.relation)&&r.relation>=-100&&r.relation<=100&&Number.isInteger(r.trust)&&r.trust>=0&&r.trust<=100,'STATE_RELATION_RANGE');need(RELATION_FIELDS.every(k=>text(r[k]))&&['核心','一般','休眠'].includes(r.group));}
 for(const f of s.factions){keys(f,['id','name','description']);need(text(f.description));}
 need(Object.values(s.equipment).every(x=>x===null||s.items.some(i=>i.id===x)),'STATE_EQUIPMENT_REFERENCE');
 need(Buffer.byteLength(JSON.stringify(s))<=65536,'STATE_CAPACITY');return s;
}
export function relationshipLabel(value,axis='relation'){
 if(axis==='trust')return value<=30?'不信任':value<=50?'略有信任':value<=70?'基本信任':value<=85?'深度信任':'托命相付';
 if(value<0)return value>=-20?'不喜':value>=-40?'厌恶':value>=-60?'敌意':value>=-80?'仇恨':'不共戴天';
 return value===0?'陌生':value<=15?'认识':value<=30?'熟人':value<=50?'道友':value<=70?'好友':value<=85?'挚友':'生死之交';
}
