import {withMeter,selectV3} from '../v3/selection.mjs';
import {selectBook} from '../context.mjs';
import {plan,defaults} from '../long-context/planner.mjs';
import {prepare} from '../long-context/request.mjs';
import {copy,check,digest,visible,boundary} from '../revisions/contracts.mjs';
import {resolve,scopeRank,pin} from './bindings.mjs';

function project(card, books, history, input, player, clock) {
  const records=[], candidates=[];
  for(const source of books) {
    const book=source.book;
    const trial={...card,data:{...card.data,character_book:book}};
    const opts={budget:Infinity,player};
    const chosen=clock&&(source.owner!=='card'||card.spec==='chara_card_v3')?selectV3(trial,history,input,opts,clock):selectBook(trial,history,input,opts);
    for(const item of chosen.trace) {
      const e=book.entries[item.index];
      const trace={owner:source.owner,ref:source.ref,index:item.index,id:item.id,
        ...(Number.isInteger(item.key)?{key:item.key}:{}),status:item.status==='budget-excluded'?'book-budget':item.status};
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

export function assemble(family, operationId, intent, model, settings=defaults(), bindings=[], library={}, rules=false) {
  if(rules===true)return withMeter(clock=>assemble(family,operationId,intent,model,settings,bindings,library,clock));
  const original=prepare(family,operationId,intent,model,settings);
  const {request}=original, actor=request.targetId;
  if(intent.kind==='regenerate')return {...original,books:{exact:true}};
  const resolved=resolve(library,bindings,actor);
  if(!resolved.length&&!(rules&&family.source.cards[actor]?.document.character.card.spec==='chara_card_v3'&&family.source.cards[actor].document.character.card.data.character_book?.entries.some(e=>e.use_regex)))return {...original,books:{sources:[],entries:[]}};
  const b={...family.branches[intent.branchId],current:request.boundary,messages:request.prefix};
  const source=family.source, config=source.cards[actor],card=config.document.character.card;
  const player=boundary(b).members[0];
  const books=resolved.map(s=>({owner:s.owner,ref:pin(s.version),book:s.version.book}));
  if(card.data.character_book)books.push({owner:'card',ref:{versionId:boundary(b).members.find(m=>m.id===actor).binding.reference.versionId},book:card.data.character_book});
  books.sort((a,b)=>scopeRank[a.owner]-scopeRank[b.owner]);
  const shown=visible(b,actor).filter(m=>m.id!==request.inputMessage?.id);
  let history=shown,policy={...settings};
  for(let pass=0;pass<=shown.length+1;pass++) {
    const projection=project(card,books,history,request.inputMessage?.content??'',player.name,rules);
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
    const evidence={policy:rules?'character-book-merge/2':'character-book-merge/1',sources:resolved.map(s=>({selected:pin(s.version),bindings:s.sources})),entries:projection.records};
    result.configuration={...result.configuration,requestedPolicy:copy(settings),bookConfigurationHash:digest(evidence.sources)};
    result.configurationHash=digest(result.configuration);
    delete result.hash;result.hash=digest(result);
    const wire={...request,context:result.messages,contextHash:result.requestHash,reservation:result.reservation};
    delete wire.hash;wire.hash=digest(wire);
    return {request:wire,plan:result,books:evidence};
  }
  check(false,'BOOK_WINDOW');
}
