import {readFileSync} from 'node:fs';
import {hash} from './assembly.mjs';
import {godAdapter} from './adapter.mjs';

export function loadOutputSuffix(){
 const data=JSON.parse(readFileSync(new URL('../resources/output-suffix.json',import.meta.url),'utf8'));
 if(data.format!==1||data.cardId!=='god'||typeof data.text!=='string'||!data.text.trim()||
   hash(data.text)!==data.textIntegrity?.sha256||Array.from(data.text).length!==data.textIntegrity.unicodeCharacters)throw Error('GOD_OUTPUT_SUFFIX_MISMATCH');
 return data;
}
// Only the current request's user message is extended. Saved history is already
// assembled and must never be wrapped again. UI uses the separately saved input.
export function assembleWithOutputSuffix(session,input){
 const base=godAdapter.assemble(session,input),suffix=loadOutputSuffix();
 const current=base.current+'\n\n'+suffix.text;
 const messages=[...base.messages.slice(0,-1),{role:'user',content:current}];
 return {...base,current,messages,evidence:{...base.evidence,assemblyVersion:'god-user-output-suffix/1',
  userSuffixHash:suffix.textIntegrity.sha256,requestHash:hash(JSON.stringify(messages))}};
}
