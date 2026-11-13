import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
import {startAuthoringServer} from './server.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {ControlPlaneModelControl} from './model-control.mjs';

// Trusted host configuration only. The existing control owns qualification,
// endpoint selection and the independent provider-side allowance.
export async function startControlledWorkbench({directory,port=0,limit=0,origin,serviceToken,recoverDeadOwner=false,comparison=false}){
  if(typeof directory!=='string'||!directory.trim()||!Number.isInteger(port)||port<0||port>65535||
      !Number.isInteger(limit)||limit<0||limit>100000)throw new Error('AUTHORING_HOST_OPTIONS');
  const root=resolve(directory);
  const control=new ControlPlaneModelControl({origin,serviceToken});
  await control.refresh();
  const provider=await ControlledProvider.open({directory:join(root,'transport'),origin,serviceToken,recoverDeadOwner});
  try{
    const host=await startAuthoringServer({directory:join(root,'works'),port,recoverDeadOwner,creatorExperience:true,
      modelSettings:{directory:join(root,'sessions'),control,provider},historySettings:{directory:join(root,'history')},
      jointSettings:{directory:join(root,'joint'),allowance:{id:'creator-workbench',limit}},playbackSettings:{directory:join(root,'display')},...(comparison?{comparisonSettings:{directory:join(root,'comparison'),origin,serviceToken}}:{})});
    return {origin:host.origin,async close(){try{await host.close();}finally{await provider.close();}}};
  }catch(error){await provider.close();throw error;}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const {values}=parseArgs({options:{directory:{type:'string'},port:{type:'string'},limit:{type:'string'},
    comparison:{type:'boolean',default:false},'recover-dead-owner':{type:'boolean',default:false}}});
  try{
    const numeric=value=>/^(0|[1-9]\d*)$/.test(value??'0')?Number(value??'0'):NaN;
    const workbench=await startControlledWorkbench({directory:values.directory,port:numeric(values.port),limit:numeric(values.limit),
      comparison:values.comparison,recoverDeadOwner:values['recover-dead-owner'],origin:process.env.RPG_AUTHORING_CONTROL_ORIGIN??'http://127.0.0.1:8000',
      serviceToken:process.env.RPG_S2S_TOKEN});
    console.log(JSON.stringify({url:workbench.origin,mode:'controlled'}));
    let closing=false;
    for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{
      if(closing)return;closing=true;
      try{await workbench.close();}catch{process.exitCode=1;}
    });
  }catch(error){
    const code=error?.code??error?.message;
    console.error(/^[A-Z][A-Z0-9_]{1,90}$/.test(code??'')?code:'AUTHORING_HOST_START_FAILED');process.exitCode=1;
  }
}
