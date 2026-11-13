// Synthetic crash fixture only: process kill deliberately leaves owner locks.
import {createGenerationCards,GENERATION_CARDS} from './generation-cards.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
import {ControlPlaneModelControl} from '../authoring/model-control.mjs';
import {randomUUID} from 'node:crypto';
process.on('message',async config=>{
 try{
  const control=new ControlPlaneModelControl({origin:config.origin,serviceToken:config.serviceToken});await control.refresh();
  const catalog=await control.parameterCatalog(),model=catalog.models.find(x=>x.available);
  const host=await createGenerationCards({...config,control,defaultModel:model.modelId,limits:Object.fromEntries(Object.keys(GENERATION_CARDS).map(k=>[k,12])),comparison:true});
  const records=[];
  for(const [card,h]of host.hosts){
   const input=card==='earth'?{characterText:'姓名：林舟\n身份：成年旅行者',world:'表世界'}:card==='god'?{setup:newSetup()}:{opening:'姓名：林舟\n身份：成年旅行者'},s=await h.store.create(input);
   let c=await host.plugins.catalog(),e=c.plugins.find(p=>p.id==='rpg.model-comparison');
   if(!e.installed){await host.plugins.change('install',{operationId:randomUUID(),pluginId:e.id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision});c=await host.plugins.catalog();e=c.plugins.find(p=>p.id==='rpg.model-comparison');}
   await host.plugins.change('enable',{operationId:randomUUID(),pluginId:e.id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:e.permissions});
   const state=await h.comparisonAdapter.comparisonInspect(s.id),candidate=temp=>({format:'modelmirror.rpg.parameter-selection/2',catalogRevision:catalog.revision,selectionId:model.selectionId,selectionRevision:model.selectionRevision,capabilityRevision:model.capabilityRevision,profile:{format:'modelmirror.rpg.parameter-profile/1',purpose:'story',modelId:model.modelId,parameters:{format:'modelmirror.rpg.parameter-config/2',values:{temperature:{mode:'explicit',value:temp}}}}});
   const b=await h.comparison.create({sessionId:s.id,operationId:randomUUID(),expectedContextRevision:state.contextRevision,expectedAuthorizationRevision:state.authorizationRevision,input:s.characterText,candidates:[{id:'a',choice:candidate(.2)},{id:'b',choice:candidate(.9)}]});
   const done=await h.comparison.wait(s.id,b.plan.id);
   records.push({card,id:s.id,batchId:b.plan.id,batch:done,session:await h.store.read(s.id),budget:await h.io.budget.status()});
  }
  process.send({records,defaultModel:model.modelId,plugins:await host.plugins.catalog()});
 }catch(e){process.send({error:e.stack});process.exitCode=1;}
});
