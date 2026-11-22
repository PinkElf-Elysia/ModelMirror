import {join} from 'node:path';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {DailyWorkspace} from '../daily/workspace.mjs';
import {GroupStore} from '../group/store.mjs';
import {GroupHost,GroupGateway} from '../group/host.mjs';
import {startGroupServer} from '../group/server.mjs';
import {attachControlledRevisions} from '../revisions/composition.mjs';
import {startRevisionServer} from '../revisions/server.mjs';
import {attachContext} from '../long-context/attach.mjs';
import {startContextServer} from '../long-context/server.mjs';
import {openContextControl} from './composition.mjs';

export const portAt=(base,offset)=>base===0?0:(base??43280)+offset;

// Use the same budget owner at every entry. Port zero lets the OS choose each
// listener independently; failures unwind resources acquired by this workspace.
export async function startLiveBase({directory,upstream,serviceToken,models,
  authorization={id:'disabled',calls:0,microUsd:0,initial:{calls:0,microUsd:0}},
  port=43280,recoverDeadOwner=false,executionLabel='受控生成',timeoutMs=250000,
  legacyBudgetDirectory=null,legacyBudgetPolicy=null}){
  const resources=[];
  const own=x=>{resources.unshift(x);return x;};
  async function close(){const errors=[];for(const x of resources.splice(0)){
    try{await x.close();}catch(e){errors.push(e);}
  }if(errors.length)throw new AggregateError(errors,'工作台资源关闭失败');}
  try{
    const context=own(await openContextControl({directory:join(directory,'control-r4a'),upstream,serviceToken,models,
      authorization,recoverDeadOwner,timeoutMs,legacyDirectory:legacyBudgetDirectory,legacyPolicy:legacyBudgetPolicy}));
    const workspace=own(await DailyWorkspace.open(directory,{recoverDeadOwner}));
    const available=authorization.calls-authorization.initial.calls;
    const characterSettings={provider:context.single,control:context.control,limit:available,workspace};
    const studio=own(await startAuthoringServer({directory:join(directory,'studio'),port,recoverDeadOwner,
      creatorExperience:true,characterSettings}));
    const charactersUrl=studio.origin+'/characters';
    const groupStore=own(await GroupStore.open({directory:join(directory,'groups'),works:studio.works,models,
      authorization:{id:authorization.id,mode:'controlled',calls:available,microUsd:authorization.microUsd-authorization.initial.microUsd},recoverDeadOwner}));
    await groupStore.recover();
    const groupHost=new GroupHost(groupStore,new GroupGateway(context.group));
    const groups=own(await startGroupServer({store:groupStore,works:studio.works,host:groupHost,port:portAt(port,1),charactersUrl,workspace}));
    const groupsUrl=groups.origin+'/groups';
    characterSettings.groupsUrl=workspace.groupUrl=groupsUrl;
    await fetch(charactersUrl);
    const revisions=own(await attachControlledRevisions({context,workspace,singleDirectory:join(directory,'studio','character','sessions'),
      groupDirectory:join(directory,'groups'),directory:join(directory,'revisions-v2'),groupHost,recoverDeadOwner}));
    const links={charactersUrl,groupsUrl,budgetGuard:context.guard,executionLabel};
    const page=own(await startRevisionServer({...revisions,...links,port:portAt(port,2)}));
    const longContext=own(await attachContext({directory:join(directory,'long-context'),context,revisions,workspace,groupHost,recoverDeadOwner}));
    const longPage=own(await startContextServer({...longContext,...links,port:portAt(port,3),revisionsUrl:page.origin+'/revisions'}));
    return {context,workspace,studio,groupStore,groupHost,revisions,page,longContext,longPage,close};
  }catch(e){await close();throw e;}
}
