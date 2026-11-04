import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';
import {parse, parseFragment} from 'parse5';
import {parse as parseJavaScript} from 'acorn';
import {newQuickJSWASMModuleFromVariant} from 'quickjs-emscripten-core';
import RELEASE_SYNC from '@jitl/quickjs-wasmfile-release-sync';
import {installVirtualDom} from './virtual-dom.mjs';
import {installPlatform,validatePlatform} from './platform.mjs';
import {fail, keys, sha256} from './contracts.mjs';

export const SANDBOX=Object.freeze({sourceBytes:2*1024*1024,resultBytes:4*1024*1024,nodes:5000,depth:64,memory:16*1024*1024,stack:256*1024,initMs:1000,stepMs:200,wallMs:2500,controls:128});
const eventTypes=new Set(['click','input','change','submit','keydown','keyup','focus','blur','toggle','pointermove','pointerdown','pointerup','touchmove','tick','resize','feedback']);
const keyPattern=/^n[1-9][0-9]{0,5}$/;
const escape=value=>value.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const voidTags=new Set('area br col hr img input source track wbr'.split(' '));
function boundedText(value,bytes=SANDBOX.sourceBytes){if(typeof value!=='string'||!value.isWellFormed()||Buffer.byteLength(value)>bytes)fail('AUTHORING_SCRIPT_SIZE');return value;}
export const needsAuthorScript=(html,js)=>!!js||/<script\b|\bon[a-z]+\s*=/i.test(html);
function synchronousSource(source){
  boundedText(source);let tree;
  try{tree=parseJavaScript(source,{ecmaVersion:'latest',sourceType:'script',allowReturnOutsideFunction:true});}catch{fail('AUTHORING_SCRIPT_EXCEPTION','',{diagnostic:'JavaScript 语法错误'});}
  const queue=[tree];let visited=0;
  while(queue.length){const node=queue.pop();if(++visited>100000)fail('AUTHORING_SCRIPT_SIZE');if(node.type==='ImportExpression')fail('AUTHORING_SCRIPT_SOURCE_UNSUPPORTED');for(const value of Object.values(node)){if(value&&typeof value==='object'){if(Array.isArray(value))queue.push(...value.filter(n=>n&&typeof n.type==='string'));else if(typeof value.type==='string')queue.push(value);}}}
}

export function validateScriptEvent(event){
  keys(event,['type','target','key','controls','viewport','layouts','elapsed','clientX','clientY','feedback'].filter(k=>['type','target','key','controls'].includes(k)||Object.hasOwn(event,k)));
  if(!eventTypes.has(event.type)||!keyPattern.test(event.target)||typeof event.key!=='string'||event.key.length>80||!Array.isArray(event.controls)||event.controls.length>SANDBOX.controls)fail('AUTHORING_SCRIPT_EVENT');
  const seen=new Set();
  if(event.viewport)validateViewport(event.viewport);
  for(const name of ['elapsed','clientX','clientY'])if(Object.hasOwn(event,name)&&(!Number.isFinite(event[name])||Math.abs(event[name])>1e7||name==='elapsed'&&event[name]<0))fail('AUTHORING_SCRIPT_EVENT');
  if(event.layouts){if(!Array.isArray(event.layouts)||event.layouts.length>512)fail('AUTHORING_SCRIPT_EVENT');for(const item of event.layouts){if(!keyPattern.test(item.key)||Object.keys(item).some(k=>k!=='key'&&(!['x','y','left','right','top','bottom','width','height','scrollTop','scrollLeft','scrollHeight','scrollWidth','clientWidth','clientHeight','offsetWidth','offsetHeight'].includes(k)||!Number.isFinite(item[k])||Math.abs(item[k])>1e7)))fail('AUTHORING_SCRIPT_EVENT');}}
  if(event.feedback){if(event.type!=='feedback'||!Array.isArray(event.feedback)||event.feedback.length>8||event.feedback.some(i=>!i||!/^(clip|media)[1-9][0-9]{0,8}$/.test(i.id)||typeof i.ok!=='boolean'||Object.keys(i).length!==2))fail('AUTHORING_SCRIPT_EVENT');}
  for(const control of event.controls){
    if(!control||typeof control!=='object'||Array.isArray(control)||!keyPattern.test(control.key)||seen.has(control.key))fail('AUTHORING_SCRIPT_EVENT');
    seen.add(control.key);
    for(const [key,value] of Object.entries(control)){
      if(key==='key')continue;
      if(key==='value')boundedText(value,64000);
      else if(!['checked','selected','open'].includes(key)||typeof value!=='boolean')fail('AUTHORING_SCRIPT_EVENT');
    }
  }
  return event;
}
export function validateViewport(value){keys(value,['innerWidth','innerHeight','devicePixelRatio']);for(const name of ['innerWidth','innerHeight'])if(!Number.isFinite(value[name])||value[name]<1||value[name]>4096)fail('AUTHORING_SCRIPT_EVENT');if(!Number.isFinite(value.devicePixelRatio)||value.devicePixelRatio<0.5||value.devicePixelRatio>3)fail('AUTHORING_SCRIPT_EVENT');return value;}

// This parser runs only in the disposable Worker. Its native callback accepts
// one guest string and returns one JSON string, never a host object or handle.
function blueprintHtml(source,{fragment=false,scripts=[]}={}){
  boundedText(source);let count=0;
  function visit(node,depth=0){
    if(++count>SANDBOX.nodes||depth>SANDBOX.depth)fail('AUTHORING_DOM_CAPACITY');
    if(node.nodeName==='#text')return {tag:'#text',text:node.value};
    if(node.tagName==='script'){
      const attrs=Object.fromEntries((node.attrs||[]).map(a=>[a.name,a.value]));
      if(fragment||attrs.src||attrs.type&&!['text/javascript','application/javascript'].includes(attrs.type))fail('AUTHORING_SCRIPT_SOURCE_UNSUPPORTED');
      scripts.push((node.childNodes||[]).map(n=>n.value||'').join(''));return null;
    }
    if(node.tagName&&!['http://www.w3.org/1999/xhtml','http://www.w3.org/2000/svg'].includes(node.namespaceURI))fail('AUTHORING_DOM_UNSUPPORTED');
    if(!node.tagName&&!['#document','#document-fragment'].includes(node.nodeName))return null;
    const attrs=Object.create(null);
    for(const {name,value} of node.attrs||[]){if(name.startsWith('data-author-'))continue;if(['__proto__','prototype','constructor'].includes(name))fail('AUTHORING_DOM_UNSUPPORTED');attrs[name]=value;}
    if(Object.keys(attrs).length>64)fail('AUTHORING_DOM_CAPACITY');
    return {tag:node.tagName||'#document',ns:node.namespaceURI==='http://www.w3.org/2000/svg'?'svg':'html',attrs,children:(node.childNodes||[]).map(n=>visit(n,depth+1)).filter(Boolean)};
  }
  const root=visit(fragment?parseFragment(source):parse(source));
  return fragment?root.children:root;
}

/** Treat the guest's serialized tree as untrusted, even after a successful step. */
function resultHtml(serialized,initial){
  boundedText(serialized,SANDBOX.resultBytes);
  let result;try{result=JSON.parse(serialized);}catch{fail('AUTHORING_SCRIPT_RESULT');}
  keys(result,['tree','proposals','platform']);validatePlatform(result.platform);
  if(!Array.isArray(result.proposals)||result.proposals.length>4||initial&&result.proposals.length)fail('AUTHORING_SCRIPT_GESTURE_REQUIRED');
  for(const proposal of result.proposals){keys(proposal,['type','payload',...(Object.hasOwn(proposal,'id')?['id']:[])]);if(!['input','draft','copy'].includes(proposal.type)||proposal.id&&(!/^clip[1-9][0-9]{0,8}$/.test(proposal.id)||proposal.type!=='copy'))fail('AUTHORING_SCRIPT_RESULT');boundedText(proposal.payload,480000);if([...proposal.payload].length>120000)fail('AUTHORING_SCRIPT_SIZE');}
  let count=0;const seen=new Set();
  function visit(node,depth=0){
    if(++count>SANDBOX.nodes||depth>SANDBOX.depth)fail('AUTHORING_DOM_CAPACITY');
    keys(node,['key','tag','ns','attrs','text','value','checked','selected','children']);
    if(!keyPattern.test(node.key)||seen.has(node.key)||!['html','svg'].includes(node.ns)||typeof node.tag!=='string'||!/^([a-zA-Z][a-zA-Z0-9-]*|#text|#document)$/.test(node.tag)||!Array.isArray(node.children)||typeof node.checked!=='boolean'||typeof node.selected!=='boolean')fail('AUTHORING_SCRIPT_RESULT');
    seen.add(node.key);boundedText(node.text);boundedText(node.value);
    if(!node.attrs||typeof node.attrs!=='object'||Array.isArray(node.attrs)||Object.keys(node.attrs).length>64)fail('AUTHORING_SCRIPT_RESULT');
    const attrs=[];
    for(const [name,value] of Object.entries(node.attrs)){
      if(!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)||['constructor','prototype'].includes(name))fail('AUTHORING_SCRIPT_RESULT');
      boundedText(value,240000);
      if(name.startsWith('on')||name.startsWith('data-author-')||['value','checked','selected'].includes(name))continue;
      attrs.push(' '+name+'="'+escape(value)+'"');
    }
    const children=node.children.map(n=>visit(n,depth+1)).join('');
    if(node.tag==='#text'){if(node.children.length)fail('AUTHORING_SCRIPT_RESULT');return escape(node.text);}
    if(node.tag==='#document'){if(depth!==0)fail('AUTHORING_SCRIPT_RESULT');return children;}
    attrs.push(' data-author-node="'+node.key+'"');
    if(node.tag==='input')attrs.push(' value="'+escape(node.value)+'"'+(node.checked?' checked':''));
    else if(node.tag==='option'){
      if(Object.hasOwn(node.attrs,'value'))attrs.push(' value="'+escape(node.attrs.value)+'"');
      if(node.selected)attrs.push(' selected');
    }
    else if(Object.hasOwn(node.attrs,'value'))attrs.push(' value="'+escape(node.attrs.value)+'"');
    // Style is raw text in HTML, so close-tag sequences must not leave that node.
    const inner=node.tag==='style'?node.children.map(n=>n.text).join('').replaceAll('<','\\3c '):node.tag==='textarea'?escape(node.value):children;
    return '<'+node.tag+attrs.join('')+'>'+(voidTags.has(node.tag)?'':inner+'</'+node.tag+'>');
  }
  const html=visit(result.tree);boundedText(html);
  return {html,proposals:result.proposals,platform:result.platform,stateHash:sha256(serialized)};
}

/** A dedicated Worker and QuickJS context per preview. Any failure closes it. */
export async function createAuthorSandbox({html,js='',viewport={innerWidth:800,innerHeight:600,devicePixelRatio:1}},{wallMs=SANDBOX.wallMs}={}){
  boundedText(html);boundedText(js);
  validateViewport(viewport);
  if(!Number.isInteger(wallMs)||wallMs<1||wallMs>10000)fail('AUTHORING_SCRIPT_OPTIONS');
  const worker=new Worker(new URL(import.meta.url),{workerData:{job:'authoring-sandbox',html,js,viewport},resourceLimits:{maxOldGenerationSizeMb:64,stackSizeMb:4}});
  let closed=false,pending=null;
  const close=()=>{if(closed)return;closed=true;if(pending){clearTimeout(pending.timer);pending.reject({code:'AUTHORING_SCRIPT_CLOSED'});pending=null;}worker.terminate().catch(()=>{});};
  const settle=message=>{if(!pending)return;const current=pending;pending=null;clearTimeout(current.timer);if(message.error){close();current.reject(message.error);}else current.resolve(message.result);};
  worker.on('message',settle);worker.on('error',()=>settle({error:{code:'AUTHORING_SCRIPT_RESOURCE_LIMIT'}}));worker.on('exit',()=>{settle({error:{code:'AUTHORING_SCRIPT_CLOSED'}});closed=true;});
  const wait=()=>new Promise((resolve,reject)=>{if(closed)return reject({code:'AUTHORING_SCRIPT_CLOSED'});if(pending)return reject({code:'AUTHORING_SCRIPT_BUSY'});const timer=setTimeout(()=>settle({error:{code:'AUTHORING_SCRIPT_TIMEOUT'}}),wallMs);pending={resolve,reject,timer};});
  const convert=error=>fail(error.code||'AUTHORING_SCRIPT_EXCEPTION','',error.detail?{diagnostic:error.detail}:{});
  const initial=await wait().catch(convert);
  return {initial,close,async step(event){validateScriptEvent(event);if(pending)fail('AUTHORING_SCRIPT_BUSY');const result=wait();if(!closed)worker.postMessage({event});return result.catch(convert);}};
}

if(!isMainThread&&workerData?.job==='authoring-sandbox'){
  let context,runtime,deadline;
  const diagnostic=error=>({code:error.code||'AUTHORING_SCRIPT_EXCEPTION',detail:String(error.detail||error.details?.diagnostic||'').slice(0,200)});
  try{
    const scripts=[],tree=blueprintHtml(workerData.html,{scripts});
    const module=await newQuickJSWASMModuleFromVariant(RELEASE_SYNC);
    runtime=module.newRuntime();runtime.setMemoryLimit(SANDBOX.memory);runtime.setMaxStackSize(SANDBOX.stack);
    runtime.setInterruptHandler(()=>Date.now()>deadline);context=runtime.newContext();
    function guestFailure(handle){
      let message='';try{const value=context.getProp(handle,'message');if(context.typeof(value)==='string')message=context.getString(value).slice(0,200);value.dispose();}catch{}handle.dispose();
      const known=new Set(['AUTHORING_DOM_UNSUPPORTED','AUTHORING_DOM_CAPACITY','AUTHORING_PLATFORM_LIMIT','AUTHORING_SCRIPT_RESULT','AUTHORING_SCRIPT_SIZE','AUTHORING_SCRIPT_GESTURE_REQUIRED','AUTHORING_SCRIPT_TARGET','AUTHORING_SCRIPT_CONTROL','AUTHORING_SCRIPT_ASYNC_UNSUPPORTED','AUTHORING_SCRIPT_SOURCE_UNSUPPORTED']);
      const prefix=/^AUTHORING_[A-Z_]+/.exec(message)?.[0];
      const code=Date.now()>deadline||/interrupted/.test(message)?'AUTHORING_SCRIPT_TIMEOUT':/out of memory|stack overflow/.test(message)?'AUTHORING_SCRIPT_RESOURCE_LIMIT':known.has(prefix)?prefix:'AUTHORING_SCRIPT_EXCEPTION';
      throw {code,detail:message};
    }
    function evaluate(code,file='author.js',stringResult=false){
      const result=context.evalCode(code,file,{type:'global'});if(result.error)guestFailure(result.error);
      try{if(stringResult){if(context.typeof(result.value)!=='string')fail('AUTHORING_SCRIPT_RESULT');return context.getString(result.value);}}finally{result.value.dispose();}
    }
    const parser=context.newFunction('__parseFragment',handle=>{
      if(context.typeof(handle)!=='string')return {error:context.newError('AUTHORING_SCRIPT_RESULT')};
      try{return context.newString(JSON.stringify(blueprintHtml(context.getString(handle),{fragment:true})));}catch(error){return {error:context.newError(error.code||'AUTHORING_DOM_UNSUPPORTED')};}
    });
    context.setProp(context.global,'__parseFragment',parser);parser.dispose();
    const verifier=context.newFunction('__verifySource',handle=>{
      if(context.typeof(handle)!=='string')return {error:context.newError('AUTHORING_SCRIPT_RESULT')};
      try{synchronousSource(context.getString(handle));return context.undefined;}catch(error){return {error:context.newError(error.code||'AUTHORING_SCRIPT_EXCEPTION')};}
    });
    context.setProp(context.global,'__verifySource',verifier);verifier.dispose();
    deadline=Date.now()+SANDBOX.stepMs;
    evaluate('('+installVirtualDom.toString()+')('+JSON.stringify(tree)+',('+installPlatform.toString()+'),'+JSON.stringify({viewport:workerData.viewport,epoch:Date.now()})+');','dom-bridge.js');
    // Retain the fixed bridge function handles before evaluating author code.
    const step=context.getProp(context.global,'__authorStep'),take=context.getProp(context.global,'__authorTake'),track=context.getProp(context.global,'__authorTrack');
    evaluate('delete globalThis.__authorStep;delete globalThis.__authorTake;delete globalThis.__authorTrack;delete globalThis.__parseFragment;delete globalThis.__verifySource;','bridge-private.js');
    function execute(event,initial=false){
      deadline=Date.now()+(initial?SANDBOX.initMs:SANDBOX.stepMs);
      if(initial){for(const [i,source] of [...scripts,workerData.js].entries()){synchronousSource(source);const r=context.evalCode(source,'inline-'+i+'.js');if(r.error)guestFailure(r.error);const tracked=context.callFunction(track,context.undefined,r.value);r.value.dispose();if(tracked.error)guestFailure(tracked.error);tracked.value.dispose();}}
      // Parse the event in the guest, then pass only its guest handle to the bridge.
      const payload=context.newString(JSON.stringify(event));
      const decode=context.evalCode('(value)=>JSON.parse(value)','event.js');if(decode.error)guestFailure(decode.error);
      const parsed=context.callFunction(decode.value,context.undefined,payload);decode.value.dispose();payload.dispose();if(parsed.error)guestFailure(parsed.error);
      const call=context.callFunction(step,context.undefined,parsed.value);parsed.value.dispose();if(call.error)guestFailure(call.error);call.value.dispose();
      const jobs=runtime.executePendingJobs(64);if(jobs.error)guestFailure(jobs.error);if(runtime.hasPendingJob())fail('AUTHORING_SCRIPT_ASYNC_PENDING');
      const output=context.callFunction(take,context.undefined);if(output.error)guestFailure(output.error);
      try{if(context.typeof(output.value)!=='string')fail('AUTHORING_SCRIPT_RESULT');return resultHtml(context.getString(output.value),initial);}finally{output.value.dispose();}
    }
    parentPort.postMessage({result:execute({type:'initialize'},true)});
    parentPort.on('message',({event})=>{
      try{validateScriptEvent(event);parentPort.postMessage({result:execute(event)});}catch(error){parentPort.postMessage({error:diagnostic(error)});parentPort.close();}
    });
    parentPort.on('close',()=>{step.dispose();take.dispose();track.dispose();context.dispose();runtime.dispose();});
  }catch(error){parentPort.postMessage({error:diagnostic(error)});parentPort.close();}
}
