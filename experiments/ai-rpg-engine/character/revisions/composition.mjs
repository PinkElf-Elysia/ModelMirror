import {join} from 'node:path';
import {SharedBudgetGuard} from './shared-budget.mjs';
import {ControlledProvider as SingleTransport} from '../../authoring/controlled-provider.mjs';
import {ControlledProvider as GroupTransport} from '../group/gateway-transport.mjs';
import {ControlPlaneModelControl} from '../../authoring/model-control.mjs';
import {RevisionControlled} from './controlled.mjs';
import {RevisionStore} from './store.mjs';import {RevisionHost} from './host.mjs';import {RevisionCoordinator} from './coordinator.mjs';import {LegacySources} from './sources.mjs';import {check,digest} from './contracts.mjs';
// Trusted application composition, never populated from browser JSON or env defaults.
// Opening this context sends no request. Existing transports all use guard.origin.
export async function openControlledContext({directory,upstream,serviceToken,models,authorization,recoverDeadOwner=false,timeoutMs=250000}){
 check(authorization&&authorization.calls>=0,'AUTHORIZATION_REQUIRED');
 const guard=await SharedBudgetGuard.open({directory:join(directory,'budget'),upstream,serviceToken,models,authorization,recoverDeadOwner,timeoutMs});await guard.start();
 const control=new ControlPlaneModelControl({origin:guard.origin,serviceToken:guard.serviceToken});
 const single=await SingleTransport.open({directory:join(directory,'single-wire'),origin:guard.origin,serviceToken:guard.serviceToken,recoverDeadOwner,timeoutMs});
 const group=await GroupTransport.open({directory:join(directory,'group-wire'),origin:guard.origin,serviceToken:guard.serviceToken,recoverDeadOwner,timeoutMs});
 return {guard,control,single,group,revision:new RevisionControlled({single,group,control,guard}),models,authorization,
  async close(){await single.close();await group.close();await guard.close();}};
}
export async function attachControlledRevisions({context,workspace,singleDirectory,groupDirectory,directory,groupHost,recoverDeadOwner=false}){
 const {authorization,models,guard}=context,limit={calls:authorization.calls-authorization.initial.calls,microUsd:authorization.microUsd-authorization.initial.microUsd};
 const sources=new LegacySources({workspace,singleDirectory,groupDirectory,models,singleBudget:limit});
 const store=await RevisionStore.open({directory,sources,models,authorization:{id:authorization.id,mode:'controlled',...limit},recoverDeadOwner}),host=new RevisionHost(store,context.revision),coordinator=new RevisionCoordinator(store,sources);
 const register=async(kind,id)=>{if(await guard.registered(id))return;const s=await sources.read(kind,id);await guard.register(id,s.budget);};
 // Register existing sources once, before they can send through this composition.
 for(const source of await sources.list())await register(source.kind,source.id);
 const createSingle=workspace.single.create.bind(workspace.single),createGroup=workspace.group.create.bind(workspace.group),sendSingle=workspace.single.send.bind(workspace.single),sendGroup=groupHost.send.bind(groupHost),configureSingle=workspace.single.configure.bind(workspace.single),listModels=workspace.single.models.bind(workspace.single);
 workspace.single.models=async()=>{const catalog=await listModels();return {...catalog,models:catalog.models.filter(entry=>models.some(m=>m.choice.entry.selectionId===entry.selectionId))};};
 workspace.single.configure=async(id,input)=>{if(input.selection!==null){await context.control.refresh();const choice=await context.control.select(input.selection);check(models.some(m=>digest(m.choice)===digest(choice)),'MODEL_UNAVAILABLE');}return configureSingle(id,input);};
 workspace.single.create=async input=>{const result=await createSingle(input);await register('single',result.id);return result;};
 workspace.group.create=async input=>{const result=await createGroup(input);await register('group',result.id);return result;};
 workspace.single.send=(id,input)=>coordinator.legacy('single',id,()=>sendSingle(id,input));groupHost.send=(id,input)=>coordinator.legacy('group',id,()=>sendGroup(id,input));
 return {store,host,sources,coordinator,context,async close(){workspace.single.models=listModels;workspace.single.configure=configureSingle;workspace.single.create=createSingle;workspace.group.create=createGroup;workspace.single.send=sendSingle;groupHost.send=sendGroup;await store.close();}};
}
