import {clone,hash,need,keys,id,text,bounded} from '../../contracts.mjs';
import {integerDelta} from '../../arithmetic.mjs';
import {renderPanelTemplateBounded} from '../../../authoring/variable-template.mjs';
import {safeHtml} from '../../../card-replica/lib/render.mjs';
const SYSTEM='你是灯塔值守的叙事者。守灯人是已确认的成年人。物资数量由宿主维护；只提出 consumeSupply {id,amount,reason} 或 setDuty {value,reason}。正文后仅输出一个 <state_delta>{"operations":[]}</state_delta>；无变化返回空数组。未知数量不猜测；工具阶段只提出只读查询，不输出最终正文；最终阶段依据结果描述，不输出完整面板。';
const tool={type:'function',function:{name:'query_local_state',description:'查询本会话登记的灯塔物资或值守状态；未登记不等于不存在。',parameters:{type:'object',additionalProperties:false,required:['queries'],properties:{queries:{type:'array',minItems:1,maxItems:3,items:{type:'object',additionalProperties:false,required:['domain'],properties:{domain:{type:'string',enum:['supplies','duty']},id:{type:'string'},name:{type:'string'},limit:{type:'integer',minimum:1,maximum:50}}}}}}}};
export function initial(){return {keeper:{name:'许舟',age:28},supplies:[{id:'lamp-oil',name:'灯油',unit:'份',quantity:5,source:'开局确认的登记册'},{id:'bandage',name:'绷带',unit:'卷',quantity:null,source:'登记册未记数量'}],duty:'守灯，等待夜班交接'};}
export function validateState(s){
 bounded(s);keys(s,['keeper','supplies','duty']);keys(s.keeper,['name','age']);need(text(s.keeper.name,100)&&s.keeper.name.trim()&&Number.isInteger(s.keeper.age)&&s.keeper.age>=18&&s.keeper.age<=120,'LIGHT_KEEPER');
 need(text(s.duty,1000)&&s.duty.trim()&&Array.isArray(s.supplies)&&s.supplies.length<=100,'LIGHT_STATE');const seen=new Set();
 for(const e of s.supplies){keys(e,['id','name','unit','quantity','source']);need(id(e.id)&&!seen.has(e.id),'LIGHT_ID');seen.add(e.id);need(['name','unit','source'].every(k=>text(e[k],500)&&e[k].trim()),'LIGHT_ENTRY');need(e.quantity===null||(Number.isSafeInteger(e.quantity)&&e.quantity>=0),'LIGHT_QUANTITY');}return s;
}
function validateQueries(a){keys(a,['queries']);need(Array.isArray(a.queries)&&a.queries.length>=1&&a.queries.length<=3,'LIGHT_QUERY');for(const q of a.queries){keys(q,['domain','id','name','limit']);need(['supplies','duty'].includes(q.domain),'LIGHT_DOMAIN');need(q.id===undefined||id(q.id),'LIGHT_QUERY_ID');need(q.name===undefined||(text(q.name,100)&&q.name.trim()),'LIGHT_QUERY_NAME');need(q.limit===undefined||(Number.isInteger(q.limit)&&q.limit>0&&q.limit<=50),'LIGHT_LIMIT');need(q.domain!=='duty'||(q.id===undefined&&q.name===undefined),'LIGHT_DUTY_FILTER');}return a;}
function query(state,stateRevision,args){
 validateState(state);validateQueries(args);return {stateRevision,scope:'仅指定登记范围；不涵盖所有世界事实或未建模容器。',results:args.queries.map(q=>{
 const all=q.domain==='duty'?[{id:'duty',value:state.duty}]:state.supplies.filter(e=>(!q.id||e.id===q.id)&&(!q.name||e.name.includes(q.name)));
 const entries=clone(all.slice(0,q.limit??50)),unset=entries.filter(e=>e.quantity===null).map(e=>e.id);
 return {domain:q.domain,status:all.length===0?'no-match':unset.length?'unset':'found',coverage:entries.length<all.length?'partial':'complete-registered-scope',total:all.length,entries,unset};
 })};
}
export function evaluate(previous,operations){
 validateState(previous);need(Array.isArray(operations)&&operations.length<=80,'LIGHT_OPERATIONS');const state=clone(previous),changes=[];
 for(const op of operations){need(text(op.reason,500)&&op.reason.trim(),'STATE_REASON_REQUIRED');
 if(op.type==='consumeSupply'){
  keys(op,['type','id','amount','reason']);need(Number.isSafeInteger(op.amount)&&op.amount>0,'STATE_DELTA_INTEGER');const entry=state.supplies.find(e=>e.id===op.id);need(entry,'STATE_ENTRY_NOT_FOUND');
  const result=integerDelta(entry.quantity,-op.amount);entry.quantity=result.after;
  changes.push({path:'supplies.'+entry.id+'.quantity',...result,reason:op.reason,authority:'rule-calculation',type:op.type});
 }else if(op.type==='setDuty'){
  keys(op,['type','value','reason']);need(text(op.value,1000)&&op.value.trim(),'LIGHT_DUTY');if(state.duty!==op.value)changes.push({path:'duty',before:state.duty,after:op.value,reason:op.reason,authority:'model-judgment',type:op.type});state.duty=op.value;
 }else need(false,'STATE_OPERATION_UNKNOWN');validateState(state);
 }return {state,changes};
}
function candidate(state,receipt){
 need(receipt?.status==='completed'&&receipt.finishReason==='stop','STATE_REPLY_INCOMPLETE');
 const match=receipt.text?.match(/^([\s\S]+?)\s*<state_delta>([\s\S]*?)<\/state_delta>\s*$/);
 need(match&&!match[1].includes('<state_delta>'),'STATE_DELTA_FORMAT');
 let p;try{p=JSON.parse(match[2]);}catch{need(false,'STATE_DELTA_JSON');}keys(p,['operations']);
 return {body:match[1].trim(),operations:p.operations,...evaluate(state,p.operations)};
}
export async function lighthouseAdapter(){return {
 id:'lighthouse',name:'灯塔值守',description:'原创异题材验证配置；没有修仙字段，也不增加新玩法。',opening:'许舟接过值夜登记簿，沿着石阶走上灯室。海面渐暗，今晚的值守才刚开始。',
 binding:{adapterId:'lighthouse',adapterVersion:'3',stateSchemaVersion:'lighthouse/1',rulesVersion:'lighthouse/1',sourceHash:hash({SYSTEM,initial:initial()}),effectiveContextHash:hash(SYSTEM),manifestHash:'0'.repeat(64)},
 ownership:{keeper:'confirmed-anchor',supplies:'confirmed-catalog; quantity:rule-calculation',duty:'model-judgment'},
 initial,validateState,validateConfig:async c=>{keys(c,['profiles','mods']);need(Array.isArray(c.profiles)&&!c.profiles.length&&Array.isArray(c.mods)&&!c.mods.length,'LIGHT_CONFIG');},
 assemble:async(r,input,options={})=>{need(options.quantityOnly==null,'LIGHT_POLICY');return {messages:[{role:'system',content:SYSTEM+'\n当前简况：'+JSON.stringify({keeper:r.state.keeper,duty:r.state.duty})},...r.turns.flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.body}]),{role:'user',content:input}],tools:[clone(tool)],tool_choice:'auto',statePolicy:{},stateRevision:r.stateRevision};},
 query:{tool,validate:validateQueries,execute:query},candidate,
 render:async s=>renderPanelTemplateBounded('<details open class="state-panel"><summary>值守档案</summary><p>{{keeper.name}} · {{keeper.age}} 岁</p><p>{{duty}}</p></details><details open class="state-panel"><summary>已登记物资</summary>{{#each supplies}}<section><h3>{{name}} · {{quantityText}}</h3><p>{{source}}</p></section>{{/each}}</details>',{...s,supplies:s.supplies.map(e=>({...e,quantityText:e.quantity===null?'未设定':e.quantity+' '+e.unit+(e.quantity===0?'（已耗尽，档案保留）':'')}))}),
 display:async body=>safeHtml(body),
 memory:async(r,t)=>({summary:[{role:'user',content:JSON.stringify({body:t.body,changes:t.changes})}],memory:[{role:'user',content:JSON.stringify({body:t.body,changes:t.changes,corrections:r.events.filter(e=>e.kind==='override')})}]}),
 scenarios:[['query-use','先查询灯油，再给灯具添油两份'],['use','给灯具添油两份'],['rest','用完剩余三份灯油'],['talk','望着海面，聊聊值夜的安排'],['query','查看全部登记物资'],['bandage','查看绷带'],['unknown','消耗一卷绷带'],['duty','准备交接班']],
 fixture(request,{input,state,variant='calm'}){
  if(request.tool_choice==='auto'&&/查看|查询/.test(input))return {query:{queries:[/绷带/.test(input)?{domain:'supplies',name:'绷带'}:{domain:'supplies'}]}};
  const operations=[];let body='你沿窗确认海面上的灯光，翻开值守日志，准备写下今晚第一条记录。';
  if(/添油|三份灯油/.test(input)){operations.push({type:'consumeSupply',id:'lamp-oil',amount:input.includes('三份')?3:2,reason:input});body='你给灯具添了油，擦净手上的油迹，回到窗边观察海面。灯光稳稳照着石阶，交班的脚步声还没有传来。';}
  else if(input.includes('消耗')){operations.push({type:'consumeSupply',id:'bandage',amount:1,reason:input});body='你准备取用绷带，先核对登记册。';}
  else if(input.includes('交接班')){operations.push({type:'setDuty',value:'准备交接班',reason:input});body='你把值守日志翻到最新一页，等待接班的人上楼。';}
  else if(request.tool_choice==='none'){const r=JSON.parse(request.messages.at(-1).content).results[0];body='你核对登记册：'+r.entries.map(e=>e.name+'（'+(e.quantity===null?'数量未设定':e.quantity+' '+e.unit)+'）').join('、')+'。未登记的情况仍需另行确认。';}
  if(variant==='warm')body+='\n海风轻敲窗框，你把登记簿压好，继续守望。';
  return {text:body+'\n<state_delta>'+JSON.stringify({operations})+'</state_delta>'};
 }
};}
