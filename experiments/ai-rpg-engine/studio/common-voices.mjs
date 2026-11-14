import {readFile} from 'node:fs/promises';

// Public names are portable; cloud asset IDs stay in deployment-local data.
export const commonVoiceDefinitions=[{id:'elysia',name:'爱莉希雅音色'}];
export async function commonVoices(path,service){
 const unavailable=reason=>commonVoiceDefinitions.map(v=>({...v,available:false,reason}));
 let config;
 try{config=JSON.parse(await readFile(path,'utf8'));}catch(e){return unavailable(e.code==='ENOENT'?'尚未配置通用声线':'通用声线配置暂不可用');}
 if(!config||config.version!==1||!Array.isArray(config.voices)||config.voices.length>20)return unavailable('通用声线配置暂不可用');
 let catalog,assets;
 try{[catalog,{assets}]=await Promise.all([service.request('catalog'),service.request('assets')]);if(!Array.isArray(assets)||!Array.isArray(catalog.models))throw Error();}
 catch{return unavailable('统一控制面暂不可用');}
 return commonVoiceDefinitions.map(def=>{
  const matches=config.voices.filter(v=>v?.id===def.id);
  const binding=matches.length===1?matches[0]:null;
  if(!binding||!/^voice_[a-f0-9]{32}$/.test(binding.assetId)||typeof binding.model!=='string')return {...def,available:false,reason:'尚未配置通用声线'};
  const asset=assets.find(a=>a.id===binding.assetId&&a.model===binding.model);
  if(!asset||asset.state!=='ready')return {...def,available:false,reason:'音色未就绪或已移除'};
  const available=!!(catalog.available&&catalog.models.some(m=>m.id===asset.model&&m.available)&&asset.usable!==false);
  return {...def,model:asset.model,voice:'library:'+asset.id,available,reason:available?'所有会话可用':'模型或额度暂不可用'};
 });
}
