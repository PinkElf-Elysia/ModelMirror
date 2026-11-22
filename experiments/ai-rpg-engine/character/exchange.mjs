import {compileKeys} from './v3/regex.mjs';
import {parseJson,sha256,canonical} from '../authoring/contracts.mjs';
import {CARD_LIMITS,validateCard,compatibility,downgrade,problem} from './card.mjs';
import {pngChunks,metadata,decode64,portrait,exportPng,textKey} from './png.mjs';
const utf8=b=>{try{return new TextDecoder('utf-8',{fatal:true}).decode(b);}catch{problem('CHARACTER_UTF8_INVALID');}};
export function authorCompatibility(card){
 const report=compatibility(card);
 if(card.spec==='chara_card_v3')for(const w of report.warnings){
  if(w.code!=='BOOK_BEHAVIOR_UNSUPPORTED')continue;
  const entry=card.data.character_book.entries[Number(w.path.split('/').pop())];
  if(!entry.use_regex||/^\s*@@/m.test(entry.content))continue;
  const rule=compileKeys(entry.keys);w.code=rule.status??'V3_BRANCH_REGEX';w.message=rule.status?'原模式保留，本条跳过 · '+rule.status+(rule.key===undefined?'':' · keys['+rule.key+']'):'可在启用 V3 受限正则的新分支使用';
 }
 return report;
}
export function inspectImport(bytes,{selection=null}={}){
 const b=Buffer.from(bytes);if(!b.length||b.length>CARD_LIMITS.png)problem('CHARACTER_FILE_SIZE');
 if(b[0]===0x50&&b[1]===0x4b)problem('CHARACTER_CHARX_UNSUPPORTED');
 const png=b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
 let card,cover=null,candidates=[],warnings=[];
 if(!png){if(b.length>CARD_LIMITS.json)problem('CHARACTER_FILE_SIZE');card=validateCard(parseJson(utf8(b),CARD_LIMITS.json));}
 else{
  const chunks=pngChunks(b),items=metadata(chunks);
  if(!items.length)problem('CHARACTER_METADATA_MISSING','',{portraitAvailable:true});
  for(const key of ['ccv3','chara']){
   const matches=items.filter(x=>x.key===key);if(!matches.length)continue;
   const different=new Set(matches.map(x=>x.base64));if(different.size>1)problem('CHARACTER_METADATA_CONFLICT','/'+key);
   try{
    const c=validateCard(parseJson(utf8(decode64(matches[0].base64)),CARD_LIMITS.json));
    if((key==='ccv3')!==(c.spec==='chara_card_v3'))problem('CHARACTER_METADATA_VERSION_MISMATCH');
    candidates.push({key,valid:true,card:c});
   }catch(e){candidates.push({key,valid:false,error:e.code||'CHARACTER_JSON_INVALID'});}
  }
  if(selection!==null&&!['ccv3','chara'].includes(selection))problem('CHARACTER_SELECTION_INVALID');
  const candidate=selection?candidates.find(x=>x.key===selection):candidates.find(x=>x.key==='ccv3')||candidates[0];
  if(!candidate?.valid)problem('CHARACTER_METADATA_SELECTION_REQUIRED','',{candidates:candidates.map(({card,...c})=>c)});
  card=candidate.card;cover=portrait(chunks);
  if(chunks.some(c=>c.type==='acTL'))warnings.push({code:'ANIMATION_UNSUPPORTED',path:'/',message:'原图保留；运行肖像仅显示静态默认画面。'});
  if(selection==='chara'&&candidates.some(c=>c.key==='ccv3'))warnings.push({code:'V2_SELECTED',path:'/',message:'已明确选择 V2 数据；原图仍完整保留。'});
 }
 const report=authorCompatibility(card);report.warnings.push(...warnings);
 return {card,cover,source:{sha256:sha256(b),size:b.length,container:png?'png':'json',selection},report,candidates:candidates.map(({card,...c})=>c)};
}
export function exportCard(card,{format='json',version='original',acceptLoss=false,image,originalPng,now=Date.now}={}){
 validateCard(card);let output=structuredClone(card),losses=[];
 if(version==='v2'){({card:output,losses}=downgrade(output));}
 else if(!['original','v3','v3-backfill'].includes(version))problem('CHARACTER_EXPORT_VERSION');
 let backfill=null;
 if(version.startsWith('v3')&&(output.spec!=='chara_card_v3'||format!=='png'))problem('CHARACTER_EXPORT_VERSION');
 if(output.spec==='chara_card_v3')output.data.modification_date=Math.floor(now()/1000);
 if(version==='v3-backfill'){
  const v2=downgrade(output);backfill=v2.card;losses.push(...v2.losses.map(p=>'V2 副本 '+p));
  backfill.data.creator_notes=['此为 V3 卡的 V2 兼容副本。',backfill.data.creator_notes].filter(Boolean).join('\n');
 }
 if(format==='png'){
  for(const source of new Set([originalPng,image].filter(Boolean))){const chunks=pngChunks(source);
   if(chunks.some(c=>c.type==='acTL'))losses.push('动画仅导出默认静态画面');
   for(const c of chunks){const key=textKey(c);if(key?.startsWith('chara-ext-asset_:'))losses.push('未打包内嵌资源 '+key);}
  }
  for(const asset of output.data.assets??[])if(/^(?:__asset:|embeded:\/\/)/.test(asset.uri))losses.push('仅保留资源引用 '+asset.uri);
 }
 if(losses.length&&!acceptLoss)return {confirmationRequired:true,losses};
 if(!['json','png'].includes(format))problem('CHARACTER_EXPORT_FORMAT');
 return {confirmationRequired:false,losses,mediaType:format==='png'?'image/png':'application/json',bytes:format==='png'?exportPng(output,image,backfill):Buffer.from(canonical(output)+'\n','utf8')};
}
