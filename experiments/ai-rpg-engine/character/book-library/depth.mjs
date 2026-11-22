import {selectBook} from '../context.mjs';
import {selectV3} from '../v3/selection.mjs';

export const depthPolicy='entry-scan-depth/1';

// Only leading, independently supported headers may be combined. Never rewrite the source entry.
export function depthHeader(content){
  if(!content.startsWith('@@'))return null;
  const lines=content.split(/\r?\n/);
  let end=0,depth,once=false;
  while(end<lines.length&&lines[end].startsWith('@@'))end++;
  const headers=lines.slice(0,end);
  if(!headers.some(s=>/^@@scan_depth(?:\s|$)/.test(s)))return null;
  const body=lines.slice(end).join('\n');
  for(const line of headers){
    if(/^@@dont_activate_after_match[ \t]*$/.test(line)&&!once){once=true;continue;}
    const match=/^@@scan_depth[ \t]+(0|[1-9]\d{0,2})[ \t]*$/.exec(line);
    if(!match||depth!==undefined||Number(match[1])>200)return {invalid:true};
    depth=Number(match[1]);
  }
  if(!body.trim()||/^\s*@@/m.test(body))return {invalid:true};
  // Preserve the author's line endings in the actual body.
  const start=headers.reduce((offset,line)=>offset+line.length+(content[offset+line.length]==='\r'?2:1),0);
  return {depth,once,body:content.slice(start)};
}

export function parseDepthBook(book){
  const rules=new Map();
  const entries=book.entries.map((entry,index)=>{
    const rule=depthHeader(entry.content);
    if(!rule)return entry;
    rules.set(index,rule);
    if(rule.invalid)return entry;
    return {...entry,content:(rule.once?'@@dont_activate_after_match\n':'')+rule.body};
  });
  return {book:{...book,entries},rules};
}

// Qualify each overridden entry before ordinary book-wide ranking and budget selection.
// Recursive entry text can feed ordinary neighbours but is never a message in a depth window.
export function selectDepthBook(card,history,input,options,clock,rules,gate,resolveGroup){
  const book=card.data.character_book;
  const select=(definition,past,current,draw,group)=>clock||draw||group?selectV3(definition,past,current,options,clock,draw,group):selectBook(definition,past,current,options);
  if(!rules?.size)return select(card,history,input,gate,resolveGroup);
  const diagnostics=new Map();
  const entries=book.entries.map((entry,index)=>{
    const rule=rules.get(index);
    if(!rule)return entry;
    if(rule.invalid){
      diagnostics.set(index,{status:entry.enabled?'invalid-scan-depth':'disabled'});
      return {...entry,enabled:false};
    }
    const countedInput=rule.depth>0&&Boolean(input);
    const take=rule.depth-Number(countedInput);
    const recent=take?history.slice(-take):[];
    const window={limit:rule.depth,history:recent.length,current:countedInput};
    // Avoid adding an empty input slot on continuation, including a spurious newline for regex.
    const corpus=recent.map(m=>m.content);
    if(countedInput)corpus.push(input);
    const probe={...card,data:{...card.data,character_book:{entries:[entry],scan_depth:0,recursive_scanning:false}}};
    const hit=select(probe,[],corpus.join('\n')).trace[0];
    const matched=['triggered','budget-excluded'].includes(hit.status);
    diagnostics.set(index,{window,...(!matched?{status:hit.status}:{}),...(Number.isInteger(hit.key)?{key:hit.key}:{})});
    return {...entry,enabled:matched,constant:true,use_regex:false};
  });
  const result=select({...card,data:{...card.data,character_book:{...book,entries}}},history,input,gate,resolveGroup);
  for(const row of result.trace)Object.assign(row,diagnostics.get(row.index));
  return result;
}
