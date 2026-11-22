import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {probabilityPolicy,probabilityField} from './probability.mjs';
import {depthPolicy} from './depth.mjs';

export async function startProbabilityPreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','draw-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','draw-save'),draft=catalog.books.find(b=>b.id===bookId);
      const row=(name,body,percent)=>({name,keys:['听风'],content:'@@scan_depth 1\n'+body,enabled:true,insertion_order:0,extensions:{[probabilityField]:percent}});
      if(!draft)await host.write('save',{bookId:null,title:'渡口风声簿',book:{extensions:{},entries:[
        row('风旗','码头的风旗指向东岸。',100),row('风铃','檐下传来一声风铃。',50),
        row('静钟','库房的铜钟响了。',0)]}},'draw-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'draw-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,depthPolicy,probabilityPolicy,
        player:[],session:[{ref,readers:'all'}]},'draw-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startProbabilityPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
