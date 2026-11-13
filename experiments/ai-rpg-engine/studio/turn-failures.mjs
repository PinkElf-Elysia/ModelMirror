import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {sealMemoryTasks} from './memory-task-state.mjs';

// Failed story text is display-only. It never enters history or memory inputs.
export function failedRequestDetails(s) {
 const result={};
 for(const [id,op] of Object.entries({...s.summaryTasks?.operations,...s.memoryTasks?.operations})) {
  if(op.kind!=='send')continue;
  const story=op.tasks.find(t=>t.purpose==='story');
  if(story?.status==='complete')continue;
  const output=story?.output;
  const code=output?.failureCode||(output?.finishReason==='length'?'PROVIDER_OUTPUT_TRUNCATED':output?.finishReason==='content_filter'?'PROVIDER_CONTENT_FILTERED':op.error);
  result[id]={input:op.input.input,partial:typeof output?.raw==='string'?output.raw:'',error:code,outcome:op.status};
 }
 return result;
}

export function dismissRequest(source,key) {
 const s=structuredClone(source);
 if(s.turns.some(t=>t.requestId===key))throw fail('REQUEST_ALREADY_COMPLETED',409);
 for(const name of ['memoryTasks','summaryTasks']) {
  const state=s[name],op=state?.operations?.[key];
  if(!op)continue;
  if(op.kind!=='send'||op.tasks.some(t=>t.purpose==='story'&&t.status==='complete'))throw fail('REQUEST_ALREADY_COMPLETED',409);
  // Keep evidence, leases and usage. Explicit dismissal prevents future recovery
  // from publishing a late response; it does not claim the upstream was unbilled.
  op.status='cancelled';op.cancelRequested=true;
  for(const task of op.tasks)if(['pending','unknown'].includes(task.status))task.status='cancelled';
  if(state.blocked===key)state.blocked=null;
  if(name==='memoryTasks')s[name]=sealMemoryTasks(state);
  else {const {hash,...body}=state;s[name]={...body,hash:sha(canonical(body))};}
 }
 if(s.memoryLegacyInputs?.[key])s.memoryLegacyInputs[key].status='not-applied';
 s.requests[key]={...s.requests[key],status:'cancelled',error:s.requests[key]?.error||'REQUEST_DISMISSED'};
 s.dismissedRequests={...s.dismissedRequests,[key]:true};
 s.revision++;
 return s;
}
