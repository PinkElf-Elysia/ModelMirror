import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {hash,need} from '../../../agent-core/contracts.mjs';
export const MODEL='google/gemini-3.8-flash';
export class ProposalProvider extends NativeBridgeProvider{
 constructor(options){
  const {authorization:a,selection:s}=options;
  need(a.maximumCalls===1&&a.maximumUSD===.20&&a.perCallUSD===.20,'E2_AUTHORIZATION');
  need(a.pricing.inputUSD>0&&a.pricing.inputUSD<=.00000075&&a.pricing.outputUSD>0&&a.pricing.outputUSD<=.00000375&&a.pricing.quoteId,'E2_PRICE');
  need(s.model===MODEL&&s.available&&hash(s.parameters)===hash({temperature:.7,top_p:.8,max_tokens:8192}),'E2_SELECTION');
  need(options.url.protocol==='http:'&&options.url.hostname==='127.0.0.1'&&options.serviceToken.length>=32,'E2_LOCAL_BRIDGE');
  super(options);this.parameterProfile='core-query-r3';this.kind=options.mode==='real'?'controlled-e2-gemini':'controlled-e2-mock';
 }
 async request(path,body,signal=AbortSignal.timeout(245000)){
  const target=path.startsWith('../progress/')?'/api/rpg/v1/'+path.slice(3):'/api/rpg/v1/e2-retest/'+path+(path==='chat/completions'?'?profile=core-query-r3':'');
  const response=await this.fetcher(new URL(target,this.url),{method:body?'POST':'GET',redirect:'error',headers:{Authorization:'Bearer '+this.serviceToken,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal});
  const raw=await response.text();need(Buffer.byteLength(raw)<=32*1024*1024&&!raw.includes(this.serviceToken),'E2_RESPONSE');
  return {status:response.status,body:JSON.parse(raw)};
 }
 generate(session,id,request){
  need(session==='e2-proposal-retest-'+this.mode+'-v1'&&id==='proposal-1','E2_REQUEST_ID');
  need(Object.keys(request).sort().join()==='messages,tool_choice,tools'&&request.tool_choice==='none'&&request.tools.length===0,'E2_PROPOSAL_ONLY');
  return super.generate(session,id,request);
 }
}
