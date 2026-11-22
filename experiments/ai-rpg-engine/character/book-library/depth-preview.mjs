import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {depthPolicy} from './depth.mjs';

export async function startDepthPreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog');
    const familyId=id('book-family','depth-neutral-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','depth-neutral-save'),draft=catalog.books.find(b=>b.id===bookId);
      const row=(depth,name,body)=>({name,keys:['水位'],content:'@@scan_depth '+depth+'\n'+body,
        enabled:true,extensions:{},insertion_order:depth});
      if(!draft)await host.write('save',{bookId:null,title:'水尺手册',book:{scan_depth:0,extensions:{},
        entries:[row(1,'眼前的水尺','水尺上的白线标出平水位。'),row(2,'上一条观测','木亭里挂着水尺图。')]}},'depth-neutral-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'depth-neutral-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id;
      const info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,depthPolicy,
        player:[],session:[{ref,readers:'all'}]},'depth-neutral-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startDepthPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
