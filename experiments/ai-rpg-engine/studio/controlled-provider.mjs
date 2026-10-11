import {parametersFor as earthParametersFor,sameParameters as earthSameParameters} from './model-parameters.mjs';
import {mkdir, readdir, readFile, open} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {models} from './provider.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const fail = code => Object.assign(Error(code), {status:409, code});
const identity = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const parameters = models.earth.parameters;
const ordered = value => Array.isArray(value) ? value.map(ordered) : value && typeof value==='object' ? Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])])) : value;
const bodyHash = value => sha(JSON.stringify(ordered(value)));


async function durable(path, value) {
  const file = await open(path, 'wx', 0o600);
  try { await file.writeFile(typeof value === 'string' ? value : JSON.stringify(value, null, 2)+'\n'); await file.sync(); }
  finally { await file.close(); }
}

async function boundedJSON(response) {
  if (!response.headers.get('content-type')?.includes('application/json')) throw fail('CONTROL_RESPONSE_INVALID');
  const chunks=[]; let size=0;
  for await (const chunk of response.body) {
    size+=chunk.length;
    if(size>64*1024*1024) throw fail('CONTROL_RESPONSE_TOO_LARGE');
    chunks.push(chunk);
  }
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
}

// This adapter is server-only. B3 supplies a persisted, authorized session selection.
// Sharing the existing card directory keeps old and controlled transports on one budget.
export async function createControlledProvider({directory, baseURL, serviceToken, enabled=false, limit=0, fetcher=fetch, reserveSlot=null, cardId='earth', modelPolicy=null, catalogModels=null, parameterProfile=null}) {
  if(parameterProfile!==null&&(parameterProfile!=='god-long-v1'||cardId!=='god'))throw fail('CONTROL_PARAMETER_PROFILE_INVALID');
  if(cardId!=='earth'&&!modelPolicy)throw fail('CONTROL_MODEL_POLICY_REQUIRED');
  if(modelPolicy&&(modelPolicy.cardId!==cardId||typeof modelPolicy.parametersFor!=='function'||typeof modelPolicy.sameParameters!=='function'))throw fail('CONTROL_MODEL_POLICY_INVALID');
  const parametersFor=modelPolicy?.parametersFor||earthParametersFor,sameParameters=modelPolicy?.sameParameters||earthSameParameters;
  if(!['earth','god'].includes(cardId)) throw fail('CONTROL_CARD_INCOMPATIBLE');
  if(!Number.isInteger(limit)||limit<0||limit>100) throw fail('INVALID_BUDGET');
  let endpoint;
  if(baseURL) {
    endpoint=new URL(baseURL);
    if(endpoint.protocol!=='http:'||!['127.0.0.1','localhost','[::1]','server'].includes(endpoint.hostname)||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||endpoint.pathname!=='/') throw fail('INVALID_CONTROL_ENDPOINT');
  }
  if(catalogModels!==null&&(!Array.isArray(catalogModels)||!catalogModels.length||catalogModels.some(m=>typeof m!=='string'||!m)))throw fail('CONTROL_CATALOG_SCOPE_INVALID');
  const allowedModels=catalogModels?new Set(catalogModels):null;
  const active=!!(enabled&&endpoint&&typeof serviceToken==='string'&&serviceToken.length>=32);
  const folder=join(directory,cardId); await mkdir(folder,{recursive:true});
  async function status() {
    const used=(await readdir(folder)).filter(name=>/^slot-\d+$/.test(name)).length;
    return {enabled:active,used,limit,remaining:Math.max(0,limit-used),parameters:modelPolicy?null:{...parameters}};
  }
  async function request(path, body, signal) {
    if(!active) throw fail('CONTROL_DISABLED');
    const url=new URL('/api/rpg/v1/'+path,endpoint);if(parameterProfile)url.searchParams.set('profile',parameterProfile);
    return fetcher(url, {
      method:body===undefined?'GET':'POST',redirect:'error',
      headers:{Authorization:'Bearer '+serviceToken,...(body===undefined?{}:{'Content-Type':'application/json'})},
      ...(body===undefined?{}:{body}),signal:signal?AbortSignal.any([signal,AbortSignal.timeout(245000)]):AbortSignal.timeout(245000)
    });
  }
  async function catalog({signal}={}) {
    const response=await request('models',undefined,signal);
    if(!response.ok) throw fail('CONTROL_CATALOG_UNAVAILABLE');
    const value=await boundedJSON(response);
    if(!Array.isArray(value.models)||value.models.length>10000) throw fail('CONTROL_CATALOG_INVALID');
    return value.models.filter(m=>!allowedModels||allowedModels.has(m.model)).map(m=>{
      if(!identity(m.selectionId)||!identity(m.selectionRevision)||typeof m.model!=='string'||typeof m.name!=='string'||typeof m.available!=='boolean'||!sameParameters(m.model,m.parameters)) throw fail('CONTROL_CATALOG_INVALID');
      const item={selectionId:m.selectionId,selectionRevision:m.selectionRevision,model:m.model,name:m.name,available:m.available};
      if(m.pricing&&m.pricing.source==='provider_catalog') item.pricing=Object.fromEntries(['currency','unit','input_price','output_price','observed_at','source','billing_authoritative'].filter(k=>k in m.pricing).map(k=>[k,m.pricing[k]]));
      return item;
    });
  }
  async function generate(card,{messages,params,signal,sessionId,requestId,selection,lease,purpose,beforeDispatch}) {
    if(card!==cardId) throw fail('CONTROL_CARD_INCOMPATIBLE');
    if(!active) throw fail('CONTROL_DISABLED');
    if(!identity(sessionId)||!identity(requestId)||!identity(selection?.selectionId)||!identity(selection?.selectionRevision)||typeof selection?.model!=='string') throw fail('CONTROL_SELECTION_REQUIRED');
    if(allowedModels&&!allowedModels.has(selection.model))throw fail('CONTROL_MODEL_OUT_OF_SCOPE');
    if(params && !sameParameters(selection.model,params)) throw fail('CONTROL_PARAMETERS_MISMATCH');
    if(!Array.isArray(messages)||messages.length<2||messages.length%2||messages.some((m,i)=>!m||Object.keys(m).sort().join(',')!=='content,role'||m.role!==(i===0?'system':i%2?'user':'assistant')||typeof m.content!=='string')) throw fail('INVALID_MESSAGES');
    const payload={sessionId,requestId,selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,messages,parameters:parametersFor(selection.model)};
    const wire=JSON.stringify(payload);
    if(Buffer.byteLength(wire)>1024*1024) throw fail('INVALID_MESSAGES');
    if(signal?.aborted) throw fail('CANCELLED_BEFORE_DISPATCH');
    const claims=join(folder,'controlled-requests');await mkdir(claims,{recursive:true});
    try { await durable(join(claims,sha(JSON.stringify([sessionId,requestId]))+'.json'),{requestHash:sha(wire),status:'claimed-no-replay'}); }
    catch(e){if(e.code==='EEXIST') throw fail('REQUEST_ALREADY_CLAIMED');throw e;}
    if(!reserveSlot&&(await status()).remaining===0) throw fail('BUDGET_EXHAUSTED');
    let slot=reserveSlot?await reserveSlot(lease,{sessionId,requestId,purpose}):null;
    for(let i=1;!reserveSlot&&i<=limit;i++) {
      try {slot=join(folder,'slot-'+i);await mkdir(slot);break;}
      catch(e){if(e.code!=='EEXIST') throw e;slot=null;}
    }
    if(!slot) throw fail('BUDGET_EXHAUSTED');
    const save=(name,value)=>durable(join(slot,name),value);
    const reservation={card,sessionId,requestId,at:new Date().toISOString(),transport:'modelmirror-rpg-s2s-v1',
      status:'reserved',...(reserveSlot?{purpose}:{}),requestHash:sha(wire),controlRequestHash:bodyHash(payload),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,
      requestedModel:selection.model,parameters:parametersFor(selection.model),retries:0};
    await save('reservation.json',reservation); await save('request.json',wire);
    let raw='',sse='',receipt=null,httpStatus=null,error=null;
    try {
      if(signal?.aborted) throw fail('CANCELLED');
      const response=beforeDispatch?await (await beforeDispatch(()=>request('chat/completions',wire,signal),sha(wire))).response:await request('chat/completions',wire,signal); httpStatus=response.status;
      const value=await boundedJSON(response);
      if(typeof value.raw==='string') raw=value.raw;
      if(typeof value.sse==='string') sse=value.sse;
      const r=value.receipt;
      if(!r||r.gateway!=='rpg_scoped'||r.sessionId!==sessionId||r.requestId!==requestId||r.selectionId!==selection.selectionId||r.selectionRevision!==selection.selectionRevision||r.requestedModel!==selection.model||!sameParameters(selection.model,r.parameters)||r.rawHash!==sha(raw)||r.sseHash!==sha(sse)||r.requestHash!==reservation.controlRequestHash||r.retries!==0) throw fail('CONTROL_RECEIPT_INVALID');
      // Keep only the documented receipt projection; no server configuration can leak to UI.
      receipt=Object.fromEntries(['gateway','runId','attemptId','requestedModel','actualModel','selectionId','selectionRevision','parameters','httpStatus','dispatched','usage','error','retries','requestHash','rawHash','sseHash','status'].map(k=>[k,r[k]]));
      if(!response.ok||r.status!=='complete'||r.error||!raw.trim()||r.dispatched!==true) throw fail('CONTROL_FAILED_OR_UNKNOWN');
      if(r.actualModel!==null&&r.actualModel!==selection.model) throw fail('CONTROL_MODEL_MISMATCH');
      if(signal?.aborted) throw fail('CANCELLED');
    } catch(e) {
      const safe=['CONTROL_RECEIPT_INVALID','CONTROL_FAILED_OR_UNKNOWN','CONTROL_MODEL_MISMATCH','CONTROL_RESPONSE_INVALID','CONTROL_RESPONSE_TOO_LARGE','CANCELLED'];
      error=signal?.aborted?'CANCELLED':safe.includes(e.code)?e.code:'CONTROL_TRANSPORT_FAILED_OR_UNKNOWN';
    }
    if([raw,sse,JSON.stringify(receipt)].some(text=>text?.includes(serviceToken))) {raw='';sse='';receipt=null;error='CREDENTIAL_ECHO_BLOCKED';}
    await save('response.txt',raw);await save('response.sse',sse);
    await save('result.json',{...reservation,status:error?'failed_or_unknown':'complete',rawHash:sha(raw),sseHash:sha(sse),httpStatus,receipt,error,manual:'pending'});
    if(error) throw fail(error);
    return raw;
  }
  async function evidence(sessionId,requestId) {
    if(!identity(sessionId)||!identity(requestId)) throw fail('INVALID_REQUEST_ID');
    const matches=[];
    for(const name of (await readdir(folder)).filter(n=>/^slot-\d+$/.test(n))) {
      let bytes;
      try {bytes=await readFile(join(folder,name,'result.json'));} catch(e){if(e.code==='ENOENT')continue;throw e;}
      const result=JSON.parse(bytes.toString('utf8'));
      if(result.sessionId!==sessionId||result.requestId!==requestId) continue;
      const r=result.receipt;
      matches.push({ref:cardId+'/'+name+'/result.json',sha256:sha(bytes),record:{sessionId,requestId,
        status:result.status,requestedModel:result.requestedModel,actualModel:r?.actualModel??result.actualModel??null,
        parameters:result.parameters,rawHash:result.rawHash,requestHash:result.requestHash,
        gateway:r?.gateway??null,runId:r?.runId??null,attemptId:r?.attemptId??null,
        selectionId:result.selectionId??null,selectionRevision:result.selectionRevision??null}});
    }
    if(matches.length>1) throw fail('MODEL_RECEIPT_AMBIGUOUS');
    return matches[0]??null;
  }
  async function recoveredOutput(sessionId,requestId){
    const found=await evidence(sessionId,requestId);if(!found)return null;
    const slot=found.ref.split('/')[1],raw=await readFile(join(folder,slot,'response.txt'),'utf8'),sse=await readFile(join(folder,slot,'response.sse'),'utf8');
    const result=JSON.parse(await readFile(join(folder,slot,'result.json'),'utf8'));
    if(sha(raw)!==result.rawHash||sha(sse)!==result.sseHash)throw fail('CONTROL_RECEIPT_INVALID');
    let finishReason=null,done=0,streamValid=true,streamRaw='';
    for(const block of sse.split(/\r?\n\r?\n/)){
      const data=block.split(/\r?\n/).filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
      if(!data)continue;if(done)streamValid=false;if(data==='[DONE]'){done++;continue;}
      try{const value=JSON.parse(data);if(value.error||value.model&&value.model!==result.requestedModel||(value.choices||[]).length>1)streamValid=false;
        for(const c of value.choices||[]){if(c.finish_reason)finishReason=c.finish_reason;const content=c.delta?.content;if(content!=null&&typeof content!=='string')streamValid=false;else streamRaw+=content||'';}
      }catch{throw fail('CONTROL_RECEIPT_INVALID');}
    }
    const r=result.receipt;
    const terminal=r?.dispatched===true&&r.status==='failed_or_unknown'&&r.error==='rpg_incomplete_output'&&result.error==='CONTROL_FAILED_OR_UNKNOWN'&&
      r.gateway==='rpg_scoped'&&r.requestHash===result.controlRequestHash&&r.rawHash===result.rawHash&&r.sseHash===result.sseHash&&r.requestedModel===result.requestedModel&&r.actualModel===result.requestedModel&&r.selectionId===result.selectionId&&r.selectionRevision===result.selectionRevision&&sameParameters(result.requestedModel,r.parameters)&&r.retries===0&&
      done===1&&streamValid&&streamRaw===raw&&sse.endsWith('\n\n')&&['content_filter','length'].includes(finishReason);
    const rejected=r?.dispatched===false&&r.status==='failed'&&result.error==='CONTROL_FAILED_OR_UNKNOWN'&&result.httpStatus===409&&
      ['rpg_fallback_limit_exhausted','rpg_route_unavailable','rpg_selection_changed'].includes(r.error)&&raw===''&&sse===''&&
      r.gateway==='rpg_scoped'&&r.requestHash===result.controlRequestHash&&r.rawHash===result.rawHash&&r.sseHash===result.sseHash&&r.requestedModel===result.requestedModel&&r.actualModel===null&&r.selectionId===result.selectionId&&r.selectionRevision===result.selectionRevision&&sameParameters(result.requestedModel,r.parameters)&&r.retries===0;
    const failureCode=rejected?(r.error==='rpg_fallback_limit_exhausted'?'CONTROL_BUDGET_EXHAUSTED':'CONTROL_PREFLIGHT_REJECTED'):terminal?(finishReason==='content_filter'?'PROVIDER_CONTENT_FILTERED':'PROVIDER_OUTPUT_TRUNCATED'):null;
    return {raw,finishReason,evidence:found,failureCode,failureKnown:!!rejected||!!terminal||r?.dispatched===true&&(r.status==='failed'||r.status==='complete'&&result.error==='CONTROL_FAILED_OR_UNKNOWN'&&!raw.trim())};
  }
  return {catalog,generate,status,evidence,recoveredOutput};
}
