// Reuses the repository's generation-session offline protocol fixture.
// No real provider, secret or external network is involved.
import assert from 'node:assert/strict';import {createServer} from 'node:http';import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {canonical,sha256} from '../../authoring/contracts.mjs';import {ControlPlaneModelControl,PARAMETER_SELECTION_FORMAT} from '../../authoring/model-control.mjs';import {PARAMETER_PROFILE,PARAMETER_CONFIG} from '../../authoring/generation-settings.mjs';
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),code=c=>e=>e?.code===c;
export const token='local-test-only-parameter-session-'+ 'x'.repeat(32);
const python=process.env.RPG_TEST_PYTHON||'python';
const parameterSource=fileURLToPath(new URL('../../../../server/model_router/rpg_parameters.py',import.meta.url));
const resolver=`import json,sys,runpy,hashlib\nm=runpy.run_path(sys.argv[1]);v=json.load(sys.stdin)\ntry:\n r=m['resolve_parameters'](v['policy'],v['parameters'],v['purpose']);r={'format':'modelmirror.rpg.parameter-resolution/2','capabilityRevision':v['capabilityRevision'],**r};r['sha256']=hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest();print(json.dumps(r))\nexcept m['ParameterError'] as e:print(json.dumps({'error':e.code}))`;
const ffield=(type,wireName,extra={},defaultValue=null)=>({type,wireName,required:false,default:defaultValue===null?{mode:'omit'}:{mode:'value',value:defaultValue},...extra});
const policy=()=>({kind:'editable',fields:{
 temperature:ffield('number','temperature',{minimum:0,maximum:2}),topP:ffield('number','top_p',{minimum:0,maximum:1}),
 maxOutputTokens:ffield('integer','max_tokens',{minimum:1,maximum:16000,required:true},4096),
 topK:ffield('integer','top_k',{minimum:0,maximum:100}),seed:ffield('integer','seed',{minimum:0,maximum:9999}),
 frequencyPenalty:ffield('number','frequency_penalty',{minimum:-2,maximum:2}),presencePenalty:ffield('number','presence_penalty',{minimum:-2,maximum:2}),
 stop:ffield('text-list','stop',{minItems:0,maxItems:8,maxItemLength:80}),
 'thinking.enabled':ffield('boolean','reasoning.enabled'),
 'thinking.effort':ffield('enum','reasoning.effort',{options:['low','high']}),
 'thinking.budgetTokens':ffield('integer','reasoning.max_tokens',{minimum:0,maximum:8192}),
},exclusive:[],requires:[],lessThan:[['thinking.budgetTokens','maxOutputTokens']],purposes:['story','summary','memory']});
const profile=(purpose,values={})=>({format:PARAMETER_PROFILE,purpose,modelId:'synthetic/'+purpose,parameters:{format:PARAMETER_CONFIG,values:clone(values)}});
export async function parameterMock(t,options={}) {
 const f={tasks:[],requests:[],lookups:[],results:new Map(),policies:Object.fromEntries(['story','summary','memory'].map(k=>[k,policy()])),onCheck:null,corrupt:null,unavailable:false};
 f.catalog=(version=2)=>{
  const models=Object.entries(f.policies).map(([id,p])=>({selectionId:id,selectionRevision:sha256(id+'-qualification'),modelId:'synthetic/'+id,name:id,available:true,contextLength:256000,maxOutputTokens:16000,parameterEvidence:'provider-declared',
   ...(version===2?{capabilityRevision:digest(p),parameterPolicy:{...clone(p),fields:Object.fromEntries(Object.entries(p.fields).map(([k,{wireName,...v}])=>[k,v]))}}:
     {parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}})}));
  const body={format:'modelmirror.rpg.control-model-catalog/'+version,source:'control-plane-cache',catalogId:'parameter-test',models};return {...body,revision:digest(body)};
 };
 f.selection=(purpose,values={})=>{const c=f.catalog(),e=c.models.find(m=>m.selectionId===purpose);return {format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile:profile(purpose,values)};};
 f.resolve=p=>JSON.parse(execFileSync(python,['-X','utf8','-B','-c',resolver,parameterSource],{input:JSON.stringify({policy:f.policies[p.selectionId],parameters:p.parameters,purpose:p.purpose,capabilityRevision:p.capabilityRevision}),encoding:'utf8',windowsHide:true}));
 const json=(res,value,status=200)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(value));};
 let dropped=false;
 const server=createServer(async(req,res)=>{
  try {
   assert.equal(req.headers.authorization,'Bearer '+token);const url=new URL(req.url,'http://localhost');
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const p=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
   f.requests.push({method:req.method,path:req.url,body:p});
   if(options.live&&url.pathname.startsWith('/api/rpg/v1/authoring/live/'))return json(res,await options.live(url,req.method,f));
   if(url.pathname.endsWith('/models'))return f.unavailable?json(res,{},503):json(res,f.catalog(url.searchParams.get('parameterVersion')==='2'?2:1));
   if(url.pathname.endsWith('/readiness'))return json(res,{format:'modelmirror.rpg.authoring-readiness/1',executionEnabled:true,dispatched:false});
   if(url.pathname.endsWith('/parameters/validate')) {
    if(f.unavailable)return json(res,{},503);
    const c=f.catalog(p.format?2:1),e=c.models.find(m=>m.selectionId===p.selectionId);
    if(!e||c.revision!==p.catalogRevision||e.selectionRevision!==p.selectionRevision||p.format&&e.capabilityRevision!==p.capabilityRevision)return json(res,{},409);
    const resolution=p.format?f.resolve(p):null;if(resolution?.error)return json(res,resolution,422);
    if(f.onCheck)await f.onCheck(p);
    return json(res,{format:'modelmirror.rpg.parameter-check/'+(p.format?2:1),catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,
     ...(p.format?{resolution}:{wireParameters:{max_tokens:p.parameters.maxOutputTokens}}),dispatched:false});
   }
   if(url.pathname==='/api/rpg/v1/authoring/tasks'&&req.method==='POST') {
    assert.equal(p.format,'modelmirror.rpg.authoring-task/2');const key=p.sessionId+'/'+p.requestId;assert.ok(!f.results.has(key),'duplicate task POST');
    const resolution=f.resolve(p);assert.ok(!resolution.error);f.tasks.push(clone(p));
    const raw=options.text?.(p)??(p.purpose==='memory'?'{"operations":[]}':p.purpose==='story'?'A complete original reply.':p.purpose==='summary'&&options.compression?'L'.repeat(10001):'A shorter summary.');
    // Memory is raw protocol JSON; no model-specific repair or fence handling.
    const output=p.purpose==='memory'&&!options.text?'{"operations":[]}':raw;
    const dispatched=options.dispatched!==false,status=dispatched?'completed':'failed',sse=dispatched?'fake-sse:'+output:'',actualRaw=dispatched?output:'';
    const receipt={format:'modelmirror.rpg.authoring-task-receipt/2',gateway:'rpg_authoring',requestHash:digest(p),sourceRequestHash:p.sourceRequestHash,
     sessionId:p.sessionId,requestId:p.requestId,operationId:p.operationId,purpose:p.purpose,selectionId:p.selectionId,selectionRevision:p.selectionRevision,catalogRevision:p.catalogRevision,
     capabilityRevision:p.capabilityRevision,parameterResolution:dispatched?resolution:null,providerRequestHash:dispatched?digest({model:'synthetic/'+p.selectionId,messages:p.messages,...resolution.wireParameters}):null,
     requestedModel:dispatched?'synthetic/'+p.selectionId:null,actualModel:dispatched?'synthetic/'+p.selectionId:null,parameters:dispatched?resolution.wireParameters:null,
     runId:dispatched?'run-'+f.tasks.length:null,attemptId:dispatched?'attempt-'+f.tasks.length:null,dispatched,status,finishReason:dispatched?'stop':null,error:dispatched?null:'rpg_authoring_execution_disabled',httpStatus:dispatched?200:null,usage:null,retries:0,rawHash:sha256(actualRaw),sseHash:sha256(sse)};
    const result={status,requestHash:receipt.requestHash,raw:actualRaw,sse,receipt:{...receipt,sha256:digest(receipt)}};f.results.set(key,result);
    if(options.onTask)await options.onTask(p,f);
    if(options.dropPurpose===p.purpose&&!dropped){dropped=true;res.destroy();return;}
    const returned=clone(result);if(f.corrupt)f.corrupt(returned);return json(res,returned);
   }
   if(req.method==='GET'&&url.pathname.startsWith('/api/rpg/v1/authoring/tasks/')) {
    f.lookups.push(req.url);const result=f.results.get(url.pathname.slice('/api/rpg/v1/authoring/tasks/'.length));
    if(result)assert.equal(result.receipt.sourceRequestHash,url.searchParams.get('sourceRequestHash'));
    return json(res,result??{status:'absent',sourceRequestHash:url.searchParams.get('sourceRequestHash')});
   }
   throw new Error('unexpected fixture route '+req.url);
  } catch(error){f.error=error;return json(res,{error:'fixture-error'},500);}
 });
 await new Promise((r,reject)=>server.once('error',reject).listen(options.port??0,'127.0.0.1',r));f.origin='http://127.0.0.1:'+server.address().port;
 f.control=new ControlPlaneModelControl({origin:f.origin,serviceToken:token,timeoutMs:options.controlTimeout??5000});await f.control.refresh();
 t.after(async()=>{server.closeAllConnections();await new Promise(r=>server.close(r));if(f.error)throw f.error;});return f;
}
