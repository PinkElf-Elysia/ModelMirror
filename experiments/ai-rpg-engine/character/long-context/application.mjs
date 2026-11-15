import {join} from 'node:path';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {DailyWorkspace} from '../daily/workspace.mjs';
import {GroupStore} from '../group/store.mjs';import {GroupHost,GroupGateway} from '../group/host.mjs';import {startGroupServer} from '../group/server.mjs';
import {openContextControl} from './composition.mjs';import {attachControlledRevisions} from '../revisions/composition.mjs';import {startRevisionServer} from '../revisions/server.mjs';import {attachContext} from './attach.mjs';import {startContextServer} from './server.mjs';
// No environment-driven real-call enable switch. The trusted caller must supply
// frozen models, prices and authorization; default authorization is zero.
export async function startContextWorkspace({directory,upstream,serviceToken,models,authorization={id:'disabled',calls:0,microUsd:0,initial:{calls:0,microUsd:0}},port=43280,recoverDeadOwner=false,executionLabel='受控生成',timeoutMs=250000,legacyBudgetDirectory=null,legacyBudgetPolicy=null}){
 const context=await openContextControl({directory:join(directory,'control-r4a'),upstream,serviceToken,models,authorization,recoverDeadOwner,timeoutMs,legacyDirectory:legacyBudgetDirectory,legacyPolicy:legacyBudgetPolicy});
 const workspace=await DailyWorkspace.open(directory,{recoverDeadOwner});
 const studio=await startAuthoringServer({directory:join(directory,'studio'),port,recoverDeadOwner,creatorExperience:true,characterSettings:{provider:context.single,control:context.control,limit:authorization.calls-authorization.initial.calls,workspace,groupsUrl:'http://127.0.0.1:'+(port+1)+'/groups'}});await fetch(studio.origin+'/characters');
 const groupStore=await GroupStore.open({directory:join(directory,'groups'),works:studio.works,models,authorization:{id:authorization.id,mode:'controlled',calls:authorization.calls-authorization.initial.calls,microUsd:authorization.microUsd-authorization.initial.microUsd},recoverDeadOwner});await groupStore.recover();
 const groupHost=new GroupHost(groupStore,new GroupGateway(context.group)),groups=await startGroupServer({store:groupStore,works:studio.works,host:groupHost,port:port+1,charactersUrl:studio.origin+'/characters',workspace});workspace.groupUrl=groups.origin+'/groups';
 const revisions=await attachControlledRevisions({context,workspace,singleDirectory:join(directory,'studio','character','sessions'),groupDirectory:join(directory,'groups'),directory:join(directory,'revisions-v2'),groupHost,recoverDeadOwner});
 const page=await startRevisionServer({...revisions,port:port+2,charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups',budgetGuard:context.guard,executionLabel});
 const longContext=await attachContext({directory:join(directory,'long-context'),context,revisions,workspace,groupHost,recoverDeadOwner}),longPage=await startContextServer({...longContext,port:port+3,charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups',revisionsUrl:page.origin+'/revisions',budgetGuard:context.guard,executionLabel});
 return {context,workspace,studio,groupStore,groupHost,revisions,page,longContext,longPage,async close(){await longPage.close();await longContext.close();await page.close();await revisions.close();await groups.close();await groupStore.close();await studio.close();await workspace.close();await context.close();}};
}
