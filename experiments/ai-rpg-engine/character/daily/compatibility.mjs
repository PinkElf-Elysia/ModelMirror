import {readFile} from 'node:fs/promises';
export async function compatible(kind,hash,artifacts){
 const m=JSON.parse(await readFile(new URL('./compatibility.json',import.meta.url),'utf8')),old=m[kind];
 if(hash!==old.hash)return false;
 // Every changed or added source must match the reviewed candidate, every other
 // source must match the exact baseline. No wildcard or persisted hash rewrite.
 return Object.entries(artifacts).every(([p,h])=>p==='character/daily/compatibility.json'||h===(m.approved[p]??old.artifacts[p]))&&Object.keys(old.artifacts).every(p=>p in artifacts);
}
