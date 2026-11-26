import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {startPreview} from './preview.mjs';
import {characterDocument} from '../document.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {oncePolicy} from './once.mjs';
import {keepPolicy} from './keep.mjs';
import {cooldownPolicy,cooldownField} from './cooldown.mjs';
import {id} from '../revisions/contracts.mjs';

export async function startTemplatePreview(dir){
  const host=await startPreview(dir);
  try{
    const works=host.workspace.works,doc=characterDocument(blankDocument());
    doc.metadata.name='钟楼记录员';
    const card=doc.character.card;
    Object.assign(card.data,{name:'钟楼记录员',description:'使用石板记录。',first_mes:'钟楼今天开放。',scenario:'旧钟楼'});
    const entry=(name,content,extensions={})=>({name,content,extensions,keys:['钟楼'],insertion_order:1,enabled:true,use_regex:false});
    card.data.character_book={extensions:{},entries:[
      entry('登记章','@@dont_activate_after_match\n首次盖蓝章。'),entry('窗灯','@@keep_activate_after_match\n挂起白灯。',{[cooldownField]:2}),entry('值班牌','@@dont_activate_after_match\n翻开旧牌。')]};
    upgradeCard(card,()=>0);
    const {workId}=await works.create({operationId:'tower-create',expectedRevision:null,document:doc});
    const v1=(await works.publish(workId,{operationId:'tower-first',expectedRevision:0})).result.versionId;
    const w=await works.read(workId);
    if(w.versions.length===1){
      const next=w.draft,c=next.character.card.data;
      c.description='使用铜牌记录。';c.scenario='新钟楼';
      const [stamp,lamp,sign]=c.character_book.entries;
      c.character_book.entries=[lamp,{...sign,content:'@@dont_activate_after_match\n翻开新牌。'},stamp];
      const saved=await works.save(workId,{expectedRevision:w.revision,operationId:'tower-edit',document:next});
      await works.publish(workId,{operationId:'tower-second',expectedRevision:saved.revision});
    }
    const v2=(await works.read(workId)).versions[1].id,reference={workId,versionId:v1};
    const player={id:null,name:'巡查员'};
    const single=await host.workspace.single.create({reference,player,openingIndex:0,operationId:'tower-single'});
    const group=await host.store.create({operationId:'tower-group',expectedRevision:null,payload:{player,scene:'公共记录室',members:[0,1].map(()=>({reference,modelId:'fixture'})),opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:16,microUsd:100000}}});
    const roots={};
    for(const [kind,sourceId] of [['single',single.id],['group',group.id]]){
      const op='tower-open-'+kind,familyId=id('book-family',op);
      const found=(await host.api('catalog')).families.find(f=>f.id===familyId);
      roots[kind]=found?{familyId,branchId:found.branches[0].id}:await host.write('open',{kind,sourceId,sourceHash:(await host.api('source',{kind,sourceId})).sourceHash,player:[],session:[],oncePolicy,keepPolicy,cooldownPolicy},op);
    }
    const link=kind=>host.url+'&family='+roots[kind].familyId+'&branch='+roots[kind].branchId+'#branches';
    return {...host,roots,groupId:group.id,workId,v1,v2,links:{single:link('single'),group:link('group'),card:new URL('/characters?work='+workId+'&version='+v2,host.url).href}};
  }catch(e){await host.close();throw e;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  const h=await startTemplatePreview(resolve(process.argv[2]));console.log(JSON.stringify(h.links));
  for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await h.close();process.exit(0);});
}
