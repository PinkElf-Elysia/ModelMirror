import {createProvider} from './provider.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {fail} from '../plugins/catalog.mjs';
// Bind a guard to this invocation only. Existing transports retain their wire hashes,
// receipts and shared ledger; no credential or configuration enters the plugin.
function guarded(options,args){
 if(typeof args.assertHistoryPolicy!=='function'||typeof args.beginHistoryDispatch!=='function')throw fail('HISTORY_DISPATCH_GUARD_REQUIRED');
 const fetcher=options.fetcher||fetch;
 return {...options,fetcher:async(url,request)=>{if(request?.method!=='POST')return fetcher(url,request);await args.assertHistoryPolicy();const launched=await args.beginHistoryDispatch(()=>fetcher(url,request));return launched.response;}};
}
export async function generateHistoryFixed(options,args){return (await createProvider(guarded(options,args))).generate('earth',args);}
export async function generateHistoryControlled(options,card,args){return (await createControlledProvider(guarded(options,args))).generate(card,args);}
