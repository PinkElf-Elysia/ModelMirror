import {createHash} from 'node:crypto';
import {rules} from './fixtures.mjs';
export const FORMAT='modelmirror.rpg.e1-proposal/1';
export function fail(code,message) { throw Object.assign(new Error(message||code),{code}); }
export function digest(value) {
  const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}
export function sourceRef(world) { return {session:world.session,branch:world.branch,version:world.version,hash:digest(world)}; }
const clean=t=>t.replace(/[\s，。！？、,.!?]/g,'');
export function normalize(world, input) {
  if (!input || typeof input!=='object' || Array.isArray(input) || Object.keys(input).some(k=>!['text','selection','action','amounts'].includes(k))) fail('INPUT_INVALID','请求字段不受支持。');
  const text=input.text;
  if(typeof text!=='string'||!text.trim()||text.length>2000) fail('INPUT_INVALID','请输入不超过 2000 字的行动。');
  const sample=world.samples.find(s=>clean(s.text)===clean(text));
  const supported=Boolean(sample) && !text.includes('永久抵御');
  const action=input.action || sample?.action || null;
  if(sample?.action && input.action && sample.action!==input.action) fail('ACTION_MISMATCH','选择的行动与样例文字不一致，请同时修改输入。');
  const selection=input.selection===undefined ? [...(sample?.selection||[])] : input.selection;
  if (!Array.isArray(selection)||selection.length>8||selection.some(v=>typeof v!=='string')||new Set(selection).size!==selection.length) fail('INPUT_INVALID','对象选择无效。');
  const amounts=input.amounts??{};
  if(!amounts||typeof amounts!=='object'||Array.isArray(amounts)||Object.keys(amounts).some(k=>!['canvas','rope'].includes(k))||Object.values(amounts).some(v=>!Number.isSafeInteger(v)||v<1||v>1000)) fail('AMOUNT_INVALID','物资用量必须是 1～1000 的整数。');
  if(action!==null && !Object.hasOwn(rules,action)) fail('ACTION_UNKNOWN','本轮未登记此动作。');
  return {text:text.trim(),selection,action,amounts,supported,ambiguous:supported&&!sample?.action};
}
export function propose(world,input,{id,revision=1}={}) {
  const n=normalize(world,input), r=rules[n.action], diagnostics=[], changes=[], basis=[];
  let question=null;
  if(!n.supported) diagnostics.push('离线样例暂不能解释这条行动或保证其中的条件。原文已保留，请使用支持的样例；不会忽略额外要求后直接确认。');
  if(n.supported&&!r) question='想先为书箱遮雨，还是遮挡入口？';
  if(r && ((world.id==='courtyard')!==(n.action==='invite'))) diagnostics.push('此场景未登记该行动规则。');
  const known=new Map(world.objects.map(o=>[o.id,o]));
  const missing=r?.required.filter(x=>!n.selection.includes(x))||[];
  const unknown=n.selection.filter(x=>!known.has(x));
  if(unknown.length) diagnostics.push('所选对象不在当前登记范围：'+unknown.join('、')+'；这不代表世界中不存在。');
  if(r&&missing.length) question='还需要选择：'+missing.map(x=>known.get(x)?.name||x).join('、')+'。';
  if(r&&n.action==='invite'&&!n.selection.some(x=>known.get(x)?.kind==='person')) question='你希望先邀请谁？';
  if(r&&n.action!=='invite'){
    const allowed=new Set(r.required);
    if(n.selection.some(x=>known.has(x)&&!allowed.has(x))) diagnostics.push('所选对象超出这项样例规则，请调整目标，不能忽略多余对象。');
    for(const key of ['canvas','rope']){
      const count=n.amounts[key]??r[key], obj=known.get(key);
      if(count<r[key]) diagnostics.push(r.label+'至少需要 '+r[key]+(obj?.unit||'')+(obj?.name||key)+'。');
      if(!Number.isSafeInteger(obj?.available)||obj.available<0) diagnostics.push((obj?.name||key)+'的可用数量未设定，需要先确认。');
      else if(count>obj.available) diagnostics.push((obj?.name||key)+'不足：现有 '+obj.available+'，拟占用 '+count+'。');
      else changes.push({id:key,name:obj.name,before:obj.available,allocated:count,after:obj.available-count,unit:obj.unit});
    }
  }
  const ready=n.supported&&Boolean(r)&&!question&&!diagnostics.length;
  if(r) basis.push({type:'registered-rule',text:r.label+'：'+(n.action==='invite'?'仅提出邀请，不登记任何人已接受。':'最少占用 '+r.canvas+' 张帆布和 '+r.rope+' 份绳索；多用材料不额外承诺效果。')});
  for(const key of n.selection) if(known.has(key)) basis.push({type:'registered-object',id:key,text:known.get(key).name+'：'+known.get(key).description});
  const proposal={
    format:FORMAT,id,revision,source:sourceRef(world),generator:'offline-fixture',
    input:{text:n.text,selection:n.selection,action:n.action,amounts:n.amounts},
    status:ready?'review':diagnostics.length?'blocked':'clarification',
    goal:r?.goal||n.text,action:r?{kind:n.action,label:r.label,targets:n.selection}:null,
    question,diagnostics,basis,
    expected:ready?{materials:changes,construction:n.action==='invite'?null:{kind:n.action,allocatedMaterials:changes.map(c=>({id:c.id,quantity:c.allocated}))},
      discussion:n.action==='invite'?{location:'court',proposedInvitees:n.selection.filter(x=>known.get(x)?.kind==='person'),acceptedInvitees:[],invitationsSent:false}:null}:null,
    assumptions:n.action==='invite'?['邀请尚未发出，人物回应未知。']:['只验证登记的物资占用，不保证耐久、风力承受或永久防护。','预计占用转入拟议搭建物，实际物资不变。'],
    confirmation:null
  };
  proposal.hash=digest(proposal);
  return proposal;
}
export function confirm(proposal,world,expectedHash,at) {
  if(proposal.hash!==expectedHash) fail('REVISION_STALE','方案已修改，请重新阅读后确认。');
  if(digest(sourceRef(world))!==digest(proposal.source)) fail('SOURCE_STALE','情境已变化，请重新核对方案。');
  if(proposal.status==='awaiting_execution') return structuredClone(proposal);
  if(proposal.status!=='review') fail('NOT_READY','请先解决澄清或限制。');
  return {...structuredClone(proposal),status:'awaiting_execution',confirmation:{proposalHash:proposal.hash,revision:proposal.revision,at}};
}
export function handoff(proposal,world) {
  if(proposal.status!=='awaiting_execution') fail('NOT_CONFIRMED','请先确认方案。');
  if(digest(sourceRef(world))!==digest(proposal.source)) fail('SOURCE_STALE','情境已变化，不能导出旧方案。');
  return {format:'modelmirror.rpg.e1-handoff/1',executionStatus:'not_executed',requiresExecutionValidation:true,
    proposal:structuredClone(proposal),notice:'仅为 E1 本地交接记录。未调用正式执行器，未改变世界状态或故事历史。'};
}



