import {expand} from '../context.mjs';
import {count,cost} from '../long-context/planner.mjs';
import {check,digest} from '../revisions/contracts.mjs';

export const positionPolicy='chat-prompt-positions/1';
const named=['before_desc','after_desc','personality','scenario'];
export function placementLine(line){
  const m=/^@@position[ \t]+(\w+)[ \t]*$/.exec(line);
  if(m&&named.includes(m[1]))return {slot:m[1]};
  const depth=/^@@depth[ \t]+(0|[1-9]\d{0,2})[ \t]*$/.exec(line);
  return depth&&Number(depth[1])<=200?{slot:'history',depth:Number(depth[1])}:null;
}

export function positionBook(book,sections){
  const rules=new Map();
  const entries=book.entries.map((entry,index)=>{
    if(!entry.content.startsWith('@@'))return entry;
    const header=entry.content.match(/^(?:@@[^\n]*(?:\n|$))+/)[0];
    const lines=header.trimEnd().split(/\r?\n/);
    if(!lines.some(s=>/^@@(?:position|depth)(?:\s|$)/.test(s)))return entry;
    const rest=[],slots=new Map();
    let invalid=false;
    for(const line of lines){
      if(!/^@@(?:position|depth)(?:\s|$)/.test(line)){rest.push(line);continue;}
      const rule=placementLine(line),key=line.split(/\s/)[0];
      if(!rule){invalid=true;continue;}
      if(!slots.has(key))slots.set(key,rule);
    }
    const body=entry.content.slice(header.length),rule=slots.get('@@position')??slots.get('@@depth');
    const reason=invalid||!body.trim()?'invalid-position':
      (rule.slot==='personality'&&!sections.personality||rule.slot==='scenario'&&!sections.scenario)?'position-section-absent':null;
    rules.set(index,{...rule,...(reason?{reason}:{})});
    return {...entry,enabled:entry.enabled&&!reason,content:[...rest,body].join('\n')};
  });
  return {book:{...book,entries},rules};
}

const sectionsFor=(data,group,scene,ex)=>({
  description:ex(data.description),personality:ex(data.personality),
  scenario:group?'【本群场景，替代卡片单人场景】\n'+scene:ex(data.scenario)
});

// The old planner owns visibility, capacity and admission. Only its admitted, ordered blocks
// move. Verify the entire structural suffix, never search for an author's text or delimiter.
export function placeAdmitted(result,projection,card,group,state,model){
  const accepted=projection.candidates.map((candidate,i)=>({...candidate,order:projection.book.entries[i].insertion_order}))
    .filter(c=>c.trace.status==='triggered').sort((a,b)=>a.order-b.order);
  if(!accepted.some(c=>c.trace.placement))return [];
  const ex=s=>expand(s,card,state.members[0].name);
  for(const c of accepted)c.text=ex(c.entry.content);
  const defs=sectionsFor(card.data,group,state.scene,ex),definition=Object.values(defs).filter(Boolean);
  const example=result.trace.some(t=>t.kind==='examples'&&t.status==='included')?['【对话示例，非真实经历】\n'+ex(card.data.mes_example)]:[];
  const before=accepted.filter(c=>c.entry.position==='before_char'),after=accepted.filter(c=>c.entry.position!=='before_char');
  const texts=rows=>rows.map(c=>c.text);
  const oldTail='\n\n'+[...texts(before),...definition,...texts(after),...example].join('\n\n');
  const [system,...following]=result.messages;
  check(system.role==='system'&&system.content.endsWith(oldTail),'POSITION_LAYOUT_CHANGED');
  const base=system.content.slice(0,-oldTail.length);
  const historyCount=result.trace.filter(t=>t.kind==='message'&&t.status==='included').length;
  const buckets=Array.from({length:historyCount+1},()=>[]);
  const slots=new Map(named.map(slot=>[slot,[]]));
  const placements=[];
  // Each new history system message costs 32 framing bytes while removing a two-byte
  // separator. Keep the book's admission priority if that final overhead cannot fit.
  const depthRows=projection.candidates.filter(c=>c.trace.status==='triggered'&&c.trace.placement?.slot==='history');
  let bound=result.counts.inputBound+30*depthRows.length;
  for(const c of depthRows.reverse()){
    if(bound+model.outputTokens<=model.contextTokens)break;
    c.trace.status='request-budget';
    bound-=Buffer.byteLength(ex(c.entry.content),'utf8')+32;
  }
  for(const c of accepted){
    const rule=c.trace.placement;
    if(!rule||c.trace.status!=='triggered')continue;
    const index=rule.slot==='history'?Math.max(0,historyCount-rule.depth):null;
    if(index!==null)buckets[index].push({role:'system',content:c.text});
    else slots.get(rule.slot).push(c.text);
    placements.push({ref:c.trace.ref,index:c.trace.index,...rule,...(index!==null?{afterMessages:index}:{})});
  }
  const ordinary=rows=>texts(rows.filter(c=>!c.trace.placement));
  const placed=[base,...ordinary(before),...slots.get('before_desc'),...(defs.description?[defs.description]:[]),
    ...slots.get('after_desc'),...(defs.personality?[defs.personality]:[]),...slots.get('personality'),
    ...(defs.scenario?[defs.scenario]:[]),...slots.get('scenario'),...ordinary(after),...example];
  const messages=[{role:'system',content:placed.join('\n\n')}];
  for(let i=0;i<=historyCount;i++){
    messages.push(...buckets[i]);
    if(i<historyCount)messages.push(following[i]);
  }
  messages.push(...following.slice(historyCount));
  Object.assign(result,{messages,requestHash:digest(messages),counts:count(messages,model),reservation:cost(messages,model)});
  const rank=p=>p.slot==='history'?named.length+p.afterMessages:named.indexOf(p.slot);
  return placements.sort((a,b)=>rank(a)-rank(b));
}
