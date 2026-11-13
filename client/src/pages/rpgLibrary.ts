import {worlds} from '../../../experiments/ai-rpg-engine/card-replica/src/presentation';
import {localAuthoringHref} from './rpgAuthoringLink';
export type LibraryWork = {id:string; name:string; description:string; tags:string[]; art:string; mark:string; href:string; source:'builtin'|'published'; version?:number; publishedAt?:string};
export const builtinWorks:LibraryWork[] = worlds.map(w=>({id:w.id,name:w.name,description:w.description,tags:[w.genre],art:w.art,mark:w.mark,href:'/rpg/'+w.id,source:'builtin'}));
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
