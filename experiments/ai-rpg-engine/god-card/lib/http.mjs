import {memoryHttp,memoryProjection} from '../../studio/memory-http.mjs';
import {createGodStore} from './adapter.mjs';
import {loadGodPlugins,loadGodPluginPackages} from './plugins.mjs';
import {projection} from '../../card-replica/lib/store.mjs';
import {cardDisplay} from '../../card-replica/lib/card-presentation.mjs';
import {REVIEWED_PLUGIN_IDS,sha,canonical,fail} from '../../plugins/catalog.mjs';
export async function createGodHost({directory,generate,branches=false,features=false,realControl=null}){
 let integrated=null;
 if(realControl&&!features)throw fail('GOD_REAL_PLUGIN_HOST_REQUIRED');
 if(realControl){const {createGodRealStore}=await import('./real-host.mjs');integrated=await createGodRealStore(directory,realControl);}
 else if(features){const {createGodPluginStore}=await import('./plugin-host.mjs');integrated=await createGodPluginStore(directory,generate);}
 const store=integrated?.store||createGodStore(directory,generate);if(!integrated)await store.init();
 const status=()=>({id:'god',name:'神临凡尘',providerEnabled:false,enabled:false,mode:'offline-only',model:null,parameters:null,used:0,limit:0,remaining:0,contextPolicy:features?'default:complete-raw-history; actual policy is per-session':'complete-raw-history',offlineSimulation:features,pluginCompatibility:features?Object.fromEntries(REVIEWED_PLUGIN_IDS.map(id=>[id,'offline-candidate'])):branches?{'rpg.branch-save':'available',other:'not-compatible'}:'pending-B3',...(realControl?{providerEnabled:true,enabled:true,mode:'controlled-real',offlineSimulation:false,model:integrated.profile.initialSelection().model,parameters:integrated.profile.initialSelection().parameters,pluginCompatibility:Object.fromEntries(REVIEWED_PLUGIN_IDS.map(id=>[id,'real-candidate']))}:{})});
 const view=s=>({...projection(s),...(features?memoryProjection(s):{}),...(s.historyWindow?{historyWindow:{config:s.historyWindow.config,configRevision:s.historyWindow.revision,pendingOperationId:s.historyWindow.pending}}:{}),...(s.modelState?{modelSelection:{revision:s.modelState.revision,current:s.modelState.current}}:{}),cardId:'god',runtime:store.status(s),...(s.provenance?{parentId:s.provenance.parentId,branchTurn:s.provenance.turn}:{}),turns:s.turns.map(t=>({...t,html:cardDisplay(t.raw,'god')}))});
 const pluginSession=async id=>{const s=await store.read(id);if(features&&s.runtime?.format===5)return store.pluginSession(id);return {id,cardId:'god',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(canonical(s.runtime)),busy:store.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')};};
 async function handle({route,method,data,plugins,send,query=new URLSearchParams()}){
  if(method==='GET'&&route==='api/status'){const budget=features?await integrated.transport.status():null;return send(200,{...status(),...(realControl?budget:{}),...(features?{[realControl?'budget':'offlineBudget']:budget}:{})});}
  if(route==='api/sessions'){
   if(method==='GET')return send(200,await store.list());
   if(method==='POST')return send(201,view(await store.create(data)));
  }
  if(features&&await memoryHttp({route,method,data,query,store,plugins,view,send}))return;
  const settings=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(history-window|model-catalog|model-selection)$/);
  if(settings){
   if(!features)throw fail('PLUGIN_CARD_INCOMPATIBLE');
   const [,id,kind]=settings;await store.read(id);
   if(kind==='history-window'){
    if([...query.keys()].some(k=>k!=='operationId')||query.getAll('operationId').length>1)throw fail('HISTORY_OPERATION_INVALID',400);
    if(method==='GET')return send(200,{...await store.historyWindowStatus(id,plugins),...(query.has('operationId')?{operation:await store.historyOperation(id,query.get('operationId'))}:{})});
    if(method==='POST'){if(query.size)throw fail('HISTORY_OPERATION_INVALID',400);const r=await store.saveHistoryWindow(id,data,plugins);return send(200,{...r,session:view(r.session)});}
   }else{
    if(query.size)throw fail('MODEL_OPERATION_INVALID',400);
    if(method==='GET'&&kind==='model-catalog')return send(200,{models:await store.catalog(id,plugins)});
    if(method==='GET'&&kind==='model-selection'){const s=await store.read(id),grant=await plugins.sessionStatus(id,'rpg.model-selector');let ready=false;try{await store.requireSelectable(s);ready=true;}catch{}return send(200,{modelSelection:s.modelState?{revision:s.modelState.revision,current:s.modelState.current}:null,runtime:store.status(s),canSelect:ready&&grant.enabled&&!store.running.has(id)&&!store.locks.has(id)});}
    if(method==='POST'&&kind==='model-selection'){const r=await store.select(id,data,plugins);return send(200,{...r,session:view(r.session)});}
   }
   throw fail('GOD_OPERATION_INVALID',400);
  }
  const branch=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(branches|plugins\/rpg\.branch-save\/nodes)$/);
  if(branch){
   await store.read(branch[1]);
   if(!branches&&!features)throw fail('PLUGIN_CARD_INCOMPATIBLE');
   if(method==='POST'&&branch[2]==='branches'){const result=await store.createBranch(branch[1],data,plugins);return send(result.replayed?200:201,{...result,session:view(result.session)});}
   if(method==='GET'&&branch[2]==='plugins/rpg.branch-save/nodes')return send(200,await store.nodes(branch[1],plugins));
  }
  const p=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/plugins\/([a-z0-9.-]+)(?:\/(enable|disable))?$/);
  if(p){
   await store.read(p[1]);
   if(!REVIEWED_PLUGIN_IDS.includes(p[2]))return send(404,{error:'PLUGIN_UNKNOWN'});
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   if(method==='GET'&&!p[3])return send(200,await plugins.sessionStatus(p[1],p[2]));
   if(method==='POST'&&p[3]){
    if(data?.sessionId!==p[1]||data?.pluginId!==p[2])throw fail('PLUGIN_SESSION_BINDING',400);
    return send(200,await plugins.change(p[3],data));
   }
  }
  const m=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)(?:\/(send|cancel))?$/);
  if(m){
   if(method==='GET'&&!m[2])return send(200,view(await store.read(m[1])));
   if(method==='POST'&&m[2]==='send'){const result=await store.send(m[1],data);return send(200,features?{...view(await store.read(m[1])),operation:{status:result.status,error:result.error}}:view(result));}
   if(method==='POST'&&m[2]==='cancel'){await store.read(m[1]);if(Object.keys(data).length)throw fail('GOD_CANCEL_FIELDS_INVALID',400);return send(200,store.cancel(m[1]));}
  }
  return send(404,{error:'NOT_FOUND'});
 }
 return {store,status,publicStatus:async()=>({...status(),...(realControl?await integrated.transport.status():{})}),pluginSession,handle,connect:plugins=>integrated?.connect(plugins),loadPlugins:features?loadGodPluginPackages:branches?loadGodPlugins:null};
}
