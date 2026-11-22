import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {fallbackPolicy} from './fallback.mjs';
import {depthPolicy} from './depth.mjs';
import {keepPolicy} from './keep.mjs';

export async function startKeepPreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','keep-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','keep-save'),draft=catalog.books.find(b=>b.id===bookId);
      const row=(name,rule,body)=>({name,keys:['蓝绳'],content:rule+'\n'+body,enabled:true,insertion_order:0,extensions:{}});
      if(!draft)await host.write('save',{bookId:null,title:'系舟手记',book:{extensions:{},entries:[
        row('系绳位置','@@keep_activate_after_match\n@@scan_depth 1','绳结系在第三根木桩上。'),
        row('当次标记','@@scan_depth 1','记录员画下本次标记。')]}},'keep-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'keep-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,keepPolicy,depthPolicy,fallbackPolicy,
        player:[],session:[{ref,readers:'all'}]},'keep-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startKeepPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
