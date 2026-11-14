import {parseFragment} from 'parse5';
const count=v=>Number.isSafeInteger(v)&&v>=0?v:null;
export const microUSD=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&Number.isSafeInteger(Math.ceil(v*1e6))?Math.ceil(v*1e6):null;
export function visibleText(source){
 const root=parseFragment(String(source).replace(/<!--(?:(?!-->)[\s\S])*$/,''));
 function text(n){if(['#comment','script','style','think','thinking','cot','template'].includes(n.nodeName))return '';return n.nodeName==='#text'?n.value:(n.childNodes||[]).map(text).join('');}
 return text(root).trim();
}
export function normalizeUsage(value,source='provider-sse'){
 const v=value&&typeof value==='object'?value:{},input=count(v.prompt_tokens),output=count(v.completion_tokens),total=count(v.total_tokens);
 const cached=count(v.prompt_tokens_details?.cached_tokens),reasoning=count(v.completion_tokens_details?.reasoning_tokens),write=count(v.prompt_tokens_details?.cache_write_tokens);
 const issues=[];
 if(total!==null&&input!==null&&output!==null&&total!==input+output)issues.push('TOTAL_TOKEN_MISMATCH');
 if(cached!==null&&input!==null&&cached>input)issues.push('CACHE_EXCEEDS_INPUT');
 if(reasoning!==null&&output!==null&&reasoning>output)issues.push('REASONING_EXCEEDS_OUTPUT');
 return {inputTokens:input,outputTokens:output,totalTokens:total,cachedInputTokens:cached,cacheWriteTokens:write,reasoningTokens:reasoning,
  reportedCostMicros:microUSD(v.cost),currency:'USD',source:Object.keys(v).length?source:'missing',issues};
}
export function usageFromSSE(sse){
 let last=null,generationId=null,model=null,frames=0,done=false,invalid=false;
 for(const block of String(sse).replaceAll('\r\n','\n').split('\n\n')){
  const data=block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
  if(!data)continue;if(done){invalid=true;break;}if(data==='[DONE]'){done=true;continue;}
  try{const v=JSON.parse(data);if(v.id&&/^[\w.-]{1,160}$/.test(v.id))generationId=v.id;if(typeof v.model==='string')model=v.model;
   if(v.usage&&typeof v.usage==='object'){last=v.usage;frames++;}
  }catch{invalid=true;}
 }
 return {...normalizeUsage(last),generationId,model,usageFrames:frames,streamComplete:done&&!invalid};
}
export function estimateCost(usage,pricing){
 if(!pricing||pricing.currency!=='USD'||usage.inputTokens===null||usage.outputTokens===null||usage.issues?.length)return null;
 const input=microUSD(pricing.inputPerMillion),output=microUSD(pricing.outputPerMillion);
 if(input===null||output===null)return null;
 // Completion tokens already include reasoning tokens; never add them again.
 const numerator=BigInt(usage.inputTokens)*BigInt(input)+BigInt(usage.outputTokens)*BigInt(output);
 const amount=Number((numerator+999999n)/1000000n);return Number.isSafeInteger(amount)?amount:null;
}
export function accounting(usage,pricing){
 const estimatedCostMicros=estimateCost(usage,pricing);
 return {usage,reportedCostMicros:usage.reportedCostMicros,estimatedCostMicros,
  estimateMethod:estimatedCostMicros===null?null:'frozen-price-no-cache-discount',
  pricing:pricing?structuredClone(pricing):null};
}
export function aggregateMeasurements(attempts){
 const fields=['inputTokens','outputTokens','totalTokens','cachedInputTokens','cacheWriteTokens','reasoningTokens'];
 const usage=Object.fromEntries(fields.map(k=>{const values=attempts.map(a=>a.measurement?.accounting?.usage?.[k]??a.receipt?.usage?.[k]);const known=values.filter(v=>count(v)!==null);return [k,{known:known.reduce((a,b)=>a+b,0),missing:values.length-known.length,total:values.length===known.length?known.reduce((a,b)=>a+b,0):null}];}));
 const costs=attempts.map(a=>a.measurement?.accounting||a.receipt?.measurement?.accounting||{});
 return {attempts:attempts.length,usage,
  reportedCostMicros:costs.reduce((n,c)=>n+(c.reportedCostMicros??0),0),reportedCostMissing:costs.filter(c=>c.reportedCostMicros==null).length,
  estimatedCostMicros:costs.reduce((n,c)=>n+(c.estimatedCostMicros??0),0),estimatedCostMissing:costs.filter(c=>c.estimatedCostMicros==null).length,
  reservedUnknownMicros:attempts.reduce((n,a)=>n+(a.costMicros==null?(a.reservation||0):0),0)};
}
