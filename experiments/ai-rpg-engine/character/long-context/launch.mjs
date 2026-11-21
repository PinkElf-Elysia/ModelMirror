import {characterDocument} from '../document.mjs';import {blankDocument} from '../../authoring/contracts.mjs';
import {ContextStore} from './store.mjs';import {Sources} from './sources.mjs';import {Parameters} from './parameters.mjs';import {startContextServer} from './server.mjs';
import {resolve,join} from 'node:path';import {mkdir} from 'node:fs/promises';
import {startAuthoringServer} from '../../authoring/server.mjs';import {DailyWorkspace} from '../daily/workspace.mjs';import {GroupStore} from '../group/store.mjs';import {GroupHost} from '../group/host.mjs';import {GroupFixtureProvider} from '../group/fixture-provider.mjs';import {startGroupServer} from '../group/server.mjs';
import {LegacySources,fixtureModel} from '../revisions/sources.mjs';import {RevisionStore} from '../revisions/store.mjs';import {RevisionFixture,RevisionHost} from '../revisions/host.mjs';import {RevisionCoordinator} from '../revisions/coordinator.mjs';import {startRevisionServer} from '../revisions/server.mjs';
const dir=resolve(process.argv[2]??'C:/tmp/mm-cr4a-data/preview'),port=Number(process.argv[3]??43240),groupPort=port+1,revisionPort=port+2;await mkdir(dir,{recursive:true});const recoverDeadOwner=process.argv.includes('--recover-dead-owner');
const workspace=await DailyWorkspace.open(dir,{recoverDeadOwner});const studio=await startAuthoringServer({directory:join(dir,'studio'),port,recoverDeadOwner,creatorExperience:true,characterSettings:{workspace,groupsUrl:'http://127.0.0.1:'+groupPort+'/groups'}});await fetch(studio.origin+'/characters');
const store=await GroupStore.open({directory:join(dir,'groups'),works:studio.works,models:[fixtureModel()],authorization:{id:'r3a-offline',mode:'fixture',calls:100,microUsd:10000000},recoverDeadOwner});await store.recover();
const provider=await GroupFixtureProvider.open(join(dir,'fixture'),{recoverDeadOwner}),groupHost=new GroupHost(store,provider),groups=await startGroupServer({store,works:studio.works,host:groupHost,port:groupPort,charactersUrl:studio.origin+'/characters',workspace});workspace.groupUrl=groups.origin+'/groups';
// Deterministic neutral tutorials in new sessions only, never formal materials.
if(!(await studio.works.list()).length){for(const [index,name] of ['茶亭旅人（中性夹具）','观星者（中性夹具）'].entries()){const document=characterDocument(blankDocument());Object.assign(document.character.card.data,{name,description:'原创中性测试角色，愿意聆听玩家，不代替他人行动。',personality:'温和、谨慎',scenario:'茶亭',first_mes:'你好，我们先商量下一步。'});document.metadata.name=name;const w=await studio.works.create({operationId:'r4-neutral-'+index,expectedRevision:null,document});await studio.works.publish(w.workId,{operationId:'r4-version-'+index,expectedRevision:0});}}
const refs=[];for(const w of await studio.works.list()){const v=(await studio.works.read(w.id)).versions.at(-1);if(v)refs.push({workId:w.id,versionId:v.id??v.versionId});}
if(refs.length>=2){
 const player={id:null,name:'续写体验员（中性夹具）'},s=await workspace.single.create({operationId:'r3b-tutorial-single',reference:refs[0],openingIndex:0,player});
 if(!s.turns){await workspace.single.send(s.id,{operationId:'tutorial-turn',expectedRevision:s.revision,input:'我提议先坐在茶亭里聊一聊。'});await workspace.single.wait(s.id);}
 const created=await store.create({operationId:'r3b-tutorial-group',expectedRevision:null,payload:{player,scene:'河边茶亭，大家还在商量下一步。',members:refs.slice(0,2).map(reference=>({reference,modelId:'fixture'})),opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:12,microUsd:100000}}});const g=await store.read(created.id);
 if(process.argv.includes('--manual-memory') && g.messages.length===1){
   const away=g.members[2].id;
   const change=async action=>store.change(g.id,action,{operationId:'demo-'+action,expectedRevision:(await store.read(g.id)).revision,payload:{memberId:away}});
   await change('leave');
   await groupHost.send(g.id,{operationId:'demo-location',expectedRevision:(await store.read(g.id)).revision,payload:{targetId:g.members[1].id,input:'我们约好在西堤集合。'}});
   await change('rejoin');
 }else if(g.messages.length===1)await groupHost.send(g.id,{operationId:'tutorial-turn',expectedRevision:g.revision,payload:{targetId:g.members[1].id,input:'我提议去河边看看，还没有出发。'}});
}
const sources=new LegacySources({workspace,singleDirectory:join(dir,'studio','character','sessions'),groupDirectory:join(dir,'groups')}),revisions=await RevisionStore.open({directory:join(dir,'revisions'),sources,recoverDeadOwner}),fixture=await RevisionFixture.open(join(dir,'revision-fixture'),{recoverDeadOwner}),host=new RevisionHost(revisions,fixture),coordinator=new RevisionCoordinator(revisions,sources);
// The trusted composition gate serializes old sends and revision operations.
const originalSingle=workspace.single.send.bind(workspace.single),originalGroup=groupHost.send.bind(groupHost);
workspace.single.send=(id,input)=>coordinator.legacy('single',id,()=>originalSingle(id,input));groupHost.send=(id,input)=>coordinator.legacy('group',id,()=>originalGroup(id,input));
const page=await startRevisionServer({store:revisions,host,sources,coordinator,port:revisionPort,charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups'});
const contextSources=new Sources(sources,revisions),contextStore=await ContextStore.open({directory:join(dir,'long-context'),sources:contextSources,parameters:new Parameters({}),recoverDeadOwner}),contextHost=new RevisionHost(contextStore,fixture);
workspace.single.send=(id,input)=>coordinator.run(async()=>{await contextStore.guardLegacy('single',id);await revisions.guardLegacy('single',id);return originalSingle(id,input);});groupHost.send=(id,input)=>coordinator.run(async()=>{await contextStore.guardLegacy('group',id);await revisions.guardLegacy('group',id);return originalGroup(id,input);});
const oldGenerate=host.generate.bind(host);host.generate=async(fid,input)=>{const f=await revisions.read(fid);await contextStore.guardLegacy(f.source.kind,f.source.id);return oldGenerate(fid,input);};
const enableMemory=process.argv.includes('--manual-memory');
const memoryUrl=enableMemory?'http://127.0.0.1:'+(port+4)+'/revisions':null;
const longPage=await startContextServer({store:contextStore,host:contextHost,sources:contextSources,coordinator,memoryUrl,port:port+3,charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups',revisionsUrl:page.origin+'/revisions'});
let manual=null;
if(enableMemory){
 const {attachMemory}=await import('../manual-memory/attach.mjs');
 for(const s of (await contextSources.list()).filter(s=>s.kind==='group'||s.kind==='single')){
   await contextStore.importSource({operationId:'memory-tutorial-'+s.id,expectedRevision:null,reference:s.reference});
 }
 manual=await attachMemory({directory:join(dir,'manual-memory'),previous:{store:contextStore,host:contextHost},
   parameters:new Parameters({}),provider:fixture,coordinator,lower:contextSources,recoverDeadOwner,
   legacyHosts:[{obj:workspace.single,method:'send',source:id=>({kind:'single',id})},
     {obj:groupHost,method:'send',source:id=>({kind:'group',id})},
     {obj:host,method:'generate',source:async id=>(await revisions.read(id)).source}],
   charactersUrl:studio.origin+'/characters',groupsUrl:groups.origin+'/groups',revisionsUrl:longPage.origin+'/revisions',port:port+4});
}
console.log(JSON.stringify({memoryUrl,context:longPage.origin+'/revisions',characters:studio.origin+'/characters?revisions='+encodeURIComponent(longPage.origin+'/revisions'),groups:groups.origin+'/groups?revisions='+encodeURIComponent(longPage.origin+'/revisions'),revisions:page.origin+'/revisions',realCalls:0,mode:'fixture'}));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await manual?.close();await longPage.close();await contextStore.close();await page.close();await revisions.close();await fixture.close();await groups.close();await store.close();await provider.close();await studio.close();await workspace.close();process.exit(0);});
