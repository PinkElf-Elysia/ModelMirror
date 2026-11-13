import {worlds} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
import {localAuthoringHref} from './rpgAuthoringLink';
export type LibraryWork = {id:string; name:string; description:string; tags:string[]; art:string; artPosition?:string; mark:string; href:string; source:'builtin'|'published'; version?:number; publishedAt?:string};
// Library-only covers do not alter saved-session runtime fingerprints.
const libraryCovers:Record<string,string>={
  douluo:'/rpg-covers/douluo-original.gif',
  god:'/rpg-covers/god-original.gif',
  cultivation:'/rpg-covers/cultivation-original.png',
  earth:'/rpg-covers/earth-generated.png',
  rpg05:'/rpg-covers/rpg05-generated.png',
};
// Display copy stays outside the immutable card runtime used by existing saves.
const libraryCopy:Record<typeof worlds[number]['id'],{name?:string;description:string}>={
  elderland:{description:'踏入艾尔德兰，在王国、遗迹与荒野间冒险，用剑与魔法走出自己的道路。'},
  medieval:{description:'回到公元四至十五世纪，从出身与技艺起步，在生计、信仰和战乱间经营一生。'},
  jinyong:{description:'走进金庸笔下的江湖，拜师学艺、结交群侠，在恩怨与抉择中写下自己的武林传奇。'},
  'ai-girls':{description:'与八位性格各异的 AI 娘同住一屋，在拌嘴、陪伴与意外中，开启热闹的都市日常。'},
  douluo:{description:'觉醒专属武魂，猎取魂环、磨炼魂技，与伙伴并肩踏上斗罗大陆的魂师之路。'},
  'dragon-raja':{description:'加入卡塞尔学院，走进混血种与龙族的隐秘世界，在任务、羁绊与命运之间作出选择。'},
  'blue-fish':{description:'和蓝色大肥鱼一起打磨角色、世界观与提示词，把零散灵感整理成可继续创作的设定。'},
  'lord-of-mysteries':{description:'走进蒸汽与神秘交织的时代，调查隐秘、结识非凡者，在迷雾中探索自己的非凡途径。'},
  ancient:{name:'古代人生模拟器',description:'选择时代与出身，从市井烟火到庙堂风云，在家业、人情与世事之间经营你的古代人生。'},
  earth:{name:'地球online',description:'自定出身与天赋，重启一段现代人生，在求学、事业与人际关系中尝试不同的生活。'},
  god:{name:'上帝模拟器',description:'以神明之身降临世界，创造文明、改写规则，观察众生如何回应你的每一次干预。'},
  cultivation:{name:'修仙大世界',description:'设定灵根与出身，拜入宗门、寻访机缘，在凡界、仙界与神界间走出自己的修行路。'},
  rpg05:{name:'无限世界',description:'穿行不同世界，以新的身份展开冒险，在一次次选择与相遇中，书写属于你的无限旅程。'},
};
export const builtinWorks:LibraryWork[] = worlds.map(w=>({id:w.id,name:libraryCopy[w.id].name||w.name,description:libraryCopy[w.id].description,tags:[w.genre],art:libraryCovers[w.id]||w.art,artPosition:libraryCovers[w.id]?'50% 50%':undefined,mark:w.mark,href:'/rpg/'+w.id,source:'builtin'}));
export function authoringOrigin(port:string|undefined,page:Pick<Location,'protocol'|'hostname'|'port'>) {
  return localAuthoringHref(port,page) ? `http://127.0.0.1:${port}` : undefined;
}
export function workbenchHref(port:string|undefined,page:Pick<Location,'protocol'|'hostname'|'port'>, work?:{id:string;versionId:string}) {
  const base=localAuthoringHref(port,page); if(!base)return;
  const url=new URL(base);
  url.searchParams.set('library',`http://127.0.0.1${page.port?':'+page.port:''}/rpg`);
  if(work){url.searchParams.set('work',work.id);url.searchParams.set('version',work.versionId);}
  return url.href;
}
export function readPublishedWorks(value:unknown,port:string|undefined,page:Pick<Location,'protocol'|'hostname'|'port'>):LibraryWork[] {
  const origin=authoringOrigin(port,page);
  if(!origin||!value||typeof value!=='object'||!('format' in value)||value.format!=='modelmirror.rpg.library/1'||!('works' in value)||!Array.isArray(value.works))throw Error('Invalid library');
  const ids=new Set<string>();
  return value.works.map(w=>{
    if(!w||typeof w!=='object'||typeof w.id!=='string'||!/^work-[a-f0-9]{64}$/.test(w.id)||ids.has(w.id)||typeof w.versionId!=='string'||!/^version-[a-f0-9]{64}$/.test(w.versionId)||typeof w.name!=='string'||typeof w.description!=='string'||!Array.isArray(w.tags)||!w.tags.every((t:unknown)=>typeof t==='string')||!Number.isSafeInteger(w.versionNumber)||w.versionNumber<1||typeof w.publishedAt!=='string'||!Number.isFinite(Date.parse(w.publishedAt)))throw Error('Invalid work');
    ids.add(w.id);
    const cover=`/rpg-app/api/authoring/library/${w.id}/${w.versionId}/cover`;
    if(w.cover!==null&&w.cover!==cover)throw Error('Invalid cover');
    return {id:w.id,name:w.name,description:w.description,tags:w.tags,art:w.cover?origin+cover:'',mark:Array.from(String(w.name).trim())[0]||'作',href:workbenchHref(port,page,w)!,source:'published',version:w.versionNumber,publishedAt:w.publishedAt};
  });
}
