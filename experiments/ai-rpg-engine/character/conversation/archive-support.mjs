// Generates only neutral test archives from exact repository commits.
import {execFileSync} from 'node:child_process';import {mkdir,mkdtemp} from 'node:fs/promises';import {join,resolve} from 'node:path';import {tmpdir} from 'node:os';import {fileURLToPath,pathToFileURL} from 'node:url';
const engine=fileURLToPath(new URL('../../',import.meta.url)),repository=fileURLToPath(new URL('../../../../',import.meta.url)),cache=new Map();
export async function baselineFixture(baseline='3fffd5b49a94a73cbf00ac409a0d9b06308d34c7'){
 if(!cache.has(baseline))cache.set(baseline,(async()=>{
  const parent=join(engine,'.rpg04-work','character-r1-archives');await mkdir(parent,{recursive:true});const source=await mkdtemp(join(parent,'source-')),archive=join(source,'baseline.tar'),prefix='experiments/ai-rpg-engine/';
  const dirs=['authoring','plugins','content','runtime','src','context','studio','package.json','package-lock.json'];if(baseline.startsWith('af3beb63')||baseline.startsWith('0cb019df'))dirs.push('character');
  execFileSync('git',['archive','--format=tar','--output='+archive,baseline,...dirs.map(p=>prefix+p)],{cwd:repository,stdio:'pipe'});execFileSync('tar',['-xf',archive,'-C',source],{stdio:'pipe'});
  return pathToFileURL(join(source,prefix,'authoring')+'/');
 })());
 const base=resolve(process.env.CHARACTER_R1_TEST_ROOT||join(tmpdir(),'mm-cr1-tests'));await mkdir(base,{recursive:true});return {root:await cache.get(baseline),directory:await mkdtemp(join(base,'archive-'))};
}
