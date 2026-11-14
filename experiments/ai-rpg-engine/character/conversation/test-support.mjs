import {mkdir,mkdtemp} from 'node:fs/promises';import {join,resolve} from 'node:path';import {tmpdir} from 'node:os';
import {AuthoringStore} from '../../authoring/store.mjs';import {blankDocument} from '../../authoring/contracts.mjs';import {characterDocument} from '../document.mjs';import {ConversationStore} from './store.mjs';
export async function fixture(t,{fault,fixtureMode=true}={}){
 const base=resolve(process.env.CHARACTER_R1_TEST_ROOT||join(tmpdir(),'mm-cr1-tests'));await mkdir(base,{recursive:true});const directory=await mkdtemp(join(base,'r-'));
 const works=await AuthoringStore.open(join(directory,'works'));const doc=characterDocument(blankDocument());Object.assign(doc.character.card.data,{name:'中性测试夹具',first_mes:'你好 {{user}}',alternate_greetings:['另一开场'],description:'角色定义',mes_example:'EXAMPLE_NOT_HISTORY'});doc.metadata.name='中性测试夹具';
 const w=await works.create({operationId:'w',expectedRevision:null,document:doc}),v=await works.publish(w.workId,{operationId:'v',expectedRevision:0});const reference={workId:w.workId,versionId:v.result.versionId};
 let host=await ConversationStore.open({directory:join(directory,'conversations'),works,fault,fixtureMode});await host.recover();
 t.after(async()=>{await host.close();await works.close();});
 const created=await host.create({operationId:'create',expectedRevision:null,payload:{player:{id:null,name:'玩家'}}});
 const f={directory,works,doc,reference,created,get host(){return host;},async reopen(options={}){await host.close();host=await ConversationStore.open({directory:join(directory,'conversations'),works,fixtureMode,...options});}};
 f.op=async(action,id,payload)=>{const s=await host.readOwner(created.conversationId);return host[action](s.id,{operationId:id,expectedRevision:s.revision,payload});};
 f.join=async id=>(await f.op('join',id,{reference})).memberId;
 f.say=async(id,content,extra={})=>f.op('append',id,{speakerId:created.memberId,content,visibility:{kind:'public'},addressedTo:[],source:{kind:'player-input',reference:null},...extra});return f;
}
