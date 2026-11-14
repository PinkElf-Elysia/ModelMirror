// Generate only neutral test data; never depend on local character material or saved sessions.
import {execFileSync} from 'node:child_process';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
export const baseline='a6563a9b8e761e3190d0ac09cfd373886802516b';
export async function baselineFixture(){
 const engine=fileURLToPath(new URL('../',import.meta.url)),parent=join(engine,'.rpg04-work','character-baseline');
 await mkdir(parent,{recursive:true});const directory=await mkdtemp(join(parent,'run-')),source=join(directory,'source');await mkdir(source);
 const archive=join(directory,'baseline.tar'),prefix='experiments/ai-rpg-engine/';
 execFileSync('git',['archive','--format=tar','--output='+archive,baseline,...['authoring','plugins','content','runtime','src','context','studio','package.json','package-lock.json'].map(p=>prefix+p)],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),stdio:'pipe'});
 execFileSync('tar',['-xf',archive,'-C',source],{stdio:'pipe'});
 return {directory,root:pathToFileURL(join(source,prefix,'authoring')+'/')};
}
