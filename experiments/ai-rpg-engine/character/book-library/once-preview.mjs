import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {oncePolicy} from './once.mjs';

export async function startOncePreview(directory,port=0,groupPort=0){
  const host=await startPreview(directory,port,groupPort);
  try{
    const catalog=await host.api('catalog');
    const existing=catalog.families.find(f=>f.id===id('book-family','once-neutral-open'));
    let familyId,branchId;
    if(existing){familyId=existing.id;branchId=existing.branches[0].id;}
    else{
      const bookId=id('book','once-neutral-save'),draft=catalog.books.find(book=>book.id===bookId);
      if(!draft)await host.write('save',{bookId:null,title:'渡口登记簿',book:{extensions:{},entries:[{
        name:'蓝盒钥匙',keys:['登记簿'],content:'@@dont_activate_after_match\n钥匙放在蓝盒中。',enabled:true,extensions:{},insertion_order:0
      }]}},'once-neutral-save');
      const ref=draft?.versions[0]??await host.write('seal',{bookId},'once-neutral-seal');
      const group=catalog.sessions.find(session=>session.kind==='group');
      const source=await host.api('source',{kind:'group',sourceId:group.id});
      ({familyId,branchId}=await host.write('open',{kind:'group',sourceId:group.id,sourceHash:source.sourceHash,
        player:[],session:[{ref:{bookId,bookVersionId:ref.bookVersionId,hash:ref.hash},readers:'all'}],oncePolicy},'once-neutral-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startOncePreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'local-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
