import {join} from 'node:path';import {check} from '../revisions/contracts.mjs';import {keys} from '../../authoring/contracts.mjs';import {ParameterBudgetGuard} from './budget.mjs';import {Parameters} from './parameters.mjs';import {leaseLegacyBudget} from './legacy-budget.mjs';import {ControlPlaneModelControl} from '../../authoring/model-control.mjs';import {ControlledProvider as SingleTransport} from '../../authoring/controlled-provider.mjs';import {ControlledProvider as GroupTransport} from '../group/gateway-transport.mjs';import {RevisionControlled} from '../revisions/controlled.mjs';
// Trusted host only. An explicit old-budget path transfers exclusive ownership;
// neither browser nor source card can create/raise the task authorization.
export async function openContextControl({directory,upstream,serviceToken,models,authorization={id:'disabled',calls:0,microUsd:0,initial:{calls:0,microUsd:0}},legacyDirectory=null,legacyPolicy=null,recoverDeadOwner=false,timeoutMs=250000}){
 const direct=new ControlPlaneModelControl({origin:upstream,serviceToken}),parameters=new Parameters({models,control:direct,mode:'controlled'});
 check(Boolean(legacyDirectory)===Boolean(legacyPolicy),'LEGACY_POLICY_REQUIRED');
 if(legacyPolicy)keys(legacyPolicy,['models','authorization']);
 // Historical validation always uses the historical policy, never the new grant.
 const legacy=legacyDirectory?await leaseLegacyBudget({directory:legacyDirectory,upstream,serviceToken,models:legacyPolicy.models,authorization:legacyPolicy.authorization,recoverDeadOwner,timeoutMs}):null;
 let guard,single,group;try{guard=await ParameterBudgetGuard.open({directory:join(directory,'budget'),upstream,serviceToken,models,authorization,legacy,recoverDeadOwner,timeoutMs,verifyConfiguration:m=>parameters.validate(m)});await guard.start();
 const control=new ControlPlaneModelControl({origin:guard.origin,serviceToken:guard.serviceToken});parameters.control=control;
 single=await SingleTransport.open({directory:join(directory,'single-wire'),origin:guard.origin,serviceToken:guard.serviceToken,recoverDeadOwner,timeoutMs});group=await GroupTransport.open({directory:join(directory,'group-wire'),origin:guard.origin,serviceToken:guard.serviceToken,recoverDeadOwner,timeoutMs});
 const revision=new RevisionControlled({single,group,control,guard}),preflight=revision.preflight.bind(revision);revision.preflight=async r=>{await preflight(r);await guard.registerConfiguration(r.model);};
 return {guard,control,single,group,revision,parameters,authorization,models,async close(){await single.close();await group.close();await guard.close();await legacy?.close();}};
 }catch(e){await single?.close();await group?.close();await guard?.close();await legacy?.close();throw e;}
}
