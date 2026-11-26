import {zipSync} from 'fflate';
import {fail,sha256} from '../authoring/contracts.mjs';
import {charxLimits,safeArchivePath} from './charx-archive.mjs';
import {inspectCharx} from './charx.mjs';

const embedded='embeded://';
const extensions=new Map([['image/png','png'],['image/jpeg','jpeg'],['image/webp','webp']]);
function reject(message,path='') {
  fail('CHARACTER_CHARX_EXPORT_INVALID',path,{diagnostic:message});
}
function localPath(asset) {
  return asset.uri.startsWith(embedded)?asset.uri.slice(embedded.length):null;
}

function coverPath(files,digest,ext) {
  // Preserve the spelling of existing directories, including e.g. Assets/Icon.
  const names=new Map();
  for(const path of files.keys()){
    const parts=path.split('/');
    for(let i=1;i<=parts.length;i++){
      const prefix=parts.slice(0,i).join('/');
      names.set(prefix.normalize('NFC').toLowerCase(),prefix);
    }
  }
  const folder=names.get('assets/icon/images')??(names.get('assets/icon')??(names.get('assets')??'assets')+'/icon')+'/images';
  for(let n=0;;n++) {
    const path=folder+'/portrait-'+digest+(n?'-'+n:'')+'.'+ext;
    if(!names.has(path.normalize('NFC').toLowerCase())) return path;
  }
}

export async function exportCharxVersion({store,archive,binding},workId,versionId) {
  if(typeof versionId!=='string'||!/^version-[a-f0-9]{64}$/.test(versionId)) reject('请先选择一个已封存的角色版本。');
  const snapshot=await store.exportConfiguration(workId,versionId);
  const work=await store.read(workId);
  const revision=work.versions.find(v=>v.id===versionId)?.sourceRevision;
  if(!Number.isSafeInteger(revision)) reject('找不到此作品的版本。');
  const ref=await binding(work,snapshot.document,revision);
  return buildCharx({store,archive},workId,versionId,snapshot,ref);
}

export async function buildCharx({store,archive},workId,versionId,snapshot,ref,
  readLocal=async hash=>(await archive()).get(hash)) {
  const character=snapshot.document.character;
  if(snapshot.document.kind!=='character'||character?.card.spec!=='chara_card_v3'||character.card.spec_version!=='3.0') reject('只有 V3.0 角色版本可以导出 CHARX。');
  const card=structuredClone(character.card);
  const declarations=card.data.assets??[];
  const entries=ref && ref.definition===character.source?.sha256?[...ref.images,...(ref.media??[])]:[];
  const indexed=new Map(entries.map(entry=>[entry.path,entry]));
  const native=new Map(snapshot.assets.map(asset=>[asset.alias,asset]));
  const files=new Map();
  let total=0;
  function setFile(path,bytes) {
    total+=bytes.length-(files.get(path)?.length??0);
    if(bytes.length>charxLimits.file||total>charxLimits.total||(!files.has(path)&&files.size>=charxLimits.entries)) reject('导出内容超过 CHARX 大小或文件数上限。',path);
    files.set(path,bytes);
  }
  for(const asset of declarations) {
    const path=localPath(asset);
    if(path===null||files.has(path)) continue;
    const entry=indexed.get(path);
    if(!safeArchivePath(path)||!entry) reject('此版本缺少已核验的包内资源。',path);
    const descriptor=entry.alias?native.get(entry.alias):entry;
    if(!descriptor) reject('此版本缺少已封存的资源。',path);
    if(total+descriptor.size>charxLimits.total) reject('导出资源总量超限。',path);
    const bytes=entry.alias?(await store.getAsset(workId,entry.alias,versionId)).bytes:await readLocal(entry.sha256);
    if(bytes.length!==descriptor.size||sha256(bytes)!==descriptor.sha256) reject('资源内容与版本记录不一致。',path);
    setFile(path,bytes);
  }

  const cover=snapshot.document.settings.media.cover;
  if(cover) {
    const icons=declarations.filter(a=>a.type==='icon');
    const main=icons.length===1?icons[0]:icons.find(a=>a.name==='main');
    if(icons.length>1&&icons.filter(a=>a.name==='main').length!==1) reject('多个肖像需要一个明确的主肖像。');
    const oldPath=main?localPath(main):null;
    if(!oldPath||indexed.get(oldPath)?.alias!==cover) {
      const {asset,bytes}=await store.getAsset(workId,cover,versionId);
      const ext=extensions.get(asset.mediaType);
      if(!ext) reject('所选版本的肖像需要静态 PNG、JPEG 或 WebP。');
      const shared=oldPath && declarations.some(a=>a!==main&&localPath(a)===oldPath);
      const reuse=oldPath&&!shared&&main.ext.replace(/^jpg$/,'jpeg')===ext;
      if(oldPath&&!shared&&!reuse){total-=files.get(oldPath).length;files.delete(oldPath);}
      const path=reuse?oldPath:coverPath(files,asset.sha256,ext);
      const target=main??{type:'icon',name:'main'};
      if(!reuse) {target.uri=embedded+path;target.ext=ext;}
      if(!main) (card.data.assets??=[]).push(target);
      setFile(path,bytes);
    }
  }
  setFile('card.json',Buffer.from(JSON.stringify(card),'utf8'));
  const members=Object.create(null);
  for(const [path,data] of files) members[path]=data;
  // A fixed ZIP timestamp makes repeated exports of one snapshot byte-stable.
  const bytes=Buffer.from(zipSync(members,{level:6,mtime:new Date(2000,0,1)}));
  if(bytes.length>charxLimits.archive) reject('生成的压缩包超过 32 MiB。');
  const report=await inspectCharx(bytes);
  if(!['images','media'].includes(report.classification)) reject(report.diagnostics[0]||'生成的角色包未通过检查。');
  return bytes;
}
