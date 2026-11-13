import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

export function assemble({input,history=[]}) {
  const {system}=JSON.parse(readFileSync(new URL('./resources/prompts.json',import.meta.url),'utf8'));
  const manifest=JSON.parse(readFileSync(new URL('./resources/MANIFEST.json',import.meta.url),'utf8'));
  if(manifest.cardId!=='harry-potter'||createHash('sha256').update(system).digest('hex')!==manifest.system.sha256)throw Error('HARRY_POTTER_PROMPT_MISMATCH');
  if(typeof input!=='string'||!input.trim())throw Error('HARRY_POTTER_INPUT_REQUIRED');
  if(!Array.isArray(history)||history.length%2||history.some((m,i)=>m?.role!==(i%2?'assistant':'user')||typeof m.content!=='string'))throw Error('HARRY_POTTER_COMPLETE_HISTORY_REQUIRED');
  return {messages:[{role:'system',content:system},...history.map(({role,content})=>({role,content})),{role:'user',content:input}]};
}
