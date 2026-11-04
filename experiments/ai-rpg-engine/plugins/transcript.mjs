export const MAX_BYTES = 10 * 1024 * 1024;
export const MAX_TURNS = 500;
const divider = '#' + '-'.repeat(43) + '#';
const boundary = '#' + '='.repeat(43) + '#';
function encode(text) {
  return text.replace(/\r\n?/g,'\n').replace(/^(?=\\|#(?:-{3,}|={3,})#$)/gm,'\\');
}
function decode(text,role) {
  const prefix='['+role+']:';
  if(!text.startsWith(prefix))return null;
  const value=text.slice(prefix.length).replace(/^\\(?=\\|#(?:-{3,}|={3,})#$)/gm,'');
  return value.trim()?value:null;
}
const invalid=code=>{throw Object.assign(Error(code),{status:400,code});};
export function* exportChunks(turns) {
  if(!turns.length)invalid('TRANSCRIPT_EMPTY');
  yield '\uFEFF';
  for(const [index,{input,raw}] of turns.entries()){
    if(index)yield '\n'+boundary+'\n';
    yield '[USER]:'+encode(input)+'\n'+divider+'\n[AI]:'+encode(raw);
  }
}
export const serialize=turns=>Array.from(exportChunks(turns)).join('');
export function parse(bytes) {
  if(bytes.byteLength>MAX_BYTES)invalid('TRANSCRIPT_TOO_LARGE');
  let text;
  try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{invalid('TRANSCRIPT_UTF8');}
  if(text.includes('\0'))invalid('TRANSCRIPT_BINARY');
  text=text.replace(/\r\n?/g,'\n');
  const turns=[];let skipped=0;
  function append(block){
    if(!block.trim())return;
    if(turns.length+skipped>=MAX_TURNS)invalid('TRANSCRIPT_TOO_MANY');
    const dividers=block.matchAll(/^#-{3,}#$/gm);
    const divider=dividers.next().value;
    if(!divider||!dividers.next().done){skipped++;return;}
    const input=decode(block.slice(0,divider.index).replace(/\n$/,''),'USER');
    const reply=decode(block.slice(divider.index+divider[0].length).replace(/^\n/,''),'AI');
    if(input===null||reply===null)skipped++;else turns.push({input,raw:reply});
  }
  let start=0;
  for(const boundary of text.matchAll(/^#={3,}#$/gm)){
    append(text.slice(start,boundary.index).replace(/\n$/,''));
    start=boundary.index+boundary[0].length;
    if(text[start]==='\n')start++;
  }
  append(text.slice(start));
  if(!turns.length)invalid('TRANSCRIPT_EMPTY');
  return {turns,skipped};
}
export function invoke({capability,session}){
  if(!['session.transcript.import','session.transcript.export'].includes(capability))invalid('TRANSCRIPT_ACTION_INVALID');
  return {kind:capability,sessionId:session.id};
}
