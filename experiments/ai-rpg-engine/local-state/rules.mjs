import {clone,need,keys,id,text,validateState,TRAITS,ACCOUNTS,SLOTS,RELATION_FIELDS,RELATION_RULES} from './definition.mjs';
import {stripPanels} from './adapter.mjs';
const lookup=(s,key,value)=>{const e=s[key].find(x=>x.id===value);need(e,'STATE_ENTRY_NOT_FOUND');return e;};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function applyOperations(previous,operations,{strictRelations=false,quantityOnly=null}={}){
 validateState(previous);need(Array.isArray(operations)&&operations.length<=80,'STATE_OPERATIONS');
 if(quantityOnly!==null){
  keys(quantityOnly,['id','delta']);need(id(quantityOnly.id)&&Number.isSafeInteger(quantityOnly.delta)&&quantityOnly.delta<0,'STATE_CHANGE_SCOPE');
  need(operations.length===1&&operations[0]?.type==='quantity'&&operations[0].id===quantityOnly.id&&operations[0].delta===quantityOnly.delta,'STATE_CHANGE_SCOPE');
 }
 const state=clone(previous),changes=[];
 for(const op of operations){
  need(op&&text(op.reason,500)&&op.reason.trim(),'STATE_REASON_REQUIRED');
  const record=(path,before,after,authority)=>{if(!same(before,after))changes.push({path,before:clone(before),after:clone(after),reason:op.reason,authority,type:op.type});};
  const set=(obj,key,value,path,authority='model-judgment')=>{record(path,obj[key],value,authority);obj[key]=clone(value);};
  switch(op.type){
   case 'set':keys(op,['type','field','value','reason']);need(TRAITS.includes(op.field),'STATE_FIELD_NOT_WRITABLE');need(op.value===null||text(op.value));set(state.traits,op.field,op.value,'traits.'+op.field);break;
   case 'mark':keys(op,['type','value','reason']);need(text(op.value)&&op.value.trim());if(!state.marks.includes(op.value)){state.marks.push(op.value);record('marks',null,op.value,'model-judgment');}break;
   case 'addAbility':case 'addItem':{
    keys(op,['type','entry','reason']);const key=op.type==='addAbility'?'abilities':'items';need(id(op.entry?.id)&&!state[key].some(x=>x.id===op.entry.id),'STATE_ENTRY_EXISTS');
    state[key].push(clone(op.entry));record(key+'.'+op.entry.id,null,op.entry,'model-judgment');break;
   }
   case 'progress':case 'condition':{
    keys(op,['type','id','value','reason']);need(text(op.value));const key=op.type==='progress'?'abilities':'items',field=op.type==='progress'?'progress':'condition',e=lookup(state,key,op.id);
    set(e,field,op.value,key+'.'+op.id+'.'+field);break;
   }
   case 'quantity':case 'balance':{
    const quantity=op.type==='quantity';keys(op,['type',quantity?'id':'account','delta','reason']);
    need(Number.isSafeInteger(op.delta)&&op.delta!==0,'STATE_DELTA_INTEGER');
    if(!quantity)need(ACCOUNTS.includes(op.account),'STATE_ACCOUNT');
    const obj=quantity?lookup(state,'items',op.id):state.balances,key=quantity?'quantity':op.account;
    need(obj[key]!==null,'STATE_INITIAL_VALUE_REQUIRED');const next=obj[key]+op.delta;
    need(Number.isSafeInteger(next),'STATE_NUMBER_OVERFLOW');need(next>=0,'STATE_INSUFFICIENT');
    set(obj,key,next,quantity?'items.'+op.id+'.quantity':'balances.'+key,'rule-calculation');break;
   }
   case 'equip':keys(op,['type','slot','id','reason']);need(SLOTS.includes(op.slot),'STATE_SLOT');
    if(op.id!==null)need(lookup(state,'items',op.id).quantity>0,'STATE_EQUIPMENT_UNAVAILABLE');
    set(state.equipment,op.slot,op.id,'equipment.'+op.slot);break;
   case 'addRelation':{
    keys(op,['type','id','name','reason']);
    if(strictRelations){
     need(id(op.id),'STATE_ENTRY_ID');
     need(text(op.name,100)&&op.name.trim(),'STATE_ENTRY_NAME');
     need(!state.relations.some(x=>x.id===op.id||x.name.normalize('NFKC').trim()===op.name.normalize('NFKC').trim()),'STATE_RELATION_ALREADY_REGISTERED');
    }else need(id(op.id)&&!state.relations.some(x=>x.id===op.id),'STATE_ENTRY_EXISTS');
    const r={id:op.id,name:op.name,relation:0,trust:0,type:'初识',affiliation:'未设定',group:'一般',impression:op.reason,lastContact:'未设定'};
    state.relations.push(r);record('relations.'+op.id,null,r,'model-judgment');break;
   }
   case 'relation':{
    keys(op,['type','id','axis','event','delta','reason']);const range=RELATION_RULES[op.axis]?.[op.event];
    need(range&&Number.isInteger(op.delta)&&op.delta>=range[0]&&op.delta<=range[1],'STATE_RELATION_EVENT');
    const r=lookup(state,'relations',op.id),floor=op.axis==='relation'?-100:0;
    set(r,op.axis,Math.max(floor,Math.min(100,r[op.axis]+op.delta)),'relations.'+op.id+'.'+op.axis,'rule-calculation');break;
   }
   case 'relationInfo':{
    keys(op,['type','id','field','value','reason']);need(RELATION_FIELDS.includes(op.field)&&text(op.value),'STATE_RELATION_FIELD');
    set(lookup(state,'relations',op.id),op.field,op.value,'relations.'+op.id+'.'+op.field);break;
   }
   case 'faction':{
    keys(op,['type','entry','reason']);const old=state.factions.find(x=>x.id===op.entry?.id);
    if(old){need(old.name===op.entry.name,'STATE_ENTRY_ID');keys(op.entry,['id','name','description']);set(old,'description',op.entry.description,'factions.'+old.id);}
    else{state.factions.push(clone(op.entry));record('factions.'+op.entry.id,null,op.entry,'model-judgment');}break;
   }
   default:need(false,'STATE_OPERATION_UNKNOWN');
  }
  validateState(state);
 }
 return {state,changes};
}
export function decodeReply(receipt){
 need(receipt?.status==='completed'&&receipt.finishReason==='stop','STATE_REPLY_INCOMPLETE');
 need(text(receipt.text,200000)&&receipt.text.trim(),'STATE_REPLY_EMPTY');
 const raw=receipt.text,opens=[...raw.matchAll(/<state_delta>/g)],closes=[...raw.matchAll(/<\/state_delta>/g)];
 need(opens.length===1&&closes.length===1&&closes[0].index>opens[0].index,'STATE_DELTA_FORMAT');
 need(!raw.slice(closes[0].index+14).trim(),'STATE_DELTA_NOT_FINAL');
 let value;try{value=JSON.parse(raw.slice(opens[0].index+13,closes[0].index));}catch{need(false,'STATE_DELTA_JSON');}
 keys(value,['operations']);const body=(raw.slice(0,opens[0].index)+raw.slice(closes[0].index+14)).trim();
 need(body.length>0,'STATE_REPLY_EMPTY');
 need(stripPanels(body)===body&&!/<variable_panel>|【自身状态】|【关系网络】|关系[:：]\s*[-+]?\d|信任[:：]\s*\d/.test(body),'STATE_DUPLICATE_PANEL');
 return {body,operations:value.operations};
}
export function candidate(previous,receipt,policy){
 const parsed=decodeReply(receipt),result=applyOperations(previous,parsed.operations,policy);
 for(const m of parsed.body.matchAll(/<local_relation id="([^"]+)"><\/local_relation>/g))need(result.state.relations.some(r=>r.id===m[1]),'STATE_RELATION_REFERENCE');
 return {...parsed,...result};
}
export function queryState(state,{items=[],abilities=[],relations=[]}={}){
 validateState(state);
 for(const xs of [items,abilities,relations])need(Array.isArray(xs)&&xs.length<=50&&xs.every(id),'STATE_QUERY');
 const pick=(key,ids)=>ids.map(x=>clone(lookup(state,key,x)));
 return {profile:clone(state.profile),traits:clone(state.traits),balances:clone(state.balances),equipment:clone(state.equipment),
 items:pick('items',items),abilities:pick('abilities',abilities),relations:pick('relations',relations)};
}
