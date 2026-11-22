import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {timingPolicy} from './timing.mjs';

export async function startTimingPreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','bell-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','bell-save'),draft=catalog.books.find(b=>b.id===bookId);
      const row=(name,rule,body)=>({name,keys:['报时'],content:rule+'\n'+body,enabled:true,insertion_order:0,extensions:{}});
      if(!draft)await host.write('save',{bookId:null,title:'渡口钟簿',book:{extensions:{},entries:[
        row('钟牌','@@activate_only_after 2','钟牌挂在门侧。'),row('双响','@@activate_only_every 2','值守人核对两次钟响。')]}},'bell-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'bell-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,timingPolicy,
        player:[],session:[{ref,readers:'all'}]},'bell-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startTimingPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
