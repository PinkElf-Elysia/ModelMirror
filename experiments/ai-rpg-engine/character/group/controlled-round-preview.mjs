import {resolve,join} from 'node:path';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {mockControl,syntheticToken} from '../revisions/mock-control.mjs';
import {characterDocument} from '../document.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';
import {startRoundWorkspace} from './controlled-workspace.mjs';
import {digest} from './contracts.mjs';

export async function roundFixtures(app,models){
  const members=[];
  for(const [i,name] of ['钟楼抄录员','河岸测量员'].entries()){
    const d=characterDocument(blankDocument());d.metadata.name=d.character.card.data.name=name;
    Object.assign(d.character.card.data,{description:'原创中性验收夹具',first_mes:'今天记录河面的水位。'});
    const w=await app.studio.works.create({operationId:'river-work-'+i,expectedRevision:null,document:d});
    const v=await app.studio.works.publish(w.workId,{operationId:'river-version-'+i,expectedRevision:0});
    members.push({reference:{workId:w.workId,versionId:v.result.versionId},modelId:models[i].id});
  }
  const payload={player:{id:null,name:'观察员'},scene:'河岸记录室',members,opening:null,budget:{calls:20,microUsd:500000}};
  const group=await app.groupStore.create({operationId:'river-group',expectedRevision:null,payload});
  return {id:group.id,payload};
}
export async function roundModels(mock){
  return Promise.all(['story','summary'].map(async name=>{
    const choice=await mock.choice(name);
    return {id:choice.entry.modelId,label:'本机模拟 · '+name,choice,contextTokens:choice.entry.contextLength,outputTokens:choice.wireParameters.max_tokens,
      price:{revision:'offline-price/1',inputMicroUsdPerMillion:10000,outputMicroUsdPerMillion:20000,overheadTokens:128}};
  }));
}
export const mockPolicy=models=>({authorizationId:'rounds-local',maxCalls:3,maxMicroUsd:20000,certifiedModels:models.map(digest)});
export const mockPrices=models=>id=>{
  const model=models.find(m=>m.id===id);
  return model&&{modelHash:digest(model),priceRevision:model.price.revision,includesReasoning:true,validUntil:Date.now()+60000};
};
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const directory=resolve(process.argv[2]);await mkdir(directory,{recursive:true});
  const portFile=join(directory,'offline-port.json');
  let previousPort;
  try{previousPort=JSON.parse(await readFile(portFile,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
  if(previousPort!==undefined&&(!Number.isInteger(previousPort)||previousPort<1||previousPort>65535))throw Error('Invalid saved mock port');
  if(previousPort&&process.argv[3]&&Number(process.argv[3])!==previousPort)throw Error('Mock endpoint changed');
  const cleanup=[],mock=await mockControl({after:fn=>cleanup.push(fn)},{port:previousPort??Number(process.argv[3]??0),
    statePath:join(directory,'offline-results.json'),onDispatch:()=>new Promise(r=>setTimeout(r,1100)),
    text:p=>p.selectionId===mock.catalog.models[0].selectionId?'蓝牌已经挂在岸边。':'我记录了蓝牌的位置。'});
  if(previousPort===undefined)await writeFile(portFile,JSON.stringify(Number(new URL(mock.origin).port)),{flag:'wx'});
  const models=await roundModels(mock);
  const app=await startRoundWorkspace({directory,upstream:mock.origin,serviceToken:syntheticToken,models,port:0,
    authorization:{id:'rounds-local',calls:24,microUsd:500000,initial:{calls:1,microUsd:4000}},
    roundPolicy:mockPolicy(models),priceEvidence:mockPrices(models),executionLabel:'本机模拟网关 · 仅中性夹具'});
  const {id}=await roundFixtures(app,models);
  console.log(JSON.stringify({url:app.workspace.groupUrl+'?id='+id,realCalls:0}));
  for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await app.close();for(const fn of cleanup)await fn();process.exit(0);});
}
