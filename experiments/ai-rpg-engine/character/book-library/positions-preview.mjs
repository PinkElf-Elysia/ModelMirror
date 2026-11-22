import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {id} from '../revisions/contracts.mjs';
import {positionPolicy} from './positions.mjs';

export async function startPositionsPreview(directory,port=0,groupPort=0,clock){
  const host=await startPreview(directory,port,groupPort,clock);
  try{
    const catalog=await host.api('catalog'),familyId=id('book-family','positions-open');
    let branchId=catalog.families.find(f=>f.id===familyId)?.branches[0].id;
    if(!branchId){
      const bookId=id('book','positions-save'),draft=catalog.books.find(b=>b.id===bookId);
      if(!draft)await host.write('save',{bookId:null,title:'渡口巡查手册',book:{scan_depth:0,extensions:{},entries:[
        ['描述前','@@position before_desc','先核对巡查牌。',0],['描述后','@@position after_desc','携带木尺。',1],
        ['场景','@@position scenario','亭旁有一盏灯。',2],['输入前','@@depth 1','请留意水线。',3],
        ['输入后','@@depth 0','记录当天水位。',4]
      ].map(([name,header,body,insertion_order])=>({name,keys:['巡查'],content:header+'\n'+body,insertion_order,enabled:true,extensions:{}}))}},'positions-save');
      const sealed=draft?.versions[0]??await host.write('seal',{bookId},'positions-seal');
      const sourceId=catalog.sessions.find(s=>s.kind==='group').id,info=await host.api('source',{kind:'group',sourceId});
      const ref={bookId,bookVersionId:sealed.bookVersionId,hash:sealed.hash};
      ({branchId}=await host.write('open',{kind:'group',sourceId,sourceHash:info.sourceHash,positionPolicy,
        player:[],session:[{ref,readers:'all'}]},'positions-open'));
    }
    const url=new URL(host.url);url.searchParams.set('family',familyId);url.searchParams.set('branch',branchId);url.hash='branches';
    return {...host,familyId,branchId,url:url.href};
  }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const host=await startPositionsPreview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
  console.log(JSON.stringify({url:host.url,mode:'offline-fixture'}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await host.close();process.exit(0);});
}
