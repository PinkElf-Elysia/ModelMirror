import test from 'node:test';
import assert from 'node:assert/strict';
import {createPublishedFrames,isPlainPublishedReply} from './published-chat.mjs';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
test('leaving a conversation releases a late display without publishing it',async()=>{
 const task=deferred(),calls=[],states=[];
 const frames=createPublishedFrames({api:(path,body)=>{calls.push({path,body});return path.endsWith('prepare')?task.promise:Promise.resolve({});},changed:s=>states.push(s)});
 const pending=frames.load('one',{sessionId:'session-one',turnNumber:1,device:'desktop'});frames.clear();task.resolve({token:'late',frameUrl:'/frame'});await pending;
 assert.deepEqual(states,[{loading:true}]);assert.deepEqual(calls.map(c=>c.path),['/joint/playback/prepare','/presentations/release']);assert.equal(calls[0].body.role,'assistant');assert.equal(calls[1].body.token,'late');
});
test('switching turns releases the old frame and does not replace the new one with a stale result',async()=>{
 const tasks=[deferred(),deferred()],states=[],released=[];let next=0;
 const frames=createPublishedFrames({api:(path,body)=>path.endsWith('prepare')?tasks[next++].promise:(released.push(body.token),Promise.resolve({})),changed:s=>states.push(s)});
 const first=frames.load('one',{}),second=frames.load('two',{});tasks[1].resolve({token:'new',frameUrl:'/new'});await second;tasks[0].resolve({token:'old',frameUrl:'/old'});await first;
 assert.equal(states.at(-1).frames[1].token,'new');assert.deepEqual(released,['old']);frames.clear();frames.clear();assert.deepEqual(released,['old','new']);
});
test('display preparation failures are recoverable and never dispatch a story request',async()=>{
 const states=[],calls=[],error=Error('display unavailable');
 const frames=createPublishedFrames({api:async path=>{calls.push(path);throw error;},changed:s=>states.push(s)});
 await frames.load('one',{});assert.deepEqual(states,[{loading:true},{loading:false,error}]);assert.deepEqual(calls,['/joint/playback/prepare']);
});

test('plain replies use the native prose area while authored markup stays isolated',()=>{
 assert.equal(isPlainPublishedReply('离线固定回复，没有调用模型。\n第二段。'),true);
 for(const text of ['<div>剧情</div>','**重点**','[线索](url)','https://example.test','- 列表','1. 列表','<script>alert(1)</script>'])assert.equal(isPlainPublishedReply(text),false,text);
});
