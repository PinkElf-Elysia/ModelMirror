// Exact, reviewed source transition for unchanged world-document semantics.
// Never rewrites a stored binding or accepts an arbitrary old digest.
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=new URL('../',import.meta.url),sha=b=>createHash('sha256').update(b).digest('hex');
export async function legacyArtifacts(actual,base){
 let review;try{review=JSON.parse(await readFile(new URL('./legacy-transition.json',import.meta.url),'utf8'));}catch{return actual;}
 if(review.baseline!=='a6563a9b8e761e3190d0ac09cfd373886802516b')return actual;
 for(const [p,h]of Object.entries(review.additions)){
  try{if(sha(await readFile(new URL(p,root)))!==h)return actual;}catch{return actual;}
 }
 const result={...actual};
 for(const [key,h]of Object.entries(actual)){
  const absolute=fileURLToPath(new URL(key,base)).replaceAll('\\','/'),prefix=fileURLToPath(root).replaceAll('\\','/');
  if(!absolute.startsWith(prefix))return actual;
  const relative=absolute.slice(prefix.length),change=review.changes[relative];
  if(change){if(h!==change.after)return actual;result[key]=change.before;}
 }
 return result;
}
