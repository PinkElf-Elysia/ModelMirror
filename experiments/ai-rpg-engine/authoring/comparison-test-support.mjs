import {spawn} from 'node:child_process';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createInterface} from 'node:readline';
export async function formalComparisonControl(t,{advanced=false}={}){
 const base=process.platform==='win32'?'C:/tmp/c1b-fixtures':fileURLToPath(new URL('../.rpg04-work/c1b-control/',import.meta.url));await mkdir(base,{recursive:true});const directory=await mkdtemp(join(base,'db-'));
 const child=spawn('python',['-m','server.tests.rpg_comparison_fixture',directory,...(advanced?['--advanced']:[])],{cwd:fileURLToPath(new URL('../../../',import.meta.url)),windowsHide:true,stdio:['ignore','pipe','pipe']});let errors='';child.stderr.on('data',b=>{errors+=b;});
 const ready=await new Promise((yes,no)=>{const timer=setTimeout(()=>{child.kill();no(Error('fixture startup timeout '+errors));},30000);child.once('exit',c=>{clearTimeout(timer);no(Error('fixture exit '+c+' '+errors));});createInterface({input:child.stdout}).on('line',line=>{try{const r=JSON.parse(line);if(r.origin){clearTimeout(timer);yes(r);}}catch{}});});
 t.after(()=>new Promise(done=>{if(child.exitCode!==null)return done();child.once('exit',done);child.kill();}));return {...ready,directory,fault:async value=>{const r=await fetch(ready.origin+'/__fault',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(value)});if(!r.ok)throw Error('fixture fault failed');},evidence:async()=>{const r=await fetch(ready.origin+'/__evidence');return r.json();}};
}
