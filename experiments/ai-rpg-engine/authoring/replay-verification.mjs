// Offline evidence only: does not start a server, import works, or call a Provider.
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,relative,dirname,isAbsolute} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parse} from 'parse5';
import {parse as parseJavaScript} from 'acorn';
import {createAuthorSandbox} from './sandbox.mjs';
import {compilePresentationBounded,PRESENTATION} from './presentation.mjs';
import {sha256} from './contracts.mjs';

const bounded=value=>{if(typeof value!=='string'||Buffer.byteLength(value)>PRESENTATION.textBytes)throw Error('REPLAY_SOURCE_SIZE');return value;};
const errorRecord=error=>({code:error.code||'REPLAY_VERIFICATION_ERROR',...(error.details?.diagnostic?{diagnostic:error.details.diagnostic}:{})});

/** Source inventory is a lower bound, not a security decision or proof of compatibility. */
export function inspectReplaySource(source){
  bounded(source);const root=parse(source),elements={},scripts=[],events=[],references=[];let nodes=0;
  const queue=[root];
  while(queue.length){
    const node=queue.pop();nodes++;
    if(node.tagName)elements[node.tagName]=(elements[node.tagName]||0)+1;
    if(node.tagName==='script')scripts.push((node.childNodes||[]).map(n=>n.value||'').join(''));
    for(const attr of node.attrs||[]){
      if(attr.name.startsWith('on'))events.push(attr.value);
      if(['src','href'].includes(attr.name)&&attr.value)references.push(attr.value);
    }
    queue.push(...node.childNodes||[]);
  }
  const calls=new Map(),syntaxErrors=[];
  function name(node){
    if(node?.type==='Identifier')return node.name;
    if(node?.type==='MemberExpression'){
      const field=node.computed?(node.property.type==='Literal'?String(node.property.value):'[computed]'):node.property.name;
      return (name(node.object)||'[expression]')+'.'+field;
    }
    return '';
  }
  for(const [index,code] of [...scripts,...events].entries()){
    let tree;try{tree=parseJavaScript(code,{ecmaVersion:'latest',allowReturnOutsideFunction:true,locations:true});}catch{syntaxErrors.push(index);continue;}
    const stack=[tree];
    while(stack.length){const node=stack.pop();
      if(node.type==='CallExpression'||node.type==='NewExpression'){
        const key=name(node.callee)||'[dynamic]';calls.set(key,(calls.get(key)||0)+1);
      }
      for(const value of Object.values(node))if(value&&typeof value==='object'){
        if(Array.isArray(value))stack.push(...value.filter(item=>item&&typeof item.type==='string'));
        else if(typeof value.type==='string')stack.push(value);
      }
    }
  }
  return {sha256:sha256(source),bytes:Buffer.byteLength(source),characters:[...source].length,nodes,elements,scriptCount:scripts.length,inlineEventCount:events.length,syntaxErrors,calls:Object.fromEntries([...calls].sort(([a],[b])=>a.localeCompare(b))),references:[...new Set(references)].sort(),inventoryIsExhaustive:false};
}

export async function verifyOpeningSource(html){
  const source=inspectReplaySource(html);let engine;
  try{engine=await createAuthorSandbox({html});return {source,initialization:'ready',stateHash:engine.initial.stateHash,fullInteractionAcceptance:'not-tested'};}
  catch(error){return {source,initialization:'blocked',error:errorRecord(error),fullInteractionAcceptance:'blocked'};}
  finally{engine?.close();}
}

export async function verifyReplySource(raw,css=''){
  bounded(raw);bounded(css);
  const compiled=await compilePresentationBounded({mode:'reply',html:raw,css,origin:'http://127.0.0.1:18550',token:'f'.repeat(48)});
  return {rawSha256:sha256(raw),characters:[...raw].length,cssSha256:sha256(css),compilation:'ready',manifest:compiled.manifest,visualAcceptance:'not-tested',resourcePolicy:'local aliases only; no download or rewrite',frame:compiled.frame};
}

// Independent minimal examples distinguish each missing API from the first
// failure in a full source. None is substituted for the original card.
export const compatibilityProbes=Object.freeze([
  {id:'svg',html:'<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="3"/></svg>',js:''},
  {id:'canvas',html:'<canvas id="c"></canvas>',js:'document.getElementById("c").getContext("2d");'},
  {id:'animation-frame',html:'<p></p>',js:'requestAnimationFrame(()=>{});'},
  {id:'timer',html:'<p></p>',js:'setTimeout(()=>{},10);'},
  {id:'interval',html:'<p></p>',js:'setInterval(()=>{},1000);'},
  {id:'layout',html:'<p id="n"></p>',js:'document.getElementById("n").getBoundingClientRect();'},
  {id:'scroll',html:'<p id="n"></p>',js:'document.getElementById("n").scrollIntoView({behavior:"smooth"});'},
  {id:'element-animation',html:'<p id="n"></p>',js:'document.getElementById("n").animate([{opacity:0},{opacity:1}],200);'},
  {id:'clipboard',html:'<p></p>',js:'navigator.clipboard.writeText("offline probe");'},
  {id:'audio',html:'<p></p>',js:'new Audio("asset://probe");'},
  {id:'pointer',html:'<p></p>',js:'addEventListener("pointermove",()=>{});'},
  {id:'sync-form',html:'<form><input id="n" value="测试"><button type="button">确认</button></form>',js:'document.getElementById("n").value="核验";'},
]);
export async function probeReplayCapabilities(){
  const results=[];
  for(const probe of compatibilityProbes){let engine;
    try{engine=await createAuthorSandbox(probe);results.push({id:probe.id,initialization:'ready'});}
    catch(error){results.push({id:probe.id,initialization:'blocked',error:errorRecord(error)});}
    finally{engine?.close();}
  }
  return results;
}

function inputFile(root,path){
  if(typeof path!=='string'||isAbsolute(path))throw Error('REPLAY_INPUT_PATH');
  const target=resolve(root,path),local=relative(root,target);
  if(!local||local.startsWith('..')||isAbsolute(local))throw Error('REPLAY_INPUT_PATH');
  return target;
}
export async function verifyReplayManifest(manifestPath){
  const root=dirname(resolve(manifestPath)),manifest=JSON.parse(await readFile(manifestPath,'utf8'));
  if(manifest.format!=='modelmirror.authoring-replay-input/1'||!Array.isArray(manifest.sources)||manifest.sources.length>8)throw Error('REPLAY_MANIFEST');
  const load=async ref=>{const bytes=await readFile(inputFile(root,ref.file));if(sha256(bytes)!==ref.sha256)throw Error('REPLAY_HASH_MISMATCH');return bounded(bytes.toString('utf8'));};
  const sources=[];
  for(const entry of manifest.sources){
    const opening=await verifyOpeningSource(await load(entry.opening)),css=await load(entry.css),replies=[];
    if(!Array.isArray(entry.replies)||entry.replies.length>20)throw Error('REPLAY_MANIFEST');
    for(const reply of entry.replies){const {frame,...result}=await verifyReplySource(await load(reply),css);replies.push({file:reply.file,...result});}
    sources.push({id:entry.id,opening,replies});
  }
  return {format:'modelmirror.authoring-replay-verification/1',recordedAt:new Date().toISOString(),providerCalls:0,sourceMutation:false,manifestSha256:sha256(await readFile(manifestPath)),sources,capabilities:await probeReplayCapabilities(),completeCardAcceptance:false};
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const [manifest,output]=process.argv.slice(2);if(!manifest||!output)throw Error('Usage: node authoring/replay-verification.mjs manifest.json result.json');
  const report=await verifyReplayManifest(manifest);await writeFile(output,JSON.stringify(report,null,2),{flag:'wx'});
  console.log(JSON.stringify({completeCardAcceptance:false,sources:report.sources.map(s=>({id:s.id,initialization:s.opening.initialization,error:s.opening.error,replies:s.replies.length}))}));
  // A successful evidence capture is deliberately not a green compatibility gate.
  process.exitCode=report.sources.some(s=>s.opening.initialization==='blocked')?2:0;
}
