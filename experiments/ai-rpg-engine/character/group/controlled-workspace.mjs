import {join} from 'node:path';
import {startLiveWorkspace} from '../live-memory/workspace.mjs';
import {RoundControl} from './round-control.mjs';
import {RoundHost} from './round-host.mjs';

// The shared host is fully composed (including branch guards) before rounds attach.
export async function startRoundWorkspace(options){
  const app=await startLiveWorkspace(options);
  if(!options.roundPolicy)return app;
  let rounds,control;
  try{
    control=new RoundControl(app,options.roundPolicy,options.priceEvidence);
    rounds=await RoundHost.open({directory:join(options.directory,'controlled-rounds'),store:app.groupStore,
      control,recoverDeadOwner:options.recoverDeadOwner});
    control.attach();
    app.workspace.attachControlledRounds(rounds);
    app.workspace.roundExecutionLabel=options.executionLabel;
    return {...app,rounds,async close(){
      app.workspace.attachControlledRounds(null);
      try{await rounds.close();}finally{control.detach();await app.close();}
    }};
  }catch(error){control?.detach?.();await rounds?.close();await app.close();throw error;}
}
