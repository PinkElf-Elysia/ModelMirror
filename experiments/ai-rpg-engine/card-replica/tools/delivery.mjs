import {readFile,writeFile,readdir,stat} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {hash} from '../lib/assembly.mjs';
const base=fileURLToPath(new URL('../',import.meta.url));
const sourceOnly=process.argv.includes('--source-only');
async function walk(dir){const out=[];for(const e of await readdir(dir,{withFileTypes:true})){if(['.local','node_modules','coverage','.git'].includes(e.name))continue;const path=join(dir,e.name);if(e.isSymbolicLink())throw Error('Symlink excluded: '+path);if(e.isDirectory())out.push(...await walk(path));else if(e.isFile()&&relative(base,path).replaceAll('\\','/')!=='resources/DELIVERY.json')out.push(path);}return out;}
const paths=await walk(base),rows=await Promise.all(paths.map(async p=>({path:relative(base,p).replaceAll('\\','/'),bytes:(await stat(p)).size,sha256:hash(await readFile(p))})));
const dest=join(base,'resources/DELIVERY.json');
if(process.argv.includes('--check')||process.argv.includes('--check-git')){
 const d=JSON.parse(await readFile(dest,'utf8')),expected=sourceOnly?d.files:[...d.files,...d.build],actual=sourceOnly?rows.filter(x=>!x.path.startsWith('dist/')):rows;
 const bad=expected.filter(r=>!actual.some(x=>x.path===r.path&&x.sha256===r.sha256)).map(x=>x.path);
 const extra=actual.filter(x=>!expected.some(r=>r.path===x.path)).map(x=>x.path);
 if(bad.length||extra.length)throw Error('Delivery drift: '+JSON.stringify({bad,extra}));
 if(process.argv.includes('--check-git')){
  const git=(...args)=>execFileSync('git',args,{cwd:base});
  const prefix=git('rev-parse','--show-prefix').toString().trim();
  const tracked=git('ls-files','-z','--','.').toString().split('\0').filter(Boolean).sort(),wanted=[...d.files.map(x=>x.path),'resources/DELIVERY.json'].sort();
  if(JSON.stringify(tracked)!==JSON.stringify(wanted))throw Error('Tracked publication scope differs');
  for(const p of tracked)if(hash(git('show','HEAD:'+prefix+p))!==hash(await readFile(join(base,p))))throw Error('Committed bytes differ: '+p);
  console.log('PASS committed HEAD '+git('rev-parse','HEAD').toString().trim()+'; '+tracked.length+' source blobs');
 }
 console.log('PASS current delivery '+expected.length+' '+(sourceOnly?'source':'source/build')+' hashes');
}else{
 const status=JSON.parse(await readFile(join(base,'STATUS.json'),'utf8'));
 const data={status:status.status,base:status.base,publicationBase:status.publication?.targetBase,branch:status.branch,generatedAtUtc:new Date().toISOString(),sourceManifest:'MANIFEST.json',previousSnapshot:'.local/publication/accepted-DELIVERY.json',files:rows.filter(x=>!x.path.startsWith('dist/')),build:rows.filter(x=>x.path.startsWith('dist/'))};
 await writeFile(dest,JSON.stringify(data,null,2)+'\n');console.log('Recorded '+rows.length+' hashes; accepted snapshot retained');
}
