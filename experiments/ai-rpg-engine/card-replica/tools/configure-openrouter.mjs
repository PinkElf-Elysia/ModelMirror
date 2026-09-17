import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {inspectCredential,buildComparison,CATALOG} from '../lib/openrouter-config.mjs';
const base=fileURLToPath(new URL('../',import.meta.url));
const dir=join(base,'.local/comparison-openrouter-20260917');
async function main(){
 if(process.argv[2]!=='--prepare')throw Error('PREPARE_ONLY');
 const credential=await inspectCredential();if(!credential.present)throw Error('CREDENTIAL_NOT_CONFIGURED');
 const character=await readFile(join(dir,'character.txt'),'utf8');
 // Public metadata only; no Authorization header and no user payload.
 const r=await fetch(CATALOG,{redirect:'error',signal:AbortSignal.timeout(20000)});
 if(!r.ok)throw Error('PUBLIC_CATALOG_FAILED');
 const catalog=await r.json(),{profile,payload}=buildComparison(character,catalog);
 const ledger=JSON.parse(await readFile(join(base,'resources/CALL_LEDGER.json'),'utf8'));
 if(ledger.limit!==4||ledger.used!==2||ledger.remaining!==2)throw Error('BUDGET_CHANGED');
 await mkdir(dir,{recursive:true});
 await writeFile(join(dir,'catalog.json'),JSON.stringify({at:new Date().toISOString(),url:CATALOG,...catalog},null,2),{flag:'wx'});
 await writeFile(join(dir,'profile.json'),JSON.stringify({...profile,credentialCheck:credential,budget:{used:ledger.used,remaining:ledger.remaining}},null,2),{flag:'wx'});
 await writeFile(join(dir,'prepared-request.json'),JSON.stringify(payload,null,2),{flag:'wx'});
 console.log(JSON.stringify({status:profile.status,model:profile.model,endpoint:profile.endpoint,credential,
  characterTextHash:profile.characterTextHash,providerDispatches:0,used:ledger.used,remaining:ledger.remaining}));
}
main().catch(()=>{console.error('CONFIGURATION_NOT_COMPLETED; no credentials printed; no generation attempted');process.exitCode=1;});
