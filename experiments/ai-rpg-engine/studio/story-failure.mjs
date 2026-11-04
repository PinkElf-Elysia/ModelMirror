import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail} from '../plugins/catalog.mjs';

// Interpret only the two observed, complete stream endings. A transport error,
// ambiguous ending or unbound result cannot settle an outstanding request.
export function terminalOutputCode(sse,raw,model) {
  if(!sse.endsWith('\n\n')&&!sse.endsWith('\r\n\r\n'))return null;
  let done=false,finish=null,text='';
  for(const block of sse.split(/\r?\n\r?\n/)){
    const data=block.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
    if(!data)continue;
    if(done)return null;
    if(data==='[DONE]'){done=true;continue;}
    let e;try{e=JSON.parse(data);}catch{return null;}
    if(!e||e.error||(e.model&&e.model!==model)||!Array.isArray(e.choices)||e.choices.length>1)return null;
    for(const c of e.choices){
      if(!c||c.delta?.tool_calls||c.delta?.function_call)return null;
      const chunk=c.delta?.content??'';
      if(typeof chunk!=='string'||(finish&&chunk))return null;
      text+=chunk;
      if(c.finish_reason){
        if(finish&&finish!==c.finish_reason)return null;
        finish=c.finish_reason;
      }
    }
  }
  if(!done||text!==raw)return null;
  if(finish==='length')return 'PROVIDER_OUTPUT_TRUNCATED';
  return finish==='stop'&&!raw.trim()?'PROVIDER_EMPTY_OUTPUT':null;
}

async function classify(control,directory,id,key,req){
  const evidence=await control.evidence(id,key);
  if(!evidence||!/^earth\/slot-[1-9]\d*\/result\.json$/.test(evidence.ref)||
      (req.evidence && req.evidence.sha256!==evidence.sha256))return null;
  const file=join(directory,evidence.ref);
  const bytes=await readFile(file);
  if(sha(bytes)!==evidence.sha256)return null;
  const result=JSON.parse(bytes),r=result.receipt,sel=req.selection;
  if(result.sessionId!==id||result.requestId!==key||result.card!=='earth'||result.httpStatus!==502||
      result.status!=='failed_or_unknown'||result.error!=='CONTROL_FAILED_OR_UNKNOWN'||result.retries!==0||
      r?.status!=='failed_or_unknown'||r.error!=='rpg_incomplete_output'||r.gateway!=='rpg_scoped'||
      r.dispatched!==true||r.retries!==0||!r.runId||!r.attemptId||r.actualModel!==sel.model||
      [r,result].some(x=>x.requestedModel!==sel.model||x.selectionId!==sel.selectionId||
        x.selectionRevision!==sel.selectionRevision||canonical(x.parameters)!==canonical(sel.parameters)))return null;
  const dir=join(directory,evidence.ref.split('/').slice(0,2).join('/'));
  const [wire,raw,sse]=await Promise.all(['request.json','response.txt','response.sse'].map(n=>readFile(join(dir,n),'utf8')));
  const w=JSON.parse(wire);
  if(sha(wire)!==result.requestHash||sha(canonical(w))!==result.controlRequestHash||r.requestHash!==result.controlRequestHash||
      w.sessionId!==id||w.requestId!==key||w.selectionId!==sel.selectionId||w.selectionRevision!==sel.selectionRevision||
      canonical(w.parameters)!==canonical(sel.parameters)||sha(canonical(w.messages))!==req.assemblyHash||
      [r,result].some(x=>x.rawHash!==sha(raw)||x.sseHash!==sha(sse)))return null;
  const code=terminalOutputCode(sse,raw,sel.model);
  return code?{code,evidence}:null;
}

export async function settleStoryFailure(store,control,directory,id,key){
  if(typeof key!=='string'||!/^[a-zA-Z0-9_-]{1,128}$/.test(key))throw fail('INVALID_REQUEST_ID',400);
  return store.exclusive(id,async()=>{
    const s=await store.read(id),req=s.requests[key];
    if(!control||s.mode!=='real'||s.runtime?.format!==2||req?.status!=='pending'||req.selection?.kind!=='controlled')return s;
    await store.requireRuntime(s);
    const proof=await classify(control,directory,id,key,req).catch(()=>null);
    if(proof){
      Object.assign(req,{status:'failed',error:proof.code,evidence:proof.evidence});
      s.revision++;
      await store.write(s);
    }
    return s;
  });
}
