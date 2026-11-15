import {resolve,join} from 'node:path';
import {mkdir} from 'node:fs/promises';
import {startAuthoringServer} from '../../authoring/server.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';
import {characterDocument} from '../document.mjs';
import {GroupStore} from '../group/store.mjs';import {GroupHost} from '../group/host.mjs';import {GroupFixtureProvider} from '../group/fixture-provider.mjs';import {startGroupServer} from '../group/server.mjs';import {DailyWorkspace} from './workspace.mjs';
// Dedicated offline preview: no credentials, environment model switch or remote transport.
const dir=resolve(process.argv[2]??'C:/tmp/mm-cr3a-data/preview'),port=Number(process.argv[3]??43150),groupPort=Number(process.argv[4]??43151);await mkdir(dir,{recursive:true});
const recoverDeadOwner=process.argv.includes('--recover-dead-owner');
const workspace=await DailyWorkspace.open(dir,{recoverDeadOwner});
const studio=await startAuthoringServer({directory:join(dir,'studio'),port,recoverDeadOwner,creatorExperience:true,characterSettings:{workspace,groupsUrl:'http://127.0.0.1:'+groupPort+'/groups'}});
if(!(await studio.works.list()).length)for(const [i,name] of ['旅人 · 测试夹具','记录员 · 测试夹具'].entries()){const doc=characterDocument(blankDocument());doc.metadata.name=name;Object.assign(doc.character.card.data,{name,description:'原创中性测试夹具，仅供离线页面验收。',first_mes:i?'今天的见闻，想从哪里记起？':'你好，{{user}}。\n\n**河边茶亭**里有一张空桌。',scenario:'河边茶亭'});const w=await studio.works.create({operationId:'neutral-'+i,expectedRevision:null,document:doc});await studio.works.publish(w.workId,{operationId:'seal-'+i,expectedRevision:0});}
await fetch(studio.origin+'/characters');
const model={id:'fixture',label:'离线测试回复',choice:null,contextTokens:32000,outputTokens:512,price:{revision:'fixture',inputMicroUsdPerMillion:100,outputMicroUsdPerMillion:200,overheadTokens:128}};
const store=await GroupStore.open({directory:join(dir,'groups'),works:studio.works,models:[model],authorization:{id:'r3a-offline',mode:'fixture',calls:100,microUsd:10000000},recoverDeadOwner});await store.recover();
const provider=await GroupFixtureProvider.open(join(dir,'fixture'),{recoverDeadOwner}),host=new GroupHost(store,provider),groups=await startGroupServer({store,works:studio.works,host,port:groupPort,charactersUrl:studio.origin+'/characters',workspace});
console.log(JSON.stringify({characters:studio.origin+'/characters',sessions:studio.origin+'/characters?desk=sessions',profiles:studio.origin+'/characters?desk=profiles',groups:groups.origin+'/groups',mode:'fixture',realCalls:0}));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await groups.close();await store.close();await provider.close();await studio.close();await workspace.close();process.exit(0);});
