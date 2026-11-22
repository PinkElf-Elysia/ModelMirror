import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from '../book-library/preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {macroPolicy} from './date-choice.mjs';

export async function startMacroPreview(directory,port=0,groupPort=0,clock){
  const host=await startPreview(directory,port,groupPort,clock);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','macro-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','macro-save'),draft=catalog.books.find(b=>b.id===bookId);
      if(!draft)await host.write('save',{bookId:null,title:'渡口值日日签',book:{scan_depth:0,extensions:{},entries:[{
        name:'当日班牌',keys:['查班'],content:'值日日期 {{date}}，班牌为{{pick::蓝灯::白旗}}。',
        enabled:true,insertion_order:0,extensions:{}
      }]}},'macro-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'macro-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,macroPolicy,
        player:[],session:[{ref,readers:'all'}]},'macro-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startMacroPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
