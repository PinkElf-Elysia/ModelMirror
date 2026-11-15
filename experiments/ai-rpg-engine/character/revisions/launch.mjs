import {resolve,join} from 'node:path';import {mkdir} from 'node:fs/promises';
import {startAuthoringServer} from '../../authoring/server.mjs';import {DailyWorkspace} from '../daily/workspace.mjs';import {GroupStore} from '../group/store.mjs';import {GroupHost} from '../group/host.mjs';import {GroupFixtureProvider} from '../group/fixture-provider.mjs';import {startGroupServer} from '../group/server.mjs';
import {LegacySources,fixtureModel} from './sources.mjs';import {RevisionStore} from './store.mjs';import {RevisionFixture,RevisionHost} from './host.mjs';import {RevisionCoordinator} from './coordinator.mjs';import {startRevisionServer} from './server.mjs';
const dir=resolve(process.argv[2]??'C:/tmp/mm-cr3b2-data/fixture-preview'),port=Number(process.argv[3]??43160),groupPort=port+1,revisionPort=port+2;await mkdir(dir,{recursive:true});const recoverDeadOwner=process.argv.includes('--recover-dead-owner');
const workspace=await DailyWorkspace.open(dir,{recoverDeadOwner});const studio=await startAuthoringServer({directory:join(dir,'studio'),port,recoverDeadOwner,creatorExperience:true,characterSettings:{workspace,groupsUrl:'http://127.0.0.1:'+groupPort+'/groups'}});await fetch(studio.origin+'/characters');
const store=await GroupStore.open({directory:join(dir,'groups'),works:studio.works,models:[fixtureModel()],authorization:{id:'r3a-offline',mode:'fixture',calls:100,microUsd:10000000},recoverDeadOwner});await store.recover();
const provider=await GroupFixtureProvider.open(join(dir,'fixture'),{recoverDeadOwner}),groupHost=new GroupHost(store,provider),groups=await startGroupServer({store,works:studio.works,host:groupHost,port:groupPort,charactersUrl:studio.origin+'/characters',workspace});workspace.groupUrl=groups.origin+'/groups';
// Deterministic neutral tutorials in new sessions only, never formal materials.
const refs=[];for(const w of await studio.works.list()){const v=(await studio.works.read(w.id)).versions.at(-1);if(v)refs.push({workId:w.id,versionId:v.id??v.versionId});}
if(refs.length>=2){
 const player={id:null,name:'续写体验员（中性夹具）'},s=await workspace.single.create({operationId:'r3b-tutorial-single',reference:refs[0],openingIndex:0,player});
 if(!s.turns){await workspace.single.send(s.id,{operationId:'tutorial-turn',expectedRevision:s.revision,input:'我提议先坐在茶亭里聊一聊。'});await workspace.single.wait(s.id);}
 const created=await store.create({operationId:'r3b-tutorial-group',expectedRevision:null,payload:{player,scene:'河边茶亭，大家还在商量下一步。',members:refs.slice(0,2).map(reference=>({reference,modelId:'fixture'})),opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:12,microUsd:100000}}});const g=await store.read(created.id);
 if(g.messages.length===1)await groupHost.send(g.id,{operationId:'tutorial-turn',expectedRevision:g.revision,payload:{targetId:g.members[1].id,input:'我提议去河边看看，还没有出发。'}});
}
const sources=new LegacySources({workspace,singleDirectory:join(dir,'studio','character','sessions'),groupDirectory:join(dir,'groups')}),revisions=await RevisionStore.open({directory:join(dir,'revisions'),sources,recoverDeadOwner}),fixture=await RevisionFixture.open(join(dir,'revision-fixture'),{recoverDeadOwner}),host=new RevisionHost(revisions,fixture),coordinator=new RevisionCoordinator(revisions,sources);
// The trusted composition gate serializes old sends and revision operations.
const originalSingle=workspace.single.send.bind(workspace.single),originalGroup=groupHost.send.bind(groupHost);
workspace.single.send=(id,input)=>coordinator.legacy('single',id,()=>originalSingle(id,input));groupHost.send=(id,input)=>coordinator.legacy('group',id,()=>originalGroup(id,input));
const page=await startRevisionServer({store:revisions,host,sources,coordinator,port:revisionPort,charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups'});
console.log(JSON.stringify({characters:studio.origin+'/characters?revisions='+encodeURIComponent(page.origin+'/revisions'),groups:groups.origin+'/groups?revisions='+encodeURIComponent(page.origin+'/revisions'),revisions:page.origin+'/revisions',realCalls:0,mode:'fixture'}));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await page.close();await revisions.close();await fixture.close();await groups.close();await store.close();await provider.close();await studio.close();await workspace.close();process.exit(0);});
