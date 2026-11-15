import test from 'node:test';import assert from 'node:assert/strict';
import {frame,queryFrame,speechMaterial,VERSION} from './projection.mjs';
const p={id:'keeper',configurationHash:'fixed'};
const w={id:'world',revision:3,stateRevision:1,state:{secret:'NEVER_SEND',count:3},binding:{adapterId:'test'},events:[{id:'a',kind:'action',stateRevision:1,receipt:{result:{label:'使用',name:'灯油',quantity:2}}}],scene:{version:VERSION,participants:{keeper:{configurationHash:'fixed',joinedAtVersion:0,grant:{scene:true,witness:true,resources:false}}},entries:[{participantId:'keeper',taskId:'t',input:'问',body:'推测',name:'沈砚'},{participantId:'visitor',taskId:'other',body:'PRIVATE'}]}};
const adapter={sceneFacts:s=>[{name:'值守',value:'值夜'}],resources:s=>[{name:'灯油',quantity:s.count}]};
test('projection excludes hidden state and other participant dialogue; speech stays attributed',()=>{
 const f=frame(w,p,adapter);assert.equal(f.events.length,1);assert.equal(f.dialogue.length,1);assert.equal(f.dialogue[0].kind,'adopted-speech');
 assert.ok(!JSON.stringify(f).includes('NEVER_SEND'));assert.ok(!JSON.stringify(f).includes('PRIVATE'));
 assert.equal(queryFrame(f,{domain:'resources'}).status,'unavailable');assert.equal(speechMaterial(w).kind,'adopted-speech-not-world-fact');
});
test('unknown queries and mismatched instance binding reject',()=>{
 assert.throws(()=>queryFrame(frame(w,p,adapter),{domain:'secret'}),/NATIVE_SCENE_QUERY/);
 assert.throws(()=>queryFrame(frame(w,p,adapter),{domain:'events',worldId:'other'}));
 assert.throws(()=>frame(w,{...p,configurationHash:'changed'},adapter),/SCENE_BINDING/);
});
