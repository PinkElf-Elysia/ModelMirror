import {selectBook,expand} from '../context.mjs';
import {compileKeys,match,meter,limits,StepLimit} from './regex.mjs';

// The caller owns one meter for all visible books, recursion and history-window replans.
export function withMeter(fn){
 try{return fn(meter());}catch(error){if(!(error instanceof StepLimit))throw error;return fn({disabled:true});}
}
export function selectV3(card,history,input,options,clock){
 const book=card.data.character_book;
 if(!book?.entries.some(e=>e.use_regex))return selectBook(card,history,input,options);
 const depth=Math.min(200,Math.floor(book.scan_depth??4)),player=options.player??'玩家';
 let corpus=[...(depth?history.slice(-depth).map(m=>m.content):[]),input].join('\n');
 const selected=new Set(),rows=book.entries.map((e,index)=>({index,id:e.id??null,status:'not-triggered'}));
 for(let pass=0;pass<Math.min(32,book.entries.length+1);pass++){
  const extra=[];let chars=null,lower=null;
  for(const [index,e] of book.entries.entries()){
   const row=rows[index];if(selected.has(index))continue;
   if(!e.enabled){row.status='disabled';continue;}
   if(/^\s*@@/m.test(e.content)){row.status='unsupported-behavior';continue;}
   if(e.use_regex){
    if(clock.disabled){row.status='regex-request-limit';continue;}
    let compiled=clock.cache.get(e);
    if(!compiled){compiled=compileKeys(e.keys);clock.cache.set(e,compiled);}
    if(compiled.status){Object.assign(row,compiled);continue;}
    if(corpus.length>limits.scan*2||(chars??=[...corpus]).length>limits.scan){row.status='regex-scan-limit';continue;}
    if(!compiled.programs.some(p=>match(p,chars,clock)))continue;
   }else{
    const text=e.case_sensitive?corpus:(lower??=corpus.toLowerCase());
    const has=keys=>keys?.some(k=>k.length&&text.includes(e.case_sensitive?expand(k,card,player):expand(k,card,player).toLowerCase()));
    if(!e.constant&&!(has(e.keys)&&(!e.selective||has(e.secondary_keys))))continue;
   }
   selected.add(index);row.status='triggered';extra.push(expand(e.content,card,player));
  }
  if(!book.recursive_scanning||!extra.length)break;
  corpus+='\n'+extra.join('\n');
 }
 // Keep ordinary ranking, position, macro and budget semantics in the existing selector.
 const entries=book.entries.map((e,i)=>({...e,enabled:selected.has(i),constant:true,use_regex:false}));
 const result=selectBook({...card,data:{...card.data,character_book:{...book,recursive_scanning:false,entries}}},[],input,options);
 for(const item of result.trace)if(item.status==='budget-excluded')rows[item.index].status=item.status;
 return {...result,trace:rows};
}
// For authoring, use the same selected entries while preserving original order and the host's capacity checks.
export function previewCard(card,history,input,player){
 const result=withMeter(clock=>selectV3(card,history,input,{player,budget:Infinity},clock));
 const book=card.data.character_book;
 if(!book)return {card,trace:[]};
 const entries=book.entries.map((e,i)=>({...e,enabled:['triggered','budget-excluded'].includes(result.trace[i].status),constant:true,use_regex:false}));
 return {card:{...card,data:{...card.data,character_book:{...book,recursive_scanning:false,entries}}},trace:result.trace};
}
