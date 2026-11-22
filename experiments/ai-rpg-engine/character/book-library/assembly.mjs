import {hasExclusive,exclusiveResolver} from './exclusive.mjs';
import {hasProbability,probabilityGate} from './probability.mjs';
import {hasCooldown,prepareCooldownBook} from './cooldown.mjs';
import {keepHeader,prepareKeepBook} from './keep.mjs';
import {hasFallback,prepareFallbackBook} from './fallback.mjs';
import {timingHeader,prepareTimedBook} from './timing.mjs';
import {depthHeader,parseDepthBook,selectDepthBook} from './depth.mjs';
import {withMeter} from '../v3/selection.mjs';
import {onceBody,prepareOnceBook} from './once.mjs';
import {plan,defaults} from '../long-context/planner.mjs';
import {prepare} from '../long-context/request.mjs';
import {copy,check,digest,visible,boundary} from '../revisions/contracts.mjs';
import {resolve,scopeRank,pin} from './bindings.mjs';

function project(card, books, history, input, player, clock, once, actor, cardRef, depth, count, fallback, keep, cooldown, turn, exclusive) {
  const records=[], candidates=[];
  for(const source of books) {
    const v3=source.owner!=='card'||card.spec==='chara_card_v3';
    const resolved=fallback&&v3?prepareFallbackBook(source.book,{depth,once:!!once,timing:count!==null,keep:!!keep}):null;
    const selectedBook=resolved?.book??source.book;
    const persistent=v3&&keep?prepareKeepBook(selectedBook,source.owner==='card'?cardRef:source.ref,actor,keep):null;
    const definitionBook=persistent?.book??selectedBook;
    const timed=v3&&count!==null?prepareTimedBook(definitionBook,count):null;
    const timedBook=timed?.book??definitionBook;
    const scoped=depth&&v3?parseDepthBook(timedBook):null;
    const definition=scoped?.book??timedBook;
    const eligible=once&&v3;
    const prepared=eligible?prepareOnceBook(definition,source.owner==='card'?cardRef:source.ref,actor,once):null;
    const matchedBook=prepared?.book??definition;
    const gated=v3&&cooldown?prepareCooldownBook(matchedBook,source.owner==='card'?cardRef:source.ref,actor,cooldown):null;
    const book=gated?.book??matchedBook;
    const trial={...card,data:{...card.data,character_book:book}};
    const opts={budget:Infinity,player};
    const gate=v3&&turn?probabilityGate(book,turn,source.owner==='card'?cardRef:source.ref):undefined;
    const chosen=selectDepthBook(trial,history,input,opts,v3&&clock,scoped?.rules,gate,v3&&exclusive?exclusiveResolver(book):undefined);
    for(const item of chosen.trace) {
      const e=book.entries[item.index];
      const trace={owner:source.owner,ref:source.ref,index:item.index,id:item.id,
        ...(item.exclusive?{exclusive:item.exclusive}:{}),...(item.probability?{probability:item.probability}:{}),...(item.window?{window:item.window}:{}),...(Number.isInteger(item.key)?{key:item.key}:{}),status:item.status==='budget-excluded'?'book-budget':item.status};
      Object.assign(trace,timed?.traces.get(item.index));
      const rule=prepared?.rules.get(item.index);
      if(rule){
        trace.onceKey=rule.key;
        if(source.book.entries[item.index].enabled&&rule.previous)Object.assign(trace,rule.previous);
      }
      Object.assign(trace,persistent?.traces.get(item.index),resolved?.traces.get(item.index));
      Object.assign(trace,gated?.traces.get(item.index));
      records.push(trace);
      if(item.status==='triggered') candidates.push({entry:e,trace});
    }
  }
  // Distinct synthetic ranks encode the agreed tie-breakers without altering source entries.
  candidates.sort((a,b)=>(b.entry.priority??0)-(a.entry.priority??0)||
    scopeRank[a.trace.owner]-scopeRank[b.trace.owner]);
  check(candidates.length<=2000,'BOOK_CANDIDATE_LIMIT');
  const insertion=[...candidates].sort((a,b)=>a.entry.insertion_order-b.entry.insertion_order);
  const positions=new Map(insertion.map((row,i)=>[row,i]));
  const entries=candidates.map((row,i)=>({keys:[],extensions:{},constant:true,enabled:true,
    insertion_order:positions.get(row),priority:candidates.length-i,content:row.entry.content,
    position:row.entry.position??'after_char'}));
  return {records,candidates,book:{entries,extensions:{},token_budget:8192}};
}

function historyWindow(messages, group, playerId, pinOpening) {
  let units=0, started=false;
  for(const m of messages) {
    if(m.provenance.kind==='opening'){if(!pinOpening)units++;continue;}
    if(group||!started||m.speakerId===playerId)units++;
    started=true;
  }
  return units;
}

export function assemble(family, operationId, intent, model, settings=defaults(), bindings=[], library={}, rules=false, once=null, depth=false, timing=false, fallback=false, keep=null, cooldown=null, probability=false, exclusive=false) {
  if(rules===true)return withMeter(clock=>assemble(family,operationId,intent,model,settings,bindings,library,clock,once,depth,timing,fallback,keep,cooldown,probability,exclusive));
  const original=prepare(family,operationId,intent,model,settings);
  const {request}=original, actor=request.targetId;
  if(intent.kind==='regenerate')return {...original,books:{exact:true}};
  const resolved=resolve(library,bindings,actor);
  const originalCard=family.source.cards[actor].document.character.card;
  const needsProjection=originalCard.spec==='chara_card_v3'&&originalCard.data.character_book?.entries.some(e=>(rules&&e.use_regex)||(once&&onceBody(e.content)!==null)||(depth&&depthHeader(e.content))||(timing&&timingHeader(e.content))||(fallback&&hasFallback(e.content))||(keep&&keepHeader(e.content))||(cooldown&&hasCooldown(e))||(probability&&hasProbability(e))||(exclusive&&hasExclusive(e)));
  if(!resolved.length&&!needsProjection)return {...original,books:{sources:[],entries:[]}};
  const b={...family.branches[intent.branchId],current:request.boundary,messages:request.prefix};
  const source=family.source, config=source.cards[actor],card=config.document.character.card;
  const player=boundary(b).members[0];
  const books=resolved.map(s=>({owner:s.owner,ref:pin(s.version),book:s.version.book}));
  if(card.data.character_book)books.push({owner:'card',ref:{versionId:boundary(b).members.find(m=>m.id===actor).binding.reference.versionId},book:card.data.character_book});
  books.sort((a,b)=>scopeRank[a.owner]-scopeRank[b.owner]);
  const shown=visible(b,actor).filter(m=>m.id!==request.inputMessage?.id);
  const speakers=new Set(boundary(b).members.filter(m=>m.kind==='character').map(m=>m.id));
  const count=timing?shown.filter(m=>speakers.has(m.speakerId)).length:null;
  const turn=probability?[family.id,b.id,actor,shown.findLast(m=>m.speakerId===actor&&m.provenance.kind==='model-result')?.revisionId??null]:null;
  const cardRef=(once||keep||cooldown||probability)&&card.data.character_book?boundary(b).members.find(m=>m.id===actor).binding.reference:null;
  let history=shown,policy={...settings};
  for(let pass=0;pass<=shown.length+1;pass++) {
    const projection=project(card,books,history,request.inputMessage?.content??'',player.name,rules,once,actor,cardRef,depth,count,fallback,keep,cooldown,turn,exclusive);
    const data={...card.data,character_book:projection.book};
    const document={...config.document,character:{...config.document.character,card:{...card,data}}};
    const adapted={...source,cards:{...source.cards,[actor]:{...config,document}}};
    const result=plan({source:adapted,branch:b,targetId:actor,model,policy,inputMessage:request.inputMessage});
    const kept=new Set(result.trace.filter(t=>t.kind==='message'&&t.status==='included').map(t=>t.id));
    const retained=shown.filter(m=>kept.has(m.id));
    if(retained.length!==history.length) {
      check(retained.length<history.length,'BOOK_WINDOW');
      history=retained;
      policy={...policy,historyLimit:historyWindow(retained,source.kind==='group',player.id,settings.pinOpening)};
      continue;
    }
    const contribution=result.trace.find(t=>t.kind==='book');
    for(const item of contribution.entries)if(item.status==='budget-excluded')projection.candidates[item.index].trace.status='request-budget';
    contribution.entries=projection.records;
    const evidence={policy:exclusive?'character-book-merge/10':probability?'character-book-merge/9':cooldown?'character-book-merge/8':keep?'character-book-merge/7':fallback?'character-book-merge/6':timing?'character-book-merge/5':depth?'character-book-merge/4':once?'character-book-merge/3':rules?'character-book-merge/2':'character-book-merge/1',sources:resolved.map(s=>({selected:pin(s.version),bindings:s.sources})),entries:projection.records};
    result.configuration={...result.configuration,requestedPolicy:copy(settings),bookConfigurationHash:digest(evidence.sources)};
    result.configurationHash=digest(result.configuration);
    delete result.hash;result.hash=digest(result);
    const wire={...request,context:result.messages,contextHash:result.requestHash,reservation:result.reservation};
    delete wire.hash;wire.hash=digest(wire);
    return {request:wire,plan:result,books:evidence};
  }
  check(false,'BOOK_WINDOW');
}
