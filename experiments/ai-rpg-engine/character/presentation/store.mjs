import {canonical, fail, keys, sha256} from '../../authoring/contracts.mjs';
import {inspectImage} from '../charx-images.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';

const digest = data => sha256(canonical(data));
const imageTypes = new Set(['image/png','image/jpeg','image/webp']);
const playableTypes=new Set(['audio/wav','audio/mpeg','video/mp4']);
const empty = () => ({serial:0,expressions:{}, background:null});
const reject = () => fail('CHARACTER_APPEARANCE_INVALID');

export async function openPresentation({directory, works, imports, resolveMembers, recoverDeadOwner=false}) {
  const records = await SessionRecords.open(directory, {scope:'character-presentation-v1', recoverDeadOwner});
  await records.reconcile();
  async function catalogue(scope,resolveBranch) {
    const parts = scope.split('/');
    const [kind, id, version] = parts;
    let members;
    if (kind === 'preview' && parts.length === 3 && /^work-[a-f0-9]{64}$/.test(id) && /^version-[a-f0-9]{64}$/.test(version)) {
      members = [{id:'character', reference:{workId:id,versionId:version}, period:1}];
    } else if (resolveBranch && kind==='branch' && parts.length===3 && /^family-[a-f0-9]{64}$/.test(id) && /^branch-[a-f0-9]{64}$/.test(version)) {
      members=await resolveBranch(id,version);
    } else if (parts.length === 2 && ((kind === 'single' && /^session-[a-f0-9]{64}$/.test(id)) || (kind === 'group' && /^group-[a-f0-9]{64}$/.test(id)))) {
      members = await resolveMembers(kind,id);
    } else reject();
    const assets = new Map();
    const people = [];
    for (const member of members) {
      const {workId, versionId} = member.reference;
      const config = await works.verifyConfiguration(workId,versionId);
      if (config.document.kind !== 'character') reject();
      const pack = await imports.view(workId,versionId);
      const person = {id:member.id,name:config.document.character.card.data.name,portrait:null,expressions:[],backgrounds:[]};
      if(kind!=='branch') person.media=[];
      const register = (role,index,row) => {
        // A background belongs to this attendance interval, even if nobody opened the panel during an absence.
        const key = digest([scope,member.id,workId,versionId,role,index,row.sha256,(role==='background'||role==='media'||role==='animation')?member.period:null]);
        assets.set(key,{role,memberId:member.id,workId,versionId,row});
        return {key,name:row.name};
      };
      const cover = config.document.settings.media.cover;
      const native = config.assets.find(a=>a.alias===cover);
      if (native && imageTypes.has(native.mediaType)) person.portrait=register('portrait',-1,{...native,name:person.name});
      for (const [index,row] of (pack?.rows??[]).entries()) {
        if(kind!=='branch' && row.animation && person.portrait){
          person.animation={...register('animation',index,row),frames:row.animation.frames,plays:row.animation.plays};
        }
        if (person.media && playableTypes.has(row.mediaType) && ['音频','视频'].includes(row.status)) {
          person.media.push({...register('media',index,row),type:row.mediaType});
        }
        if (row.status!=='图片' || !imageTypes.has(row.mediaType)) continue;
        if (row.main && !person.portrait) person.portrait=register('portrait',index,row);
        if (row.type==='emotion') person.expressions.push(register('expression',index,row));
        if (row.type==='background') person.backgrounds.push(register('background',index,row));
      }
      people.push(person);
    }
    return {people,assets,roster:members.map(m=>[m.id,m.reference,m.period])};
  }
  async function access(scope,input=null,imageKey=null,resolveBranch=null) {
    const {people,assets,roster}=await catalogue(scope,resolveBranch);
    if (imageKey!==null) {
      const item=assets.get(imageKey);
      if (!item || ['media','animation'].includes(item.role)) reject();
      const {workId,versionId,row}=item;
      if (!row.alias) return imports.image(workId,versionId,row.path,row.sha256);
      const {asset,bytes}=await works.getAsset(workId,row.alias,versionId);
      if (asset.sha256!==row.sha256 || !imageTypes.has(asset.mediaType)) reject();
      const shape=inspectImage(bytes,asset.mediaType.slice(6));
      if (!shape || shape.animated) reject();
      return {bytes,mediaType:asset.mediaType};
    }
    const preview=scope.startsWith('preview/');
    const recordId='look-'+digest(scope);
    const prior=preview?null:await records.get(recordId);
    const state=prior?.data??empty();
    const token=hash=>digest([scope,roster,hash]);
    const revision=token(prior?.hash??null);
    if (input!==null) {
      if (preview) reject();
      keys(input,['revision','role','memberId','key']);
      if (input.revision!==revision) fail('CHARACTER_APPEARANCE_CONFLICT');
      if (!people.some(p=>p.id===input.memberId) || !['expression','background'].includes(input.role)) reject();
      const choice=assets.get(input.key);
      if (input.key!==null && (!choice || choice.role!==input.role || choice.memberId!==input.memberId)) reject();
      if (input.role==='background') state.background=input.key;
      else if(input.key===null) delete state.expressions[input.memberId];
      else state.expressions[input.memberId]=input.key;
      state.serial++;
      await records.put(recordId,prior?.hash??null,state);
    }
    return {people,revision:input?token(digest(state)):revision,
      expressions:Object.fromEntries(people.filter(p=>assets.has(state.expressions[p.id])).map(p=>[p.id,state.expressions[p.id]])),
      background:assets.has(state.background)?state.background:null};
  }
  async function media(scope,key) {
    const {assets}=await catalogue(scope);
    const item=assets.get(key);
    if (!item || item.role!=='media') reject();
    return imports.media(item.workId,item.versionId,item.row.path,item.row.sha256);
  }
  async function animation(scope,key) {
    const {assets}=await catalogue(scope);
    const item=assets.get(key);
    if(item?.role!=='animation')reject();
    return imports.animation(item.workId,item.versionId,item.row.path,item.row.animation.playbackHash);
  }
  return {access,media,animation,close:()=>records.close()};
}

export async function presentationRoute(service,route,method,body,send,kinds,resolveBranch=null,transport=null) {
  const anim=route.match(/^(.+)\/animation\/([a-f0-9]{64})$/);
  if(anim){
    if(method!=='GET' || !kinds.includes(anim[1].split('/')[0]))reject();
    const file=await service.animation(anim[1],anim[2]);return send(file.bytes,200,file.mediaType);
  }
  const media=route.match(/^(.+)\/media\/([a-f0-9]{64})$/);
  if (media) {
    if (method!=='GET' || !transport || !kinds.includes(media[1].split('/')[0])) reject();
    const file=await service.media(media[1],media[2]);
    return sendMedia(file,transport.req,transport.res,send);
  }
  const [scope,imageKey]=route.split('/image/');
  if (!kinds.includes(scope.split('/')[0]) || route.split('/image/').length>2) reject();
  if (imageKey!==undefined) {
    if (method!=='GET' || !/^[a-f0-9]{64}$/.test(imageKey)) reject();
    const file=await service.access(scope,null,imageKey,resolveBranch);
    return send(file.bytes,200,file.mediaType);
  }
  if (method==='GET') return send(await service.access(scope,null,null,resolveBranch));
  if (method!=='POST') reject();
  const input=await body();
  if (!input || typeof input!=='object') reject();
  return send(await service.access(scope,input,null,resolveBranch));
}

// Native players may request a single byte range, including the suffix of an MP4.
function sendMedia({bytes,mediaType},req,res,send) {
  const size=bytes.length,range=req.headers.range;
  res.setHeader('Accept-Ranges','bytes');
  res.setHeader('Cache-Control','private, no-store');
  if(range===undefined){res.setHeader('Content-Length',size);return send(bytes,200,mediaType);}
  const parts=/^bytes=(\d*)-(\d*)$/.exec(range);
  let start=0,end=size-1;
  if(parts && parts[1]) {
    start=Number(parts[1]);
    if(parts[2])end=Math.min(end,Number(parts[2]));
  } else if(parts && parts[2]) start=Math.max(0,size-Number(parts[2]));
  if(!parts || (!parts[1]&&!parts[2]) || !Number.isSafeInteger(start) || start>end || start>=size) {
    res.setHeader('Content-Range','bytes */'+size);
    return send(Buffer.alloc(0),416,mediaType);
  }
  res.setHeader('Content-Range','bytes '+start+'-'+end+'/'+size);
  res.setHeader('Content-Length',end-start+1);
  return send(bytes.subarray(start,end+1),206,mediaType);
}

export async function groupLookMembers(store,id) {
  const state=await store.read(id);
  return presentMembers(state.members);
}
export function presentMembers(members) {
  return members.filter(m=>m.kind==='character' && m.intervals.at(-1).until===null)
    .map(m=>({id:m.id,reference:m.binding.reference,period:m.intervals.length}));
}
