import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const meta=JSON.parse(readFileSync(new URL('./resources/source.json',import.meta.url),'utf8'));
export function systemPrompt(){
 const text=readFileSync(new URL('./resources/system.txt',import.meta.url),'utf8');
 if(createHash('sha256').update(text).digest('hex')!==meta.systemSha256)throw Error('BLUE_FISH_PROMPT_DRIFT');
 return text;
}
