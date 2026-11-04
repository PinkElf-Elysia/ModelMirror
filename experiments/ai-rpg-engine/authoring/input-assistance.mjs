// Shared, deterministic authoring rules. No I/O, provider calls or source edits.
export const ASSISTANCE_LIMITS=Object.freeze({sourceUnits:2*1024*1024,inputCharacters:120000,choices:20,choiceCharacters:2000,words:1000,wordCharacters:200});
const validText=value=>typeof value==='string'&&value.isWellFormed();
const length=value=>[...value].length;
export function blockedWordsIssue(words){
  if(!Array.isArray(words)||words.length>ASSISTANCE_LIMITS.words)return 'BLOCKED_WORDS_LIMIT';
  if(words.some(word=>!validText(word)||length(word)>ASSISTANCE_LIMITS.wordCharacters))return 'BLOCKED_WORD_INVALID';
  return null;
}
export function addBlockedWord(words,word){
  const issue=blockedWordsIssue(words);
  if(issue)return {words:Array.isArray(words)?[...words]:[],issue};
  if(!validText(word)||length(word)>ASSISTANCE_LIMITS.wordCharacters)return {words:[...words],issue:'BLOCKED_WORD_INVALID'};
  if(word==='')return {words:[...words],issue:null};
  if(words.length===ASSISTANCE_LIMITS.words)return {words:[...words],issue:'BLOCKED_WORDS_LIMIT'};
  return {words:[...words,word],issue:null};
}
export const removeBlockedWord=(words,word)=>words.filter(item=>item!==word);
export function checkPlayerInput(input,words){
  const issue=blockedWordsIssue(words)||(!validText(input)||input.length>ASSISTANCE_LIMITS.inputCharacters*2||length(input)>ASSISTANCE_LIMITS.inputCharacters?'PLAYER_INPUT_LIMIT':null);
  if(issue)return {allowed:false,matches:[],issue};
  const matches=words.flatMap((word,index)=>word!==''&&input.includes(word)?[{index,word}]:[]);
  return {allowed:matches.length===0,matches,issue:null};
}

function decodeEntities(text){
  const named={amp:'&',quot:'"',apos:"'",lt:'<',gt:'>',nbsp:'\u00a0'};
  return text.replace(/&(#x[0-9a-f]+|#[0-9]+|amp|quot|apos|lt|gt|nbsp);/gi,(whole,key)=>{
    if(key[0]!=='#')return named[key.toLowerCase()];
    const point=key[1].toLowerCase()==='x'?Number.parseInt(key.slice(2),16):Number(key.slice(1));
    return point>0&&point<=0x10ffff&&!(point>=0xd800&&point<=0xdfff)?String.fromCodePoint(point):whole;
  });
}
function sourceLines(source){
  const lines=[];let start=0;
  while(start<source.length){const next=source.indexOf('\n',start),end=next<0?source.length:next+1;let text=source.slice(start,next<0?end:next);if(text.endsWith('\r'))text=text.slice(0,-1);lines.push({start,end,text});start=end;}
  return lines;
}
// Linear lexical protection, not an HTML renderer. Bit 1 excludes literal/code
// regions; bit 2 prevents a marker embedded in an HTML attribute from escaping.
function protectedSource(source,lines){
  const mask=new Uint8Array(source.length);let fence=null,start=0;
  for(const line of lines){
    const open=/^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line.text);
    if(!fence&&open){fence={char:open[1][0],length:open[1].length};start=line.start;continue;}
    if(fence){const close=/^ {0,3}(`+|~+)\s*$/.exec(line.text);if(close&&close[1][0]===fence.char&&close[1].length>=fence.length){mask.fill(1,start,line.end);fence=null;}}
    else if(/^(?: {4}|\t)/.test(line.text))mask.fill(1,line.start,line.end);
  }
  if(fence)mask.fill(1,start);
  const excluded=new Set(['script','style','pre','code','textarea','template','noscript']);
  let i=0,root=null,depth=0,rootStart=0;
  while(i<source.length){
    if(mask[i]&1){i++;continue;}
    if(source[i]!=='<'){i++;continue;}
    if(source.startsWith('<!--',i)){
      const close=source.indexOf('-->',i+4),end=close<0?source.length:close+3;
      const body=source.slice(i+4,close<0?end:close).trim();
      // Only a complete, standalone suggestions envelope is a protocol block.
      // Examples, mixed comments and comments in inert elements remain literal.
      const standalone=!root&&close>=0&&(body.startsWith('<suggested_questions>\n')||body.startsWith('<suggested_questions>\r\n'));
      if(standalone&&body.endsWith('\n</suggested_questions>')&&!body.includes('<!--')&&body.indexOf('<suggested_questions>',1)<0){mask.fill(1,i,i+4);mask.fill(1,close,end);}
      else mask.fill(1,i,end);
      i=end;continue;
    }
    const begin=i;let end=i+1,quote=null;
    // Only inspect actual tag prefixes; prose comparisons must not consume a
    // later real marker. Quotes may span lines, but never execute anything.
    if(!/^<\/?[A-Za-z]/.test(source.slice(i,i+3))){i++;continue;}
    for(;end<source.length;end++){const c=source[end];if(quote){if(c===quote)quote=null;}else if(c==='"'||c==="'")quote=c;else if(c==='>'){end++;break;}}
    const token=source.slice(begin,end),tag=/^<(\/?)([a-zA-Z][a-zA-Z0-9_:-]*)\b/.exec(token);
    if(tag){const name=tag[2].toLowerCase(),closing=tag[1]==='/';
      if(!root&&excluded.has(name)&&!closing){root=name;depth=1;rootStart=begin;}
      else if(root===name){if(closing)depth--;else if(!['script','style','textarea'].includes(name))depth++;if(depth===0){mask.fill(1,rootStart,end);root=null;}}
      if(name!=='suggested_questions')for(let n=begin;n<end;n++)mask[n]|=2;
    }
    i=end;
  }
  if(root)mask.fill(1,rootStart);
  return mask;
}
function optionText(line){
  const wrapped=/^<([a-z]{1,4})>(.*)<\/\1>$/.exec(line);
  const content=wrapped?wrapped[2]:line;
  const match=/^(?:[1-5A-E]\.|-) (.+)$/.exec(content);
  if(!match||/[<>]/.test(match[1]))return null;
  const decoded=decodeEntities(match[1]);
  return decoded.trim()?decoded:null;
}
export function projectSuggestions(source,enabled=false){
  if(!validText(source)||source.length>ASSISTANCE_LIMITS.sourceUnits)return {display:typeof source==='string'?source:'',choices:[],diagnostics:[{code:'SUGGESTIONS_SOURCE_LIMIT'}]};
  if(!enabled)return {display:source,choices:[],diagnostics:[]};
  const lines=sourceLines(source),mask=protectedSource(source,lines),choices=[],removals=[],diagnostics=[];
  const hasMask=(line,bits)=>{for(let i=line.start;i<line.end;i++)if(mask[i]&bits)return true;return false;};
  for(let i=0;i<lines.length;i++){
    if(lines[i].text!=='<suggested_questions>'||hasMask(lines[i],3))continue;
    let end=i+1;while(end<lines.length&&lines[end].text!=='</suggested_questions>')end++;
    if(end===lines.length){diagnostics.push({code:'SUGGESTIONS_INVALID',detail:'建议块未闭合，原文保留'});break;}
    const items=lines.slice(i+1,end).map(line=>hasMask(line,1)?null:optionText(line.text));
    if(hasMask(lines[end],3)||items.length<3||items.some(item=>item===null)){diagnostics.push({code:'SUGGESTIONS_INVALID',detail:'建议格式无效，原文保留'});i=end;continue;}
    if(choices.length+items.length>ASSISTANCE_LIMITS.choices||items.some(item=>length(item)>ASSISTANCE_LIMITS.choiceCharacters))return {display:source,choices:[],diagnostics:[{code:'SUGGESTIONS_CAPACITY',detail:'建议超过20项或单项2000字符，整份原文保留'}]};
    choices.push(...items);removals.push([lines[i].start,lines[end].end]);i=end;
  }
  let display='',cursor=0;
  for(const [start,end] of removals){display+=source.slice(cursor,start);cursor=end;}
  display+=source.slice(cursor);
  return {display,choices,diagnostics};
}
