import {readSseEvents} from '../runtime/node/sse.mjs';
// Text transport only. Never parses narrative as a turn or repairs its content.
export async function receiveMinimalText(payload, {fetchImpl=globalThis.fetch, signal}={}) {
  if(payload?.model_id!=='gpt-5.6-luna'||payload.max_tokens!==4096||payload.temperature!==0||
    payload.require_managed_route!==true||payload.tool_mode!=='none'||payload.compression?.mode!=='off'||
    payload.output_mode!=='none'||payload.gateway!=='default'||Object.hasOwn(payload,'response_format'))throw Error('TEXT_ROUTE_POLICY');
  const controller=AbortSignal.timeout(60000), combined=signal?AbortSignal.any([controller,signal]):controller;
  let text='',receipt=null,finishReason=null,done=false,raw=[],bytes=0,error=null,observedModel=null,httpStatus=null;
  try {
    const response=await fetchImpl('http://127.0.0.1:18305/api/chat',{method:'POST',redirect:'error',headers:{'content-type':'application/json',accept:'text/event-stream'},body:JSON.stringify(payload),signal:combined});
    httpStatus=response.status;
    if(!response.ok||!response.headers.get('content-type')?.includes('text/event-stream'))throw Error('HTTP_'+response.status);
    const capture={async *[Symbol.asyncIterator](){for await(const b of response.body){bytes+=b.length;if(bytes>8*1024*1024)throw Error('STREAM_LIMIT');raw.push(Buffer.from(b));yield b;}}};
    const parsed=await readSseEvents(capture,{onEvent(event){
      if(done)throw Error('EVENT_AFTER_DONE');
      if(event.data==='[DONE]'){if(!receipt)throw Error('MISSING_RECEIPT');done=true;return;}
      const value=JSON.parse(event.data);
      if(event.event==='route_receipt'){
        if(receipt||!finishReason)throw Error('RECEIPT_ORDER');
        receipt=value;return;
      }
      if(event.event!=='message'||receipt)throw Error('UNEXPECTED_EVENT');
      if(value.error)throw Error('UPSTREAM_ERROR');
      if(value.model){if(value.model!=='gpt-5.6-luna')throw Error('MODEL_MISMATCH');observedModel=value.model;}
      if(value.choices?.length===0&&value.usage)return;
      if(finishReason||!Array.isArray(value.choices)||value.choices.length!==1)throw Error('CHOICE_ORDER');
      const choice=value.choices[0];
      if(choice?.delta?.refusal)throw Error('REFUSAL');
      if(choice?.delta?.tool_calls||choice?.delta?.function_call)throw Error('UNEXPECTED_TOOL');
      if(typeof choice?.delta?.content==='string')text+=choice.delta.content;
      if(choice?.finish_reason)finishReason=choice.finish_reason;
    }});
    if(!parsed.valid)throw Error(parsed.diagnostics[0].code);
    if(!done||finishReason!=='stop'||receipt?.actual_model!=='gpt-5.6-luna'||receipt?.requested_model!=='gpt-5.6-luna'||
      receipt?.fallback_attempts!==0||!receipt?.reason_codes?.includes('qualified')||
      !['newapi_preferred','newapi_required_default'].includes(receipt?.strategy)||receipt.engine!=='newapi')
      throw Error('INCOMPLETE_OR_ROUTE_MISMATCH');
  }catch(e){error=combined.aborted?'CANCELLED_OR_TIMEOUT':e.code??e.message??'UNKNOWN';}
  return {status:error?'failed_or_unknown':'transport_complete',text,raw:Buffer.concat(raw),receipt,finishReason,done,observedModel,httpStatus,error};
}
