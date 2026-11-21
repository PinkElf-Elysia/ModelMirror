import { ContextStore } from './store.mjs';
import { MemorySources } from './sources.mjs';
import { RevisionHost } from '../revisions/host.mjs';
import { startContextServer } from '../long-context/server.mjs';

// All entrants use the same coordinator and (in controlled mode) receipt provider and budget guard.
export async function attachMemory({directory, previous, parameters, provider, coordinator, lower,
  charactersUrl, groupsUrl, revisionsUrl, port, guard=null, authorization, recoverDeadOwner=false, legacyHosts=[]}) {
  if (authorization?.mode==='controlled' && !guard) throw Error('SHARED_CONTROL_REQUIRED');
  const sources=new MemorySources(previous.store,lower);
  const store=await ContextStore.open({directory,sources,parameters,authorization,recoverDeadOwner});
  const host=new RevisionHost(store,provider);
  const bindings=[];
  function guardMethod(obj, method, source) {
    const original=obj[method];
    obj[method]=async function(...args){
      const ref=await source(args[0]);
      await store.guardLegacy(ref.kind,ref.id);
      return original.apply(this,args);
    };
    bindings.push(()=>{obj[method]=original;});
  }
  try {
    guardMethod(previous.host,'generate',async fid=>(await previous.store.read(fid)).source);
    for(const h of legacyHosts)guardMethod(h.obj,h.method,h.source);
    const page=await startContextServer({store,host,sources,coordinator,charactersUrl,groupsUrl,revisionsUrl,port,budgetGuard:guard});
    return {store,host,page,async close(){await page.close();for(const restore of bindings.reverse())restore();await store.close();}};
  } catch(error){for(const restore of bindings.reverse())restore();await store.close();throw error;}
}
