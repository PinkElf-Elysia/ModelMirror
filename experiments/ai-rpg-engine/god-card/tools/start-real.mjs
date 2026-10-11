// Transfer only the existing RPG service credential inside the launcher process.
// Never inspect Provider keys, dump the environment or persist credentials.
import {execFileSync,spawn} from 'node:child_process';
import {readFileSync,existsSync,openSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const repo=fileURLToPath(new URL('../../../../',import.meta.url));
const work=repo+'experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real';
try{
 const auth=JSON.parse(readFileSync(work+'/AUTHORIZATION.json'));
 if(auth.id!=='rpg-god-b4-20261010'||auth.limit!==4||!auth.noRetry)throw Error('AUTHORIZATION_MISMATCH');
 if(existsSync(work+'/launcher-owner.json'))throw Error('ALREADY_STARTED_NO_AUTOMATIC_RESTART');
 const state=JSON.parse(execFileSync('docker',['inspect','--format','{{json .State.Running}}','modelmirror-server'],{windowsHide:true,encoding:'utf8'}));
 if(!state)throw Error('CONTROL_SERVER_STOPPED');
 const bytes=execFileSync('docker',['exec','modelmirror-server','python','-B','-c',"import os,sys; t=os.environ.get('RPG_S2S_TOKEN',''); assert os.environ.get('RPG_S2S_ENABLED','').lower()=='true' and len(t)>=32; sys.stdout.write(t)"],{windowsHide:true,stdio:['ignore','pipe','pipe']});
 const token=bytes.toString('utf8');bytes.fill(0);
 if(token.length<32||token.length>1024)throw Error('CONTROL_TOKEN_UNAVAILABLE');
 const env={...process.env,RPG_S2S_TOKEN:token},log=openSync(work+'/host-private.log','a');
 const processHandle=spawn(process.execPath,[repo+'experiments/ai-rpg-engine/god-card/tools/real-preview.mjs'],{cwd:repo,env,windowsHide:true,detached:true,stdio:['ignore',log,log]});
 processHandle.unref();
 writeFileSync(work+'/launcher-owner.json',JSON.stringify({pid:processHandle.pid,backend:18511,ui:18512,source:'god-card/tools/real-preview.mjs',control:'http://127.0.0.1:8000',authorizedCalls:4,credentialsPersisted:false},null,2),{flag:'wx'});
 console.log(JSON.stringify({started:true,pid:processHandle.pid,providerCalls:0}));
}catch(e){console.error(JSON.stringify({error:/^[A-Z_]+$/.test(e.message)?e.message:'START_FAILED',noRetry:true,secretsPrinted:false}));process.exitCode=1;}
