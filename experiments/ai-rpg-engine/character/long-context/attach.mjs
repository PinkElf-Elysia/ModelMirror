import {ContextStore} from './store.mjs';import {Sources} from './sources.mjs';import {RevisionHost} from '../revisions/host.mjs';import {check} from '../revisions/contracts.mjs';
export async function attachContext({directory,context,revisions,workspace,groupHost,recoverDeadOwner=false}){
 check(context.parameters&&context.guard,'R4_CONTROL_REQUIRED');const sources=new Sources(revisions.sources,revisions.store),a=context.authorization;
 const store=await ContextStore.open({directory,sources,parameters:context.parameters,authorization:{id:a.id,mode:'controlled',calls:a.calls,microUsd:a.microUsd},recoverDeadOwner}),host=new RevisionHost(store,context.revision),coordinator=revisions.coordinator;
 const single=workspace.single.send.bind(workspace.single),group=groupHost.send.bind(groupHost),generate=revisions.host.generate.bind(revisions.host);
 revisions.host.generate=async(fid,input)=>{const f=await revisions.store.read(fid);await store.guardLegacy(f.source.kind,f.source.id);return generate(fid,input);};
 // Guard before the existing coordinator, which serializes old and new prepares.
 // SharedBudgetGuard is the final atomic authority even if two UIs race here.
 workspace.single.send=async(id,input)=>{await store.guardLegacy('single',id);return single(id,input);};groupHost.send=async(id,input)=>{await store.guardLegacy('group',id);return group(id,input);};
 return {store,host,sources,coordinator,async close(){workspace.single.send=single;groupHost.send=group;revisions.host.generate=generate;await store.close();}};
}
