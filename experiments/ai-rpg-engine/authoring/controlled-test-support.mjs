// Synthetic local HTTP control plane for protocol/integration tests only.
import {createServer} from 'node:http';
import {canonical,sha256} from './contracts.mjs';
import {ControlPlaneModelControl,OfflineModelControl,CONTROL_CATALOG_FORMAT} from './model-control.mjs';
export const syntheticToken='synthetic-authoring-control-token-tests-only';
export const digest=v=>sha256(canonical(v));
export async function fakeControl(t,options={}){
 const offline=new OfflineModelControl({catalogId:'controlled-test',models:['story','summary','memory'].map(k=>({selectionId:sha256(k),selectionRevision:sha256(k+'-revision'),modelId:'synthetic/'+k,name:k,
  available:true,contextLength:256000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))});
 const catalog={...offline.catalog(),format:CONTROL_CATALOG_FORMAT,source:'control-plane-cache'};catalog.models.forEach(e=>e.parameterEvidence='provider-declared');
 const calls=[],lookups=[],records=new Map();let dropped=false;
 const json=(res,value,status=200)=>{res.writeHead(status,{'content-type':'application/json'});res.end(canonical(value));};
 const result=p=>{
  const requestedModel=catalog.models.find(m=>m.selectionId===p.selectionId).modelId;
  const dispatched=options.dispatched!==false;
  const raw=!dispatched?'':options.text?.(p)??(p.purpose==='memory'?'{"operations":[]}':p.purpose==='story'?'Full original story.':'A retained summary.');
  const status=options.status??'completed',finishReason=status==='completed'?(options.finishReason??'stop'):null,sse=dispatched?'synthetic-sse:'+raw:'';
  const c={format:'modelmirror.rpg.authoring-task-receipt/1',gateway:'rpg_authoring',requestHash:digest(p),sourceRequestHash:p.sourceRequestHash,
   sessionId:p.sessionId,requestId:p.requestId,operationId:p.operationId,purpose:p.purpose,selectionId:p.selectionId,selectionRevision:p.selectionRevision,catalogRevision:p.catalogRevision,
   requestedModel,actualModel:requestedModel,parameters:{max_tokens:p.parameters.maxOutputTokens},runId:dispatched?'run-'+calls.length:null,attemptId:dispatched?'attempt-'+calls.length:null,
   dispatched,status,finishReason,error:!dispatched?'rpg_authoring_execution_disabled':status==='completed'?null:'rpg_authoring_result_unknown',httpStatus:200,usage:null,retries:0,rawHash:sha256(raw),sseHash:sha256(sse)};
  return {status,requestHash:c.requestHash,raw,sse,receipt:{...c,sha256:digest(c)}};
 };
 const server=createServer(async(req,res)=>{
  try{
   if(req.headers.authorization!=='Bearer '+syntheticToken)return json(res,{error:'unauthorized'},401);
   const url=new URL(req.url,'http://localhost');
   if(url.pathname.endsWith('/models'))return json(res,catalog);
   if(url.pathname==='/api/rpg/v1/authoring/readiness')return json(res,options.readinessValue??{format:'modelmirror.rpg.authoring-readiness/1',executionEnabled:typeof options.ready==='function'?options.ready():options.ready!==false,dispatched:false},options.readinessStatus??200);
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const p=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
   if(url.pathname.endsWith('/parameters/validate'))return json(res,{format:'modelmirror.rpg.parameter-check/1',catalogRevision:catalog.revision,selectionId:p.selectionId,selectionRevision:p.selectionRevision,wireParameters:{max_tokens:p.parameters.maxOutputTokens},dispatched:false});
   if(url.pathname==='/api/rpg/v1/authoring/tasks'&&req.method==='POST'){
    const key=p.sessionId+'/'+p.requestId;calls.push(p);if(!records.has(key))records.set(key,result(p));
    if(options.onDispatch)await options.onDispatch(p);
    if(options.dropAfterStored&&!dropped&&(!options.dropPurpose||p.purpose===options.dropPurpose)){dropped=true;res.destroy();return;}
    const value=structuredClone(records.get(key));options.corrupt?.(value);return json(res,value);
   }
   if(req.method==='GET'&&url.pathname.startsWith('/api/rpg/v1/authoring/tasks/')){
    lookups.push(req.url);const key=url.pathname.slice('/api/rpg/v1/authoring/tasks/'.length);
    const value=records.get(key);if(value&&value.receipt.sourceRequestHash!==url.searchParams.get('sourceRequestHash'))return json(res,{error:'conflict'},409);
    return json(res,value??{status:'absent',sourceRequestHash:url.searchParams.get('sourceRequestHash')});
   }
   json(res,{error:'not-found'},404);
  }catch{json(res,{error:'test-server-failed'},500);}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>{server.close(r);server.closeAllConnections();}));
 const origin='http://127.0.0.1:'+server.address().port,modelControl=new ControlPlaneModelControl({origin,serviceToken:syntheticToken,ttlMs:30000});await modelControl.refresh();
 return {origin,catalog,modelControl,calls,lookups,records,choice(purpose='story'){const entry=catalog.models.find(e=>e.modelId==='synthetic/'+purpose);return modelControl.select({catalogRevision:catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,parameters:{maxOutputTokens:4096}});}};
}
