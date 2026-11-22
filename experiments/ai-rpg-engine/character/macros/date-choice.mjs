import {boundary,check,digest,visible} from '../revisions/contracts.mjs';

import {variablePolicy,renderVariables} from './variables.mjs';

export const macroPolicy='date-choice-utc/1';
const fields=['description','personality','scenario','mes_example','system_prompt','post_history_instructions'];

// Only complete top-level tokens are considered. Nested and unknown expressions stay literal.
export function render(text,source,seed,day,base=0){
  const parts=[],items=[];
  let depth=0,start=0,end=0,nested=false;
  for(const match of text.matchAll(/\{\{|\}\}/g)){
    if(match[0]==='{{'){
      if(depth===0){start=match.index;nested=false;}else nested=true;
      depth++;continue;
    }
    if(!depth||--depth!==0||nested||match.index-start>512)continue;
    const token=text.slice(start,match.index+2),body=token.slice(2,-2);
    let value;
    if(/^date$/i.test(body))value=day;
    else if(/^pick::/i.test(body)){
      const options=body.slice(6).split('::').map(s=>s.trim());
      if(options.length<2||options.length>16||options.some(s=>!s||s.length>80||/[{}@<>\u0000-\u001f\u007f]/u.test(s)))continue;
      const hash=digest([macroPolicy,seed,source,base+start,token]);
      value=options[parseInt(hash.slice(0,12),16)%options.length];
    }else continue;
    check(items.length<128,'MACRO_FIELD_LIMIT');
    items.push({source,offset:base+start,token,value});
    parts.push(text.slice(end,start),value);end=match.index+2;
  }
  return {text:items.length?parts.join('')+text.slice(end):text,items};
}

export function macroFrame(family,branch,actor,now,variables=null){
  const prior=visible(branch,actor).findLast(m=>m.speakerId===actor&&m.provenance.kind==='model-result');
  const seed=[family.id,branch.id,actor,boundary(branch).members.find(m=>m.id===actor).binding,prior?.revisionId??null];
  const day=now?.toISOString().slice(0,10),cardTrace=new Map(),bookTrace=new Map();
  function expand(text,source){
    if(!variables)return render(text,source,seed,day);
    return renderVariables(text,source,variables,day?(token,base)=>render(token,source,seed,day,base):null);
  }
  return {
    card(card){
      const data={...card.data};
      for(const field of fields){
        if(field==='scenario'&&family.source.kind==='group')continue;
        const result=expand(data[field],field);
        data[field]=result.text;cardTrace.set(field,result.items);
      }
      return {...card,data};
    },
    book(source){
      const key=source.ref.bookVersionId??source.ref.versionId;
      const entries=source.book.entries.map((entry,index)=>{
        if(!entry.enabled||!entry.content.includes('{{'))return entry;
        // Leave decorator syntax untouched, including fallback choices.
        const header=entry.content.match(/^(?:@@[^\n]*(?:\n|$))+/)?.[0]??'';
        const result=expand(entry.content.slice(header.length),'book/'+key+'/'+index);
        if(variables)check(!/^@@/m.test(result.text)||result.text===entry.content.slice(header.length),'VARIABLE_BOOK_DIRECTIVE');
        if(result.items.length)bookTrace.set(result.items[0].source,result.items);
        return {...entry,content:header+result.text};
      });
      return {...source,book:{...source.book,entries}};
    },
    snapshot(result){
      const items=[];
      for(const [field,values] of cardTrace){
        if(field==='mes_example'&&!result.plan.trace.some(t=>t.kind==='examples'&&t.status==='included'))continue;
        items.push(...values);
      }
      for(const row of result.books.entries){
        if(row.status!=='triggered')continue;
        const key='book/'+(row.ref.bookVersionId??row.ref.versionId)+'/'+row.index;
        items.push(...(bookTrace.get(key)??[]));
      }
      check(items.length<=512,'MACRO_REQUEST_LIMIT');
      if(variables)return {policy:variablePolicy,...(day?{datePolicy:macroPolicy,timeZone:'UTC'}:{}),items};
      return {policy:macroPolicy,timeZone:'UTC',items};
    }
  };
}
