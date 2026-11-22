import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from '../book-library/preview.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';
import {characterDocument} from '../document.mjs';
import {id} from '../revisions/contracts.mjs';
import {upgradeCard} from './metadata.mjs';

export async function startV3Preview(directory,port=0,groupPort=0){
 const host=await startPreview(directory,port,groupPort);
 try{
  const doc=characterDocument(blankDocument()),card=doc.character.card;
  upgradeCard(card,()=>1800000000000);
  Object.assign(card.data,{name:'潮汐记录员 · 中性夹具',nickname:'小潮',description:'在渡口记录水位。',first_mes:'今天想查哪一条潮位记录？',
   creator_notes_multilingual:{zh:'离线编辑与往返验收。'},source:['neutral-tide-fixture'],group_only_greetings:['大家好，潮位记录已经摊开。'],
   mes_example:'<START>\r\n{{user}}: 我想看看记录。\r\n{{char}}: 请翻到带蓝签的那页。',
   character_book:{extensions:{},entries:[{name:'潮位编号',keys:['/^潮[0-9]{2}$/mu'],content:'登记簿在木柜第二层。',enabled:true,use_regex:true,extensions:{},insertion_order:0}]},
   extensions:{neutral:{opaque:[0,false,'潮']}}});doc.metadata.name=card.data.name;
  const works=host.workspace.works,w=await works.create({operationId:'v3-tide-fixture',expectedRevision:null,document:doc});
  const sealed=await works.publish(w.workId,{operationId:'v3-neutral-seal',expectedRevision:0}),reference={workId:w.workId,versionId:sealed.result.versionId};
  const session=await host.workspace.single.create({operationId:'v3-neutral-session',reference,openingIndex:0,player:{id:null,name:'行旅人'}});
  const info=await host.api('source',{kind:'single',sourceId:session.id});
  let branch;
  const shelf=await host.api('catalog');
  const existing=shelf.families.find(f=>f.id===id('book-family','v3-neutral-open'));
  if(existing)branch={familyId:existing.id,branchId:existing.branches[0].id};
  branch??=await host.write('open',{kind:'single',sourceId:session.id,sourceHash:info.sourceHash,session:[],player:[],regexPolicy:'v3-bounded/1'},'v3-neutral-open');
  const page=new URL('/characters',host.url).href;
  return {...host,workId:w.workId,editor:page+'?work='+w.workId+'&section=metadata',branchUrl:page+'?books=1&family='+branch.familyId+'&branch='+branch.branchId+'#branches'};
 }catch(error){await host.close();throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const p=await startV3Preview(resolve(process.argv[2]),Number(process.argv[3]??0),Number(process.argv[4]??0));
 console.log(JSON.stringify({editor:p.editor,branch:p.branchUrl,mode:'offline-fixture'}));
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await p.close();process.exit(0);});
}