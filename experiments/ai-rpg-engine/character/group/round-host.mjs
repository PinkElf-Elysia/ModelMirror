import {SessionRecords} from '../../authoring/session-storage.mjs';
import {isId,isHash,keys} from '../../authoring/contracts.mjs';
import {text} from '../conversation/contracts.mjs';
import {browserView,check,digest,idFor,pending} from './contracts.mjs';
import {groupPageView} from './narration.mjs';
import {GroupHost} from './host.mjs';
import {previewTurn} from './turn-preview.mjs';

const FORMAT = 'character-rounds/1';
const finished = new Set(['completed','stopped','failed','changed','blocked']);
const stepId = (groupId, roundId, index) => idFor('roundstep',groupId,roundId,index);


const budgetStops = new Set(['GROUP_BUDGET_EXHAUSTED','GROUP_TASK_BUDGET_EXHAUSTED','GROUP_PRICE_UNKNOWN',
  'GROUP_CONTEXT_CAPACITY','GROUP_REVISION_CONFLICT','GROUP_REQUEST_PENDING']);

function verifyPlan(group, round) {
  keys(round,['id','intent','revision','message','targets','rosterHash','offset','stop']);
  check(isId(round.id) && isHash(round.intent) && isHash(round.rosterHash) &&
    Number.isSafeInteger(round.revision) && round.revision >= 0 && round.revision <= group.revision &&
    Number.isSafeInteger(round.offset) && round.offset >= 0 && round.offset <= group.requests.length,'ROUND_BINDING');
  text(round.message,2000);
  check(Array.isArray(round.targets) && round.targets.length >= 1 && round.targets.length <= 3 &&
    new Set(round.targets).size===round.targets.length &&
    round.targets.every(id=>group.members.some(m=>m.kind==='character' && m.id===id)),'ROUND_BINDING');
  check(round.stop===null || round.stop==='user' || budgetStops.has(round.stop),'ROUND_FORMAT');
}

function project(group, round) {
  if (!round) return null;
  verifyPlan(group,round);
  const steps = round.targets.map((memberId,index) => {
    const operation = stepId(group.id,round.id,index);
    const request = group.requests.find(r => r.operationId === operation);
    if (request) check(request.targetId === memberId && request.id === idFor('request',group.id,operation),'ROUND_BINDING');
    return {memberId, requestId:request?.id ?? null, status:request?.status ?? 'waiting'};
  });
  const next = steps.findIndex(s => s.status !== 'completed');
  const current = steps[next]?.status;
  let status = 'ready';
  if (round.stop) status = round.stop === 'user' ? 'stopped' : 'blocked';
  else if (next < 0) status = 'completed';
  else if (current !== 'waiting') status = current;
  else if (digest(group.members) !== round.rosterHash ||
    group.requests.length !== round.offset + next ||
    (next === 0 && group.revision !== round.revision)) status = 'changed';
  return {id:round.id, status, reason:round.stop, steps, next, revision:group.revision};
}

// The group ledger alone owns dispatch, receipts, player input and money.
// The side record contains only the confirmed plan and its stop decision.
export class RoundHost {
  #queue = Promise.resolve();
  constructor(records,store,provider) {
    this.records=records; this.store=store; this.provider=provider;
  }
  static async open({directory,store,provider,recoverDeadOwner=false}) {
    check(store.mode==='fixture' && provider?.mode==='fixture','ROUND_EXECUTION_DISABLED');
    const records=await SessionRecords.open(directory,{scope:'character-rounds-v1',recoverDeadOwner});
    try { await records.reconcile(); return new RoundHost(records,store,provider); }
    catch (error) { await records.close(); throw error; }
  }
  #serial(fn) {
    const work=this.#queue.then(fn);
    this.#queue=work.catch(()=>{});
    return work;
  }
  async #load(id) {
    const group=await this.store.read(id);
    const saved=await this.records.get(id);
    const data=saved?.data ?? {format:FORMAT,groupId:id,rounds:[]};
    check(data.format===FORMAT && data.groupId===id && Array.isArray(data.rounds),'ROUND_FORMAT');
    return {group,saved,data,last:data.rounds.at(-1)};
  }
  async groupView(id) {
    const {group,last}=await this.#load(id);
    return {...groupPageView(group),round:project(group,last)};
  }
  async history(id,before=null) {
    const {group,data}=await this.#load(id);
    const end=before===null ? data.rounds.length : data.rounds.findIndex(r=>r.id===before);
    check(end>=0,'ROUND_CHANGED');
    const start=Math.max(0,end-5);
    const messages=new Map(groupPageView(group).messages.map(m=>[m.id,m.content]));
    const items=data.rounds.slice(start,end).reverse().map(saved=>{
      const round=project(group,saved);
      return {...round,input:messages.get(idFor('message',id,stepId(id,round.id,0)))??null,
        steps:round.steps.map(s=>({...s,content:s.requestId ? messages.get(idFor('message',id,s.requestId))??null : null}))};
    });
    return {revision:group.revision,items,before:start>0 ? data.rounds[start].id : null};
  }
  async view(id) {
    const {group,last}=await this.#load(id);
    return project(group,last);
  }
  narrate(id,body) {
    return this.#serial(async()=>{
      const {group,last}=await this.#load(id);
      check(!last || finished.has(project(group,last).status),'ROUND_ACTIVE');
      await this.store.narrate(id,body);
      return this.groupView(id);
    });
  }
  start(id,body) {
    return this.#serial(async()=>{
      keys(body,['operationId','previousRoundId','rules','message']);
      check(isId(body.operationId),'OPERATION_INVALID');
      text(body.message,2000);
      const {group,saved,data,last}=await this.#load(id);
      const intent=digest(body), prior=data.rounds.find(r=>r.id===body.operationId);
      if (prior) {
        check(prior.intent===intent,'OPERATION_COLLISION');
        return {round:project(group,last),duplicate:true};
      }
      check((last?.id ?? null)===body.previousRoundId && (!last || finished.has(project(group,last).status)),'ROUND_ACTIVE');
      check(!pending(group),'REQUEST_PENDING');
      const plan=previewTurn(browserView(group),body.rules);
      check(plan.count>0 && !plan.diagnostics.length,'ROUND_EMPTY');
      check(body.rules.mode!=='mentions' || body.rules.text===body.message,'ROUND_MENTION_CHANGED');
      const round={id:body.operationId,intent,revision:group.revision,message:body.message,
        targets:plan.rows.filter(r=>r.included).map(r=>r.id),rosterHash:digest(group.members),
        offset:group.requests.length,stop:null};
      data.rounds.push(round);
      await this.records.put(id,saved?.hash ?? null,data);
      return {round:project(group,round),duplicate:false};
    });
  }
  stop(id,body) {
    return this.#serial(async()=>{
      keys(body,['roundId']);
      const {group,saved,data,last}=await this.#load(id);
      check(last && last.id===body.roundId,'ROUND_CHANGED');
      if (!finished.has(project(group,last).status)) {
        last.stop='user';
        await this.records.put(id,saved.hash,data);
      }
      return project(group,last);
    });
  }
  async step(id,body) {
    keys(body,['roundId','index','expectedRevision']);
    let completion;
    await this.#serial(async()=>{
      const {group,saved,data,last}=await this.#load(id);
      check(last && last.id===body.roundId,'ROUND_CHANGED');
      const current=project(group,last);
      check(current.status==='ready' && current.next===body.index,'ROUND_STEP_CHANGED');
      check(group.revision===body.expectedRevision,'REVISION_CONFLICT');
      check(!pending(group),'REQUEST_PENDING');
      // Stop and step share this queue until the provider has actually been entered.
      // Release it before awaiting the receipt so stop works during a slow response.
      let entered;
      const boundary=new Promise(resolve=>{entered=resolve;});
      const host=new GroupHost(this.store,{
        mode:this.provider.mode,
        dispatch:request=>{
          try { return this.provider.dispatch(request); } finally { entered(); }
        }
      });
      completion=host.send(id,{operationId:stepId(id,last.id,body.index),expectedRevision:group.revision,
        payload:{targetId:last.targets[body.index],input:body.index===0 ? last.message : null}});
      try { await Promise.race([boundary,completion]); }
      catch(error) {
        if (budgetStops.has(error.code)) {
          last.stop=error.code;
          await this.records.put(id,saved.hash,data);
        }
        throw error;
      }
    });
    await completion;
    return this.view(id);
  }
  async close() { await this.#queue; await this.records.close(); }
}
