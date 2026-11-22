import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';

export async function startDisplayPreview(directory,port=0,groupPort=0){
  const h=await startPreview(directory,port,groupPort);
  try{
    const catalog=await h.api('catalog'),familyId=id('book-family','display-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await h.api('source',{kind:'group',sourceId});
      ({branchId}=await h.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,player:[],session:[]},'display-open'));
    }
    const loc={familyId,branchId},view=await h.api('view',{...loc,targetId:null});
    const operationId='display-seed',requestId=id('request',familyId,operationId);
    if(!view.requests.some(r=>r.id===requestId)){
      const intent={branchId,targetId:view.targetId,messageId:null,input:'东岸挂起蓝牌。\\n\\n西岸记录水位。'};
      const p=await h.api('preview',{familyId,intent,operationId});
      await h.api('send',{operationId,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}});
    }
    const url=new URL(h.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...h,...loc,requestId,url:url.href};
  }catch(error){await h.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startDisplayPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
