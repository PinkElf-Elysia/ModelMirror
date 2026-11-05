import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {SessionRecords} from './session-storage.mjs';
import {validateJointPlan} from './joint-plan.mjs';
export const JOINT_BUDGET_FORMAT='modelmirror.rpg.authored-joint-budget/1';
const budgetToken=Symbol(),budgets=new WeakSet();
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
const integer=n=>Number.isSafeInteger(n)&&n>=0;
const active=['dispatching','unknown'],finished=['completed','failed','cancelled','not_dispatched'],states=['reserved','released',...active,...finished];
const idFor=(allowanceId,plan)=>'group-'+digest({allowanceId,sessionId:plan.input.sessionId,operationId:plan.input.operationId});
const counters=s=>{let used=0,reserved=0;for(const g of s.groups)for(const t of g.stages){if(t.state==='reserved')reserved++;else if(!['released','not_dispatched'].includes(t.state))used++;}return {limit:s.limit,used,reserved,remaining:s.limit-used-reserved};};
function validate(s){
 canonical(s);keys(s,['format','allowanceId','limit','revision','groups']);
 if(s.format!==JOINT_BUDGET_FORMAT||!isId(s.allowanceId)||!integer(s.limit)||s.limit>100000||!integer(s.revision)||!Array.isArray(s.groups)||s.groups.length>1000)fail('JOINT_BUDGET_CORRUPT');
 const ids=new Set(),requests=new Set(),operations=new Set();
 for(const g of s.groups){
  keys(g,['id','plan','stages']);validateJointPlan(g.plan);
  if(g.id!==idFor(s.allowanceId,g.plan)||ids.has(g.id)||!g.plan.stages.length||!Array.isArray(g.stages)||g.stages.length!==g.plan.stages.length)fail('JOINT_BUDGET_CORRUPT');ids.add(g.id);
  for(const [i,t]of g.stages.entries()){
   keys(t,['stageId','purpose','operationId','state','requestId','requestHash','resultHash']);
   const {state,requestId,requestHash,resultHash,...stage}=t;
   if(!same(stage,g.plan.stages[i])||!states.includes(state)||operations.has(stage.operationId))fail('JOINT_BUDGET_CORRUPT');operations.add(stage.operationId);
   if(['reserved','released'].includes(state)){if(requestId!==null||requestHash!==null||resultHash!==null)fail('JOINT_BUDGET_CORRUPT');}
   else{
    if(!isId(requestId)||!isHash(requestHash)||requests.has(requestId)||(finished.includes(state)?!isHash(resultHash):resultHash!==null))fail('JOINT_BUDGET_CORRUPT');requests.add(requestId);
    if(g.stages.slice(0,i).some(p=>!['completed','released'].includes(p.state)))fail('JOINT_BUDGET_ORDER_CORRUPT');
   }
  }
 }
 if(counters(s).remaining<0)fail('JOINT_BUDGET_CORRUPT');return s;
}
// Host-only shared allowance, explicitly supplied by trusted configuration.
// This class cannot buy credit, certify a model or invoke a transport.
export class JointBudget {
 #records;#id;#allowance;#limit;#queue=Promise.resolve();#closed=false;
 constructor(token,records,id,allowance,limit){if(token!==budgetToken)fail('JOINT_BUDGET_REQUIRED');budgets.add(this);this.#records=records;this.#id=id;this.#allowance=allowance;this.#limit=limit;}
 static async open({directory,allowanceId,limit=0,recoverDeadOwner=false,storageFault}={}){
  if(!isId(allowanceId)||!integer(limit)||limit>100000)fail('JOINT_ALLOWANCE_INVALID');
  const records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-joint-budget'}),id='allowance';
  try{
   // A single CAS record binds the directory to exactly one allowance. Changing
   // a work/session ID or reopening with another name cannot mint fresh credit.
   const current=await records.get(id);
   if(!current)await records.put(id,null,{format:JOINT_BUDGET_FORMAT,allowanceId,limit,revision:0,groups:[]});
   else {validate(current.data);if(current.data.allowanceId!==allowanceId||current.data.limit!==limit)fail('JOINT_ALLOWANCE_CHANGED');}
   return new JointBudget(budgetToken,records,id,allowanceId,limit);
  }catch(error){await records.close();throw error;}
 }
 #serial(fn){if(this.#closed)fail('JOINT_BUDGET_CLOSED');const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #load(){const r=await this.#records.get(this.#id);if(!r)fail('JOINT_BUDGET_CORRUPT');validate(r.data);if(r.data.allowanceId!==this.#allowance||r.data.limit!==this.#limit)fail('JOINT_ALLOWANCE_CHANGED');return r;}
 async #save(r){r.data.revision++;validate(r.data);return this.#records.put(this.#id,r.hash,r.data);}
 status(){return this.#serial(async()=>{const r=await this.#load();return {...counters(r.data),revision:r.data.revision,allowanceId:r.data.allowanceId};});}
 read(groupId){return this.#serial(async()=>{if(!isId(groupId))fail('JOINT_GROUP_INVALID');const r=await this.#load();return clone(r.data.groups.find(g=>g.id===groupId)??null);});}
 reserve(value){const plan=clone(validateJointPlan(value));return this.#serial(async()=>{
  const r=await this.#load(),id=idFor(this.#allowance,plan),prior=r.data.groups.find(g=>g.id===id);
  if(prior){if(!same(prior.plan,plan))fail('JOINT_OPERATION_COLLISION');return {group:clone(prior),replayed:true};}
  if(!plan.stages.length)fail('JOINT_NO_TASKS');if(r.data.groups.length>=1000)fail('JOINT_RECORD_CAPACITY');
  if(counters(r.data).remaining<plan.stages.length)fail('JOINT_BUDGET_EXHAUSTED');
  const group={id,plan,stages:plan.stages.map(s=>({...s,state:'reserved',requestId:null,requestHash:null,resultHash:null}))};
  r.data.groups.push(group);await this.#save(r);return {group:clone(group),replayed:false};
 });}
 #stage(r,groupId,stageId){const g=r.data.groups.find(x=>x.id===groupId),t=g?.stages.find(x=>x.stageId===stageId);if(!g||!t)fail('JOINT_STAGE_NOT_FOUND');return {g,t};}
 claim({groupId,stageId,requestId,requestHash}){return this.#serial(async()=>{
  if(!isId(requestId)||!isHash(requestHash))fail('JOINT_REQUEST_INVALID');
  const r=await this.#load(),{g,t}=this.#stage(r,groupId,stageId);
  if(t.state!=='reserved'){
   if(t.state==='released')fail('JOINT_STAGE_RELEASED');
   if(t.requestId!==requestId||t.requestHash!==requestHash)fail('JOINT_REQUEST_COLLISION');
   return {dispatch:false,state:t.state};
  }
  if(r.data.groups.some(g=>g.stages.some(t=>t.requestId===requestId)))fail('JOINT_REQUEST_COLLISION');
  if(g.stages.slice(0,g.stages.indexOf(t)).some(p=>!['completed','released'].includes(p.state)))fail('JOINT_STAGE_BLOCKED');
  t.state='dispatching';t.requestId=requestId;t.requestHash=requestHash;await this.#save(r);
  return {dispatch:true,state:t.state};
 });}
 uncertain(groupId,stageId){return this.#serial(async()=>{
  const r=await this.#load(),{t}=this.#stage(r,groupId,stageId);if(!active.includes(t.state))fail('JOINT_STAGE_STATE');
  if(t.state!=='unknown'){t.state='unknown';await this.#save(r);}return clone(t);
 });}
 settle({groupId,stageId,requestId,requestHash,status,resultHash}){return this.#serial(async()=>{
  if(!finished.includes(status)||!isHash(resultHash))fail('JOINT_RESULT_INVALID');
  const r=await this.#load(),{t}=this.#stage(r,groupId,stageId);
  if(t.requestId!==requestId||t.requestHash!==requestHash)fail('JOINT_REQUEST_COLLISION');
  if(finished.includes(t.state)){if(t.state!==status||t.resultHash!==resultHash)fail('JOINT_RESULT_COLLISION');return clone(t);}
  if(!active.includes(t.state))fail('JOINT_STAGE_STATE');t.state=status;t.resultHash=resultHash;await this.#save(r);return clone(t);
 });}
 release(groupId,stageId){return this.#serial(async()=>{
  const r=await this.#load(),{t}=this.#stage(r,groupId,stageId);if(!['reserved','released'].includes(t.state))fail('JOINT_DISPATCH_COUNT_RETAINED');
  if(t.state!=='released'){t.state='released';await this.#save(r);}return clone(t);
 });}
 releaseUnused(groupId){return this.#serial(async()=>{
  const r=await this.#load(),g=r.data.groups.find(x=>x.id===groupId);if(!g)fail('JOINT_GROUP_INVALID');let changed=false;
  for(const t of g.stages)if(t.state==='reserved'){t.state='released';changed=true;}
  if(changed)await this.#save(r);return clone(g);
 });}
 reconcile(){return this.#serial(()=>this.#records.reconcile());}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}

const budgetMethods=Object.fromEntries(['status','read','reserve','claim','uncertain','settle','release','releaseUnused','reconcile'].map(k=>[k,JointBudget.prototype[k]]));
export function requireJointBudget(value){
 if(!budgets.has(value))fail('JOINT_BUDGET_REQUIRED');
 return Object.freeze(Object.fromEntries(Object.entries(budgetMethods).map(([name,method])=>[name,method.bind(value)])));
}
