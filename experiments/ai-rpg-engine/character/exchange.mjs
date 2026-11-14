import {parseJson,sha256,canonical} from '../authoring/contracts.mjs';
import {CARD_LIMITS,validateCard,compatibility,downgrade,problem} from './card.mjs';
import {pngChunks,metadata,decode64,portrait,exportPng} from './png.mjs';
const utf8=b=>{try{return new TextDecoder('utf-8',{fatal:true}).decode(b);}catch{problem('CHARACTER_UTF8_INVALID');}};
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
 const report=compatibility(card);report.warnings.push(...warnings);
 return {card,cover,source:{sha256:sha256(b),size:b.length,container:png?'png':'json',selection},report,candidates:candidates.map(({card,...c})=>c)};
}
export function exportCard(card,{format='json',version='original',acceptLoss=false,image}={}){
 validateCard(card);let output=structuredClone(card),losses=[];
 if(version==='v2'){({card:output,losses}=downgrade(output));}
 else if(version!=='original')problem('CHARACTER_EXPORT_VERSION');
 if(format==='png'&&output.spec!=='chara_card_v2')problem('CHARACTER_V3_PNG_EXPORT_UNSUPPORTED');
 if(losses.length&&!acceptLoss)return {confirmationRequired:true,losses};
 if(!['json','png'].includes(format))problem('CHARACTER_EXPORT_FORMAT');
 return {confirmationRequired:false,losses,mediaType:format==='png'?'image/png':'application/json',bytes:format==='png'?exportPng(output,image):Buffer.from(canonical(output)+'\n','utf8')};
}
