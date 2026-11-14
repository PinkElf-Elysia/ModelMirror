// Independently implemented from pinned CCv2/CCv3 specifications; no external card code.
export const SPEC_SOURCES = Object.freeze({
 v2:'https://github.com/malfoyslastname/character-card-spec-v2/blob/8083fb388615ccbce768e97cbbd49d2b3214632c/spec_v2.md',
 v3:'https://github.com/kwaroran/character-card-spec-v3/blob/f3a86af019fbd99f788f7a1155f399655b34ab35/SPEC_V3.md'
});
export const CARD_LIMITS=Object.freeze({json:16*1024*1024,png:32*1024*1024,depth:64,entries:2000});
export function problem(code,path='',details={}){throw Object.assign(new Error(code),{code,path,details});}
const obj=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const string=(v,p)=>{if(typeof v!=='string'||!v.isWellFormed())problem('CHARACTER_FIELD_TYPE',p);};
const strings=(v,p)=>{if(!Array.isArray(v))problem('CHARACTER_FIELD_TYPE',p);v.forEach((x,i)=>string(x,p+'/'+i));};
const number=(v,p,signed=false)=>{if(typeof v!=='number'||!Number.isFinite(v)||(!signed&&v<0))problem('CHARACTER_FIELD_TYPE',p);};
export function blankCard(){return {spec:'chara_card_v2',spec_version:'2.0',data:{name:'',description:'',personality:'',scenario:'',first_mes:'',mes_example:'',creator_notes:'',system_prompt:'',post_history_instructions:'',alternate_greetings:[],tags:[],creator:'',character_version:'',extensions:{}}};}
export function validateCard(card){
 if(!obj(card)||!['chara_card_v2','chara_card_v3'].includes(card.spec))problem('CHARACTER_FORMAT_UNSUPPORTED','/spec');
 string(card.spec_version,'/spec_version');
 if(card.spec==='chara_card_v2'&&card.spec_version!=='2.0')problem('CHARACTER_VERSION_UNSUPPORTED','/spec_version');
 if(!obj(card.data))problem('CHARACTER_FIELD_TYPE','/data');
 const d=card.data;
 for(const k of ['name','description','personality','scenario','first_mes','mes_example','creator_notes','system_prompt','post_history_instructions','creator','character_version'])string(d[k],'/data/'+k);
 for(const k of ['alternate_greetings','tags'])strings(d[k],'/data/'+k);
 if(!obj(d.extensions))problem('CHARACTER_FIELD_TYPE','/data/extensions');
 if(d.nickname!==undefined)string(d.nickname,'/data/nickname');
 if(card.spec==='chara_card_v3'){
  strings(d.group_only_greetings,'/data/group_only_greetings');
  if(d.source!==undefined)strings(d.source,'/data/source');
  for(const k of ['creation_date','modification_date'])if(d[k]!==undefined)number(d[k],'/data/'+k);
  if(d.creator_notes_multilingual!==undefined){if(!obj(d.creator_notes_multilingual))problem('CHARACTER_FIELD_TYPE','/data/creator_notes_multilingual');for(const [k,v]of Object.entries(d.creator_notes_multilingual))string(v,'/data/creator_notes_multilingual/'+k);}
  if(d.assets!==undefined){if(!Array.isArray(d.assets))problem('CHARACTER_FIELD_TYPE','/data/assets');d.assets.forEach((a,i)=>{if(!obj(a))problem('CHARACTER_FIELD_TYPE','/data/assets/'+i);for(const k of ['type','uri','name','ext'])string(a[k],'/data/assets/'+i+'/'+k);});}
 }
 const b=d.character_book;
 if(b!==undefined){
  if(!obj(b)||!obj(b.extensions)||!Array.isArray(b.entries)||b.entries.length>CARD_LIMITS.entries)problem('CHARACTER_BOOK_INVALID','/data/character_book');
  for(const k of ['name','description'])if(b[k]!==undefined)string(b[k],'/data/character_book/'+k);
  for(const k of ['scan_depth','token_budget'])if(b[k]!==undefined)number(b[k],'/data/character_book/'+k);
  if(b.recursive_scanning!==undefined&&typeof b.recursive_scanning!=='boolean')problem('CHARACTER_BOOK_INVALID','/data/character_book/recursive_scanning');
  b.entries.forEach((e,i)=>{const p='/data/character_book/entries/'+i;
   if(!obj(e)||!obj(e.extensions))problem('CHARACTER_BOOK_INVALID',p);
   strings(e.keys,p+'/keys');string(e.content,p+'/content');if(typeof e.enabled!=='boolean')problem('CHARACTER_FIELD_TYPE',p+'/enabled');
   number(e.insertion_order,p+'/insertion_order',true);
   for(const k of ['case_sensitive','selective','constant','use_regex'])if(e[k]!==undefined&&typeof e[k]!=='boolean')problem('CHARACTER_FIELD_TYPE',p+'/'+k);
   if(e.secondary_keys!==undefined)strings(e.secondary_keys,p+'/secondary_keys');
   if(e.priority!==undefined)number(e.priority,p+'/priority',true);
   for(const k of ['name','comment'])if(e[k]!==undefined)string(e[k],p+'/'+k);
   if(e.id!==undefined&&!(typeof e.id==='number'&&Number.isFinite(e.id)||card.spec==='chara_card_v3'&&typeof e.id==='string'))problem('CHARACTER_FIELD_TYPE',p+'/id');
   if(e.position!==undefined&&!['before_char','after_char'].includes(e.position))problem('CHARACTER_FIELD_TYPE',p+'/position');
  });
 }
 return structuredClone(card);
}
export function compatibility(card){
 validateCard(card);const d=card.data,warnings=[];
 const add=(code,path,message)=>warnings.push({code,path,message});
 if(card.spec==='chara_card_v3')add('V3_PARTIAL','/spec','V3 核心字段映射；不代表完整 V3 行为兼容。');
 if(card.spec==='chara_card_v3'&&card.spec_version!=='3.0')add('FUTURE_VERSION','/spec_version','未来版本字段仅保留。');
 for(const k of ['assets','group_only_greetings','creator_notes_multilingual'])if(d[k]&&Object.keys(d[k]).length)add('PRESERVED_ONLY','/data/'+k,'数据保留，此字段不参与单人运行。');
 for(const [i,e]of (d.character_book?.entries||[]).entries())if(e.use_regex||/^\s*@@/m.test(e.content))add('BOOK_BEHAVIOR_UNSUPPORTED','/data/character_book/entries/'+i,'正则或装饰器行为未实现；该条目不参与运行。');
 const macroFields=Object.fromEntries(['description','personality','scenario','first_mes','mes_example','system_prompt','post_history_instructions'].map(k=>[k,d[k]]));
 d.alternate_greetings.forEach((value,i)=>macroFields['alternate_greetings/'+i]=value);
 (d.character_book?.entries||[]).forEach((e,i)=>{macroFields['character_book/entries/'+i+'/content']=e.content;});
 for(const [k,value] of Object.entries(macroFields)){
  const unsupported=[...value.matchAll(/\{\{([^{}]+)\}\}/g)].map(x=>x[1]).filter(x=>!['char','user',...(['system_prompt','post_history_instructions'].includes(k)?['original']:[])].includes(x.toLowerCase()));
  if(unsupported.length)add('MACRO_UNSUPPORTED','/data/'+k,'未支持宏按原文保留：'+[...new Set(unsupported)].join('、'));
 }
 if(Object.keys(d.extensions).length)add('EXTENSIONS_PRESERVED','/data/extensions','扩展完整保留；不执行脚本、插件或资源请求。');
 return {format:card.spec,version:card.spec_version,warnings,sources:SPEC_SOURCES};
}
export function downgrade(card){
 const source=validateCard(card);if(source.spec==='chara_card_v2')return {card:source,losses:[]};
 const result=structuredClone(source),losses=[];
 result.spec='chara_card_v2';result.spec_version='2.0';
 for(const k of ['assets','nickname','creator_notes_multilingual','source','group_only_greetings','creation_date','modification_date'])if(Object.hasOwn(result.data,k)){losses.push('/data/'+k);delete result.data[k];}
 for(const [i,e]of (result.data.character_book?.entries||[]).entries()){
  if(typeof e.id==='string'){losses.push('/data/character_book/entries/'+i+'/id');delete e.id;}
  if(e.use_regex||/^\s*@@/m.test(e.content)){e.enabled=false;losses.push('/data/character_book/entries/'+i+' (disabled unsupported behavior)');}
  e.content=e.content.split('\n').filter(line=>!/^\s*@@/.test(line)).join('\n');
  delete e.use_regex;
 }
 return {card:validateCard(result),losses};
}
