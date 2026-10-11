import {join} from 'node:path';
import {createMemoryStoreClass} from '../../studio/memory-session-store.mjs';
import {createMemoryTransport} from '../../studio/memory-task-provider.mjs';
import {createGodRealProfile,godRealPolicy,GOD_REAL_MODEL,GOD_REAL_PARAMETER_PROFILE} from './real-profile.mjs';
import {fail} from '../../plugins/catalog.mjs';
// Credentials are supplied by the server process only. No fallback or certification.
export async function createGodRealStore(directory,options){
 const transport=await createMemoryTransport({...options,directory:join(directory,'real-dispatches'),cardId:'god',modelPolicy:godRealPolicy,catalogModels:[GOD_REAL_MODEL],parameterProfile:GOD_REAL_PARAMETER_PROFILE});
 if(!(await transport.status()).enabled)throw fail('CONTROL_DISABLED');
 const selection=(await transport.catalog()).find(m=>m.model===GOD_REAL_MODEL&&m.available);
 const profile=await createGodRealProfile(selection),Store=createMemoryStoreClass(profile);
 const store=new Store(directory,()=>{throw fail('GOD_OFFLINE_FALLBACK_FORBIDDEN');});await store.init();
 return {store,transport,profile,connect:plugins=>store.connectMemory(plugins,transport)};
}
