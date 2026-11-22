import {resolve,join} from 'node:path';
import {mkdir} from 'node:fs/promises';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';
import {characterDocument} from '../document.mjs';
import {GroupStore} from '../group/store.mjs';
import {GroupHost} from '../group/host.mjs';
import {GroupFixtureProvider} from '../group/fixture-provider.mjs';
import {startGroupServer} from '../group/server.mjs';
import {DailyWorkspace} from '../daily/workspace.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {pathToFileURL} from 'node:url';

export async function startPreview(dir,port=0,groupPort=0,bookClock){
  await mkdir(dir,{recursive:true});
  const workspace=await DailyWorkspace.open(dir),groupDirectory=join(dir,'groups');
  const studio=await startAuthoringServer({directory:join(dir,'studio'),port,creatorExperience:true,
    characterSettings:{workspace,bookClock,bookGroupDirectory:groupDirectory,...(groupPort?{groupsUrl:'http://127.0.0.1:'+groupPort+'/groups'}:{})}});
  let store,provider,groups;
  async function close(){await groups?.close();await store?.close();await provider?.close();await studio.close();await workspace.close();}
  try{
    await fetch(studio.origin+'/characters');
    store=await GroupStore.open({directory:groupDirectory,works:studio.works,models:[fixtureModel()],authorization:{id:'books-offline',mode:'fixture',calls:64,microUsd:1000000}});await store.recover();
    provider=await GroupFixtureProvider.open(join(dir,'fixture'));
    groups=await startGroupServer({store,works:studio.works,host:new GroupHost(store,provider),port:groupPort,workspace,charactersUrl:studio.origin+'/characters'});
    const api=async(action,args={})=>{
      const res=await fetch(studio.origin+'/rpg-app/api/authoring/characters/books',{method:'POST',headers:{origin:studio.origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify({action,args})});
      const value=await res.json();if(!res.ok)throw Error(JSON.stringify(value));return value;
    };
    const write=async(action,payload,operationId=crypto.randomUUID())=>api(action,{payload,operationId,expectedRevision:(await api('catalog')).revision});
    if(!(await studio.works.list()).length){
      const entry=(keys,content)=>({keys,content,enabled:true,insertion_order:1,extensions:{}});
      const createBook=async(title,keys,content)=>{
        const r=await write('save',{bookId:null,title,book:{entries:[entry(keys,content)],extensions:{neutral:{revision:1}}}});
        return write('seal',r);
      };
      const publicRef=await createBook('渡口手册',['渡口'],'渡口的铜铃悬在东檐。');
      const privateRef=await createBook('行旅笔记',['渡口'],'私记：木匣内有一枚白棋。');
      const refs=[];
      for(const [i,name] of ['守桥人','记录员'].entries()){
        const doc=characterDocument(blankDocument());doc.metadata.name=name;
        Object.assign(doc.character.card.data,{name,description:'原创中性验收夹具。',first_mes:'你也来渡口看水位吗？',scenario:'渡口木亭'});
        const {workId}=await studio.works.create({operationId:'neutral-'+i,expectedRevision:null,document:doc});
        const {versionId}=await write('role',{workId,workRevision:0,refs:i?[]:[publicRef]});refs.push({workId,versionId});
      }
      const player=await workspace.store.saveProfile({operationId:'neutral-player',expectedRevision:null,payload:{id:null,name:'行旅者',description:'中性夹具',avatar:null}});
      await write('profile',{profileId:player.profileId,profileRevision:player.profileRevision,refs:[privateRef]});
      await workspace.single.create({operationId:'neutral-single',reference:refs[0],openingIndex:0,player});
      const group=await store.create({operationId:'neutral-group',expectedRevision:null,payload:{player,scene:'渡口茶亭',members:refs.map(reference=>({reference,modelId:'fixture'})),opening:{memberIndex:0,index:0,kind:'ordinary'},budget:{calls:20,microUsd:100000}}});
      const info=await api('source',{kind:'group',sourceId:group.id});
      await write('open',{kind:'group',sourceId:group.id,sourceHash:info.sourceHash,session:[{ref:publicRef,readers:'all'}],player:[]});
    }
    return {url:studio.origin+'/characters?books=1',groups:groups.origin+'/groups',api,write,workspace,store,close};
  }catch(e){await close();throw e;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  if(!process.argv[2])throw Error('请输入独立预览数据目录');
  const p=await startPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:p.url,groupUrl:p.groups,mode:'offline-fixture'}));
  for(const sig of ['SIGTERM','SIGINT'])process.once(sig,async()=>{await p.close();process.exit(0);});
}
