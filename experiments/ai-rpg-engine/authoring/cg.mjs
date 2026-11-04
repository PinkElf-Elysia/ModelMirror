import {canonical, sha256, keys, isId, fail, validateCG, validateManifest, cgIssues} from './contracts.mjs';

export const CG_PLAN_FORMAT = 'modelmirror.rpg.ordinary-cg-plan/1';
export const CG_LIMITS = Object.freeze({sourceBytes:2*1024*1024, planBytes:2*1024*1024, scanUnits:128*1024*1024, images:32, bytes:8*1024*1024});
const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
function decimals(value) {
  const [mantissa, exponent='0'] = value.toString().split('e'), dot = mantissa.indexOf('.');
  return {units:BigInt(mantissa.replace('.', '')), exponent:Number(exponent)-(dot < 0 ? 0 : mantissa.length-dot-1)};
}
function weights(images) {
  if (!Array.isArray(images) || !images.length || images.length > 200) fail('AUTHORING_CG_WEIGHTS');
  const values = images.map(image => {
    if (!finite(image?.weight)) fail('AUTHORING_CG_WEIGHTS');
    return decimals(image.weight);
  });
  const exponent = Math.min(...values.map(v => v.exponent));
  const units = values.map(v => v.units*10n**BigInt(v.exponent-exponent));
  const total = units.reduce((a,b) => a+b, 0n);
  if (!total) fail('AUTHORING_WEIGHT_TOTAL_ZERO');
  return {units,total};
}
/** Observed 1/1/1 -> 33/33/34. General floor + final-positive remainder is a local rule. */
export function normalizeCGWeights(images) {
  const {units,total} = weights(images);
  const points = units.map(value => Number(value*100n/total));
  const last = units.findLastIndex(value => value > 0n);
  points[last] += 100-points.reduce((a,b) => a+b, 0);
  return images.map((image,index) => ({...structuredClone(image),weight:points[index]}));
}
/** Exact decimal arithmetic, half-open bins; entropy is supplied once by the host. */
export function chooseCGImage(images, draw) {
  const {units,total} = weights(images);
  if (!finite(draw) || draw >= 1) fail('AUTHORING_CG_DRAW_REQUIRED');
  const d = decimals(draw), numerator = d.exponent < 0 ? d.units : d.units*10n**BigInt(d.exponent), denominator = d.exponent < 0 ? 10n**BigInt(-d.exponent) : 1n;
  let sum = 0n;
  for (let index=0; index<units.length; index++) {
    sum += units[index];
    if (units[index] > 0n && sum*denominator > numerator*total) return index;
  }
  fail('AUTHORING_CG_WEIGHTS');
}
function capacity(value) {
  keys(value,['scanUnits','images','bytes'],'/capacity');
  for (const [key,amount] of Object.entries(value)) if (!Number.isSafeInteger(amount) || amount < 0 || amount > CG_LIMITS[key]) fail('AUTHORING_CG_CAPACITY_CONFIG','/capacity/'+key);
  return structuredClone(value);
}
function source(value) {
  keys(value,['id','complete','user','assistant'],'/turn');
  if (!isId(value.id) || value.complete !== true) fail('AUTHORING_CG_COMPLETE_TURN_REQUIRED');
  for (const key of ['user','assistant']) if (typeof value[key] !== 'string' || !value[key].isWellFormed()) fail('AUTHORING_CG_SOURCE','/turn/'+key);
  if (Buffer.byteLength(value.user)+Buffer.byteLength(value.assistant) > CG_LIMITS.sourceBytes) fail('AUTHORING_CG_SOURCE_SIZE');
  return structuredClone(value);
}
function validateBinding(request) {
  for (const key of ['workId','operationId','versionId']) if (!isId(request[key])) fail('AUTHORING_CG_BINDING','/'+key);
  if (!Number.isSafeInteger(request.revision) || request.revision < 0) fail('AUTHORING_CG_BINDING','/revision');
}
/** Host-internal only: this function never samples, writes, dispatches, or mutates history. */
export function buildCGPlan(request) {
  keys(request,['workId','operationId','versionId','revision','cg','turn','assets','draws','capacity']);
  validateBinding(request);
  const cg=validateCG(request.cg), turn=source(request.turn), assets=validateManifest(request.assets), limits=capacity(request.capacity);
  const issue=cgIssues(cg,assets)[0];
  if (issue) fail(issue.code,issue.path);
  const draws = new Map();
  if (!Array.isArray(request.draws) || request.draws.length > 2000) fail('AUTHORING_CG_DRAW_REQUIRED');
  for (const row of request.draws) {
    keys(row,['id','value'],'/draws');
    if (!cg.entries.some(entry=>entry.id===row.id) || draws.has(row.id) || !finite(row.value) || row.value >= 1) fail('AUTHORING_CG_DRAW_REQUIRED');
    draws.set(row.id,row.value);
  }
  const pool=[{role:'user',text:turn.user.toLowerCase()},{role:'assistant',text:turn.assistant.toLowerCase()}];
  const byAlias=new Map(assets.map(asset=>[asset.alias,asset]));
  const decisions=[], selections=[];
  let scanUnits=0;
  for (const [index,entry] of cg.entries.entries()) {
    const decision={entryId:entry.id,index,status:entry.enabled?'miss':'disabled',hits:[],imageIndex:null,draw:null};
    decisions.push(decision);
    if (!entry.enabled) continue;
    for (const [keywordIndex,word] of entry.keywords.entries()) {
      const keyword=word.toLowerCase();
      for (const message of pool.filter(item=>entry.roles.includes(item.role))) {
        scanUnits += message.text.length+keyword.length;
        if (scanUnits > limits.scanUnits) fail('AUTHORING_CG_SCAN_CAPACITY');
        if (message.text.includes(keyword)) decision.hits.push({keywordIndex,role:message.role});
      }
    }
    const hits=new Set(decision.hits.map(hit=>hit.keywordIndex));
    if (!(entry.rule==='and' ? hits.size===entry.keywords.length : hits.size>0)) continue;
    const selected=entry.mode==='regular' ? 0 : chooseCGImage(entry.images,draws.get(entry.id));
    decision.status='selected'; decision.imageIndex=selected; decision.draw=entry.mode==='weighted'?draws.get(entry.id):null;
    const asset=byAlias.get(entry.images[selected].alias);
    selections.push({entryId:entry.id,index,imageIndex:selected,target:entry.target,label:entry.name,alias:asset.alias,sha256:asset.sha256,mediaType:asset.mediaType,size:asset.size});
  }
  const chat=selections.filter(image=>image.target==='chat'), background=selections.findLast(image=>image.target==='background')||null;
  for (const image of selections) if (image.target==='background' && image!==background) {
    const decision=decisions[image.index]; decision.status='superseded'; decision.supersededBy=background.entryId;
  }
  const carried=[...chat,...(background?[background]:[])], used=new Map(carried.map(image=>[image.sha256,image.size]));
  const bytes=[...used.values()].reduce((a,b)=>a+b,0);
  if (carried.length > limits.images || bytes > limits.bytes) fail('AUTHORING_CG_OUTPUT_CAPACITY');
  const binding={workId:request.workId,operationId:request.operationId,versionId:request.versionId,revision:request.revision,turnId:turn.id,
    sourceHash:sha256(canonical(turn)),rulesHash:sha256(canonical(cg)),assetsHash:sha256(canonical(assets.slice().sort((a,b)=>a.alias<b.alias?-1:1)))};
  const payload={format:CG_PLAN_FORMAT,binding,capacity:limits,scanUnits,decisions,chat,background:background?{action:'set',image:background}:{action:'keep'},usage:{images:carried.length,bytes}};
  const serialized=canonical(payload);
  if (Buffer.byteLength(serialized) > CG_LIMITS.planBytes) fail('AUTHORING_CG_PLAN_SIZE');
  return freeze({...payload,sha256:sha256(serialized)});
}
/** A checksum is not authentication. Both expected values and artifact must come from host-owned storage. */
export function replayCGPlan(plan, expected) {
  keys(expected,['binding','sha256']);
  keys(plan,['format','binding','capacity','scanUnits','decisions','chat','background','usage','sha256']);
  const serialized=canonical(plan);
  if (Buffer.byteLength(serialized) > CG_LIMITS.planBytes+80) fail('AUTHORING_CG_PLAN_SIZE');
  const saved=JSON.parse(serialized), hash=saved.sha256; delete saved.sha256;
  if (saved.format!==CG_PLAN_FORMAT || hash!==expected.sha256 || canonical(saved.binding)!==canonical(expected.binding) || sha256(canonical(saved))!==hash) fail('AUTHORING_CG_PLAN_MISMATCH');
  return freeze({...saved,sha256:hash});
}
