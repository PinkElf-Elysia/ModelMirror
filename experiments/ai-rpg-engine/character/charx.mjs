import {parseJson,sha256} from '../authoring/contracts.mjs';
import {CARD_LIMITS,validateCard} from './card.mjs';
import {fail as problem} from '../authoring/contracts.mjs';
import {readCharxArchive,safeArchivePath} from './charx-archive.mjs';
import {inspectAnimatedPortrait} from './apng.mjs';
import {inspectMedia} from './charx-media.mjs';
import {inspectImage} from './charx-images.mjs';

export const isZip = bytes => bytes[0]===0x50 && bytes[1]===0x4b;
const short = value => value.length>512 ? value.slice(0,511)+'…' : value;
const fileExtension = name => name.split('/').pop().split('.').pop().toLowerCase().replace(/^jpg$/,'jpeg');

export async function inspectCharx(bytes) {
  return (await openCharx(bytes)).report;
}

export async function openCharx(bytes) {
  const files=await readCharxArchive(bytes);
  const definition=files.get('card.json');
  if(!definition) problem('CHARACTER_CHARX_INVALID','card.json',{diagnostic:'缺少根目录 card.json'});
  let card;
  try {
    card=validateCard(parseJson(new TextDecoder('utf-8',{fatal:true}).decode(definition),CARD_LIMITS.json));
    if(card.spec!=='chara_card_v3' || card.spec_version!=='3.0') throw Error('version');
  } catch(e) {problem('CHARACTER_CHARX_INVALID',e.path||'card.json',{diagnostic:'需要有效的 V3.0 角色定义'});}
  const assets=card.data.assets??[{type:'icon',name:'main',uri:'ccdefault:',ext:'png'}];
  if(assets.length>2048) problem('CHARACTER_CHARX_INVALID','card.json',{diagnostic:'资源引用超过 2048 项'});
  const icons=assets.filter(a=>a.type==='icon');
  const ambiguous=icons.length>1 && icons.filter(a=>a.name==='main').length!==1;
  const main=icons.length===1?icons[0]:icons.find(a=>a.name==='main');
  const references=new Map(), diagnostics=[];
  if(ambiguous) diagnostics.push('多个肖像需要且只能有一个 main。');
  const members=[{path:'card.json',size:definition.length,status:'角色定义',uses:[]}];
  for(const a of assets) {
    const use={type:short(a.type),name:short(a.name),main:!ambiguous && a===main};
    if(!a.uri.startsWith('embeded://')) {
      const external=/^https?:\/\//i.test(a.uri), standard=a.uri==='ccdefault:';
      if(!external && !standard) diagnostics.push('未支持的资源引用：'+short(a.name));
      members.push({path:a.uri.startsWith('data:')?'data:（内联数据）':short(a.uri),size:null,
        status:external?'远程引用，仅保留':standard?'使用默认资源':'未支持的引用，仅保留',uses:[use]});
      continue;
    }
    const path=a.uri.slice('embeded://'.length);
    if(!safeArchivePath(path)) problem('CHARACTER_CHARX_INVALID',short(path),{diagnostic:'包内引用路径无效'});
    if(!references.has(path)) references.set(path,[]);
    references.get(path).push({...use,ext:a.ext});
  }
  let mediaPresent=false,animatedPortrait=null;
  for(const [path,data] of files) {
    if(path==='card.json') continue;
    const uses=references.get(path)??[], ext=fileExtension(path);
    const media=inspectMedia(data,ext);
    if(media) {
      mediaPresent=true;
      const issues=[];
      if(!media.verified) issues.push(media.diagnostic);
      if(!uses.length) issues.push('未引用的文件');
      if(media.format!==ext) issues.push('文件后缀与媒体容器不符');
      if(uses.some(use=>use.ext!==ext || use.ext!==media.format)) issues.push('声明格式与文件不符');
      if(uses.some(use=>use.main)) issues.push('主肖像需要静态图片');
      diagnostics.push(...issues.map(issue=>path+'：'+issue));
      const status=[media.label,media.verified?'已识别容器':'未核验',...issues,
        uses.length>1?'共 '+uses.length+' 处引用':''].filter(Boolean).join(' · ');
      members.push({path,size:data.length,status,uses,media});
      references.delete(path);
      continue;
    }
    let shape=null;
    try {shape=inspectImage(data,ext==='apng'?'png':ext);} catch {problem('CHARACTER_CHARX_INVALID',path,{diagnostic:'图片结构、校验或像素尺寸无效'});}
    let status='静态图片';
    if(shape?.animated && ['png','apng'].includes(ext) && uses.length===1 && uses[0].main && uses[0].ext===ext) {
      try {animatedPortrait=inspectAnimatedPortrait(data);status='APNG 主肖像';}
      catch(error){status='动画，仅保留原包';diagnostics.push(path+'：'+error.message);}
    } else if(!shape || ext==='apng' || shape.animated || !uses.length || uses.some(u=>u.ext.replace(/^jpg$/,'jpeg')!==ext)) {
      status=!shape?'附件，仅保留原包':shape.animated?'动画，仅保留原包':!uses.length?'未引用的文件，仅保留原包':'声明格式与文件不符';
      diagnostics.push(path+'：'+status);
    }
    members.push({path,size:data.length,status,uses,...shape});
    references.delete(path);
  }
  for(const [path,uses] of references) {
    const status=files.has(path)?'引用指向角色定义':'找不到同名包内文件';
    members.push({path,size:null,status,uses});diagnostics.push(path+'：'+status);
  }
  const report={source:{container:'charx',sha256:sha256(bytes),size:bytes.length},name:card.data.name,
    classification:diagnostics.length?'original-only':mediaPresent?'media':'images',members,diagnostics};
  return {report,files,card,animatedPortrait};
}
