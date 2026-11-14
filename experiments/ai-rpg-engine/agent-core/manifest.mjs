import {readFile,readdir} from 'node:fs/promises';
import {resolve,dirname,relative,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {hash,need} from './contracts.mjs';
// Follow actual static module dependencies; resources are declared by trusted composition.
export async function runtimeManifest(entries,resources=[]){
 const root=fileURLToPath(new URL('../',import.meta.url)),files=new Map();
 async function visit(path){
  path=resolve(path);need(path.startsWith(root),'CORE_DEPENDENCY_OUTSIDE');const name=relative(root,path).split(sep).join('/');
  if(files.has(name))return;const data=await readFile(path);files.set(name,hash(data.toString('base64')));
  if(path.endsWith('.mjs'))for(const match of data.toString().matchAll(/(?:from\s*|import\s*)['"]([^'"]+)['"]/g)){
   if(match[1].startsWith('.'))await visit(resolve(dirname(path),match[1]));
  }
 }
 async function resource(path){const rows=await readdir(path,{withFileTypes:true});for(const row of rows){if(['node_modules','.git','.rpg03-work'].includes(row.name))continue;const p=resolve(path,row.name);if(row.isDirectory())await resource(p);else if(!row.name.endsWith('.test.mjs'))await visit(p);}}
 for(const entry of entries)await visit(fileURLToPath(entry));
 for(const entry of resources){if(entry.directory)await resource(fileURLToPath(entry.url));else await visit(fileURLToPath(entry.url));}
 const manifest=Object.fromEntries([...files].sort(([a],[b])=>a.localeCompare(b)));return {files:manifest,hash:hash(manifest)};
}
