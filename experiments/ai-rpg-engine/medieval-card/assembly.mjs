import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
const system=readFileSync(new URL('./resources/system.txt',import.meta.url),'utf8');
const manifest=JSON.parse(readFileSync(new URL('./resources/MANIFEST.json',import.meta.url),'utf8'));
if(manifest.cardId!=='medieval'||createHash('sha256').update(system).digest('hex')!==manifest.system.sha256)throw Error('MEDIEVAL_PROMPT_MISMATCH');
export function assemble({input,history=[]}){
 if(typeof input!=='string'||!input.trim())throw Error('MEDIEVAL_INPUT_REQUIRED');
 if(!Array.isArray(history)||history.length%2||history.some((m,i)=>m?.role!==(i%2?'assistant':'user')||typeof m.content!=='string'))throw Error('MEDIEVAL_COMPLETE_HISTORY_REQUIRED');
 return {messages:[{role:'system',content:system},...history.map(({role,content})=>({role,content})),{role:'user',content:input}]};
}
