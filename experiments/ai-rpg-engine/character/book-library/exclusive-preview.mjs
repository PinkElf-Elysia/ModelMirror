import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {probabilityPolicy,probabilityField} from './probability.mjs';
import {exclusiveField,exclusivePolicy} from './exclusive.mjs';
import {depthPolicy} from './depth.mjs';

export async function startExclusivePreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','group-choice-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','group-choice-save'),draft=catalog.books.find(b=>b.id===bookId);
      const row=(name,body,priority,percent=100)=>({name,keys:['看信号'],content:'@@scan_depth 1\n'+body,priority,
        enabled:true,insertion_order:0,extensions:{[exclusiveField]:'渡口信号',[probabilityField]:percent}});
      if(!draft)await host.write('save',{bookId:null,title:'渡口信号簿',book:{extensions:{},entries:[
        row('灯号','桅杆上亮起一盏白灯。',1),row('旗号','码头升起一面蓝旗。',2),
        row('钟号','岸边铜钟响了三声。',3,0)]}},'group-choice-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'group-choice-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,depthPolicy,probabilityPolicy,exclusivePolicy,
        player:[],session:[{ref,readers:'all'}]},'group-choice-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startExclusivePreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
