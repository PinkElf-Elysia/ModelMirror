// Synthetic offline fixture. Invoked only by the Python MockTransport test.
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {AuthoringStore} from '../authoring/store.mjs';
import {blankDocument,canonical,sha256} from '../authoring/contracts.mjs';
import {ControlledProvider} from '../authoring/controlled-provider.mjs';
import {ControlPlaneModelControl} from '../authoring/model-control.mjs';
import {controlledPayload} from '../authoring/controlled-receipt.mjs';
import {characterDocument} from './document.mjs';
import {CharacterSessionHost} from './session.mjs';
const {directory,...connection}=JSON.parse(process.argv[2]);
assert.equal(connection.serviceToken,'synthetic-rpg-service-token-for-tests-only');
const works=await AuthoringStore.open(join(directory,'works'));
let provider=await ControlledProvider.open({directory:join(directory,'transport'),...connection});
const control=new ControlPlaneModelControl(connection);await control.refresh();
let host=await CharacterSessionHost.open({directory:join(directory,'sessions'),works,provider,control,limit:2});
try{
 const doc=characterDocument(blankDocument());
 Object.assign(doc.character.card.data,{name:'测试夹具',description:'灯塔管理员。',first_mes:'你好，{{user}}。{{char}}在灯塔等你。',mes_example:'示例专用句',creator_notes:'作者说明不可进入模型',post_history_instructions:'角色后置规则：保留玩家选择。'});doc.metadata.name='测试夹具';
 const made=await works.create({operationId:'fixture',expectedRevision:null,document:doc});
 const sealed=await works.publish(made.workId,{operationId:'seal',expectedRevision:0});
 const reference={workId:made.workId,versionId:sealed.result.versionId};
 let s=await host.create({operationId:'first',reference,openingIndex:0,player:{id:null,name:'玩家'}});
 const sibling=await host.create({operationId:'sibling',reference,openingIndex:0,player:{id:null,name:'玩家'}});
 const catalog=control.list(),entry=catalog.models.find(m=>m.available);
 const selection={catalogRevision:catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,parameters:{temperature:0.7,maxOutputTokens:1536}};
 s=await host.configure(s.id,{expectedRevision:s.revision,settings:{memoryEnabled:false,historyTurns:null},selection});
 // World payloads remain byte-identical without the opt-in marker.
 const base={id:'payload',operationId:'payload',sessionId:'payload',kind:'story',modelChoice:control.select(selection),messages:[{role:'system',content:'规则'},{role:'user',content:'输入'}]};base.messagesHash=sha256(canonical(base.messages));
 assert(!Object.hasOwn(controlledPayload(base),'messageContract'));
 assert.throws(()=>controlledPayload({...base,messageContract:'unknown'}),{code:'CONTROL_REQUEST_INVALID'});
 assert.throws(()=>controlledPayload({...base,kind:'memory',messageContract:'modelmirror.rpg.character-messages/1'}),{code:'CONTROL_REQUEST_INVALID'});
 const inputs=['请先在这里休息。','我们刚才约定了什么？'];let firstIntent;
 for(let i=0;i<inputs.length;i++){
  const intent={operationId:'turn-'+i,expectedRevision:s.revision,input:inputs[i]};firstIntent??=intent;
  await host.send(s.id,intent);s=await host.wait(s.id);assert.equal(s.operations[intent.operationId].state,'completed');
 }
 assert.equal(s.turns,2);assert.equal(s.messages.length,5);assert.equal(s.messages.filter(m=>m.content===s.binding.openingText).length,1);
 const before=structuredClone(s);await host.close();await provider.close();
 provider=await ControlledProvider.open({directory:join(directory,'transport'),...connection});
 host=await CharacterSessionHost.open({directory:join(directory,'sessions'),works,provider,control,limit:2});
 assert.deepEqual(await host.read(s.id),before);
 await host.send(s.id,firstIntent);await host.recover(s.id,{operationId:firstIntent.operationId});
 assert.equal(provider.dispatchCount,0);assert.equal((await host.read(sibling.id)).turns,0);
 assert.equal((await host.status()).budget.used,2);
 console.log(JSON.stringify({turns:s.turns,siblingTurns:0,recovered:true}));
}finally{await host.close();await provider.close();await works.close();}
