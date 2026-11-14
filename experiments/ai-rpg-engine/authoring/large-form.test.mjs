import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {parse} from 'parse5';
import {compilePresentation} from './presentation.mjs';
import {validateScriptEvent} from './sandbox.mjs';
const origin='http://127.0.0.1:19040',token='a'.repeat(48);
function harness(count=600){
 const listeners={},windowListeners={},messages=[];
 const nodes=Array.from({length:count},(_,i)=>({dataset:{authorNode:'n'+(i+2)},tagName:i?'OPTION':'INPUT',value:'',checked:false,selected:false,parentElement:null,
  closest(){return this;},getBoundingClientRect(){return {x:0,y:0,left:0,right:10,top:0,bottom:10,width:10,height:10};}}));
 const document={body:{dataset:{authorNode:'n1'}},hidden:false,activeElement:null,addEventListener(type,fn){(listeners[type]??=[]).push(fn);},
  querySelectorAll(selector){return nodes;},querySelector(selector){const key=selector.match(/data-author-node="([^" ]+)"/)?.[1];return nodes.find(n=>n.dataset.authorNode===key)||null;}};
 const parent={postMessage:m=>messages.push(m)};
 const result=compilePresentation({origin,token,mode:'opening',html:'<input data-author-node="n2">',execution:{sourceHash:'b'.repeat(64),stateHash:'c'.repeat(64),sequence:0}});
 const tree=parse(result.frame);let source;function walk(n){if(n.tagName==='script')source=n.childNodes[0].value;for(const c of n.childNodes||[])walk(c);}walk(tree);
 runInNewContext(source,{document,parent,window:{addEventListener(type,fn){windowListeners[type]=fn;}},performance:{now:()=>0},ResizeObserver:class{observe(){}},requestAnimationFrame(){},setTimeout(){},clearTimeout(){},innerWidth:1200,innerHeight:800,devicePixelRatio:1});
 const input=()=>{for(const fn of listeners.input)fn({type:'input',target:nodes[0],isTrusted:true,key:'',preventDefault(){}});};
 const ack=()=>{const message=messages.filter(m=>m.type==='author-event').at(-1);windowListeners.message({source:parent,origin,data:{protocol:'modelmirror.rpg.presentation/1',token,type:'patch',sequence:message.payload.sequence,payload:{unchanged:true,platform:{effects:[],layoutRequests:[],scheduled:false,clock:0}}}});};
 return {nodes,messages,input,ack,events:()=>messages.filter(m=>m.type==='author-event')};
}
test('600 options do not exhaust the 128-change event budget',()=>{
 const h=harness();h.nodes[0].value='姓名';h.input();
 assert.equal(h.events().length,1);const event=JSON.parse(JSON.stringify(h.events()[0].payload.event));assert.deepEqual(event.controls,[{key:'n2',value:'姓名',checked:false}]);validateScriptEvent(event);assert.ok(!h.messages.some(m=>m.type==='script-client-error'));
});
test('input edited while a patch is in flight is retained and sent next',()=>{
 const h=harness();h.nodes[0].value='甲';h.input();h.nodes[0].value='甲乙';h.input();assert.equal(h.events().length,1);h.ack();assert.equal(h.nodes[0].value,'甲乙');assert.equal(h.events().length,2);assert.equal(h.events()[1].payload.event.controls[0].value,'甲乙');h.ack();h.input();assert.equal(h.events()[2].payload.event.controls.length,0);
});
test('option selection changes are synchronized, untouched options are omitted',()=>{
 const h=harness();h.nodes[4].selected=true;h.nodes[5].selected=true;h.input();assert.deepEqual(JSON.parse(JSON.stringify(h.events()[0].payload.event.controls)),[{key:'n6',selected:true},{key:'n7',selected:true}]);
});
test('a genuinely oversized change still stops before dispatch',()=>{
 const h=harness();for(let i=1;i<=129;i++)h.nodes[i].selected=true;h.input();assert.equal(h.events().length,0);assert.ok(h.messages.some(m=>m.type==='script-client-error'));
});

test('changing a radio selection synchronizes the unchecked sibling',()=>{
 const h=harness();for(const i of [1,2])Object.assign(h.nodes[i],{tagName:'INPUT',value:String(i),checked:i===1});
 h.input();h.ack();h.nodes[1].checked=false;h.nodes[2].checked=true;h.input();
 assert.deepEqual(JSON.parse(JSON.stringify(h.events()[1].payload.event.controls)),[{key:'n3',value:'1',checked:false},{key:'n4',value:'2',checked:true}]);
});
