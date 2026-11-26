import {blankDocument,canonical,configuration,fail,FORMAT,isId,LIMITS,sha256} from '../authoring/contracts.mjs';
import {characterDocument} from './document.mjs';
import {exportCharxVersion} from './charx-export.mjs';
import {openCharx} from './charx.mjs';

const operationFor = hash => 'charx-'+hash;
const identityFor = hash => 'work-'+sha256('authoring-create:'+operationFor(hash));
const aliasFor = path => 'cx-'+sha256(path);
const embedded = 'embeded://';

async function remember(records,key,data) {
  const old=await records.get(key);
  if(!old) await records.put(key,null,data);
  else if(canonical(old.data)!==canonical(data)) fail('CHARACTER_CHARX_CONFLICT');
}

export function charxImports({store,sources,archive}) {
  let queue=Promise.resolve();
  async function confirm(bytes,input) {
    if(!isId(input.operationId)) fail('AUTHORING_OPERATION_ID_INVALID');
    if(typeof input.fileName!=='string'||input.fileName.length>240||typeof input.origin!=='string'||input.origin.length>4096) fail('CHARACTER_SOURCE_INVALID');
    const hash=sha256(bytes);
    if(input.expectedHash!==hash) fail('CHARACTER_IMPORT_CHANGED');
    const {card,files,report}=await openCharx(bytes);
    if(report.classification!=='images') fail('CHARACTER_CHARX_ORIGINAL_ONLY','',{diagnostic:'此包只能完整保留，请核对资源清单。'});
    const definition=files.get('card.json');
    const source={container:'json',size:definition.length,sha256:sha256(definition),selection:null};
    const document=characterDocument(blankDocument(),card,source);
    const assets=[], images=[], blobs=new Map();
    for(const row of report.members) {
      if(row.status!=='静态图片') continue;
      const data=files.get(row.path), digest=sha256(data);
      const ext=row.uses[0].ext.replace(/^jpg$/,'jpeg');
      const image={path:row.path,mediaType:'image/'+ext,size:data.length,sha256:digest,width:row.width,height:row.height,alias:null};
      if(data.length<=LIMITS.imageBytes) {
        image.alias=aliasFor(row.path);
        assets.push({alias:image.alias,fileName:image.alias+'.'+ext,mediaType:image.mediaType,size:image.size,sha256:digest});
        blobs.set(digest,data);
        if(row.uses.some(u=>u.main)) document.settings.media.cover=image.alias;
      }
      images.push(image);
    }
    const payload={format:FORMAT.package,configuration:configuration(document,assets),
      blobs:[...blobs].map(([digest,data])=>({sha256:digest,base64:data.toString('base64')}))};
    const checked=await store.inspectImport(payload);
    if(!checked.valid) fail('CHARACTER_CHARX_WORK_INVALID','',{issues:checked.issues});
    const operationId=operationFor(hash),workId=identityFor(hash);
    const originArchive=await archive();
    await remember(originArchive.records,'confirm-'+sha256(input.operationId),{hash,fileName:input.fileName,origin:input.origin});
    await originArchive.put(bytes,{fileName:input.fileName,origin:input.origin});
    await sources.put(definition,{fileName:'card.json'});
    for(const image of images) if(!image.alias) await originArchive.put(files.get(image.path));
    await remember(originArchive.records,workId,{hash,definition:source.sha256,payloadHash:checked.payloadHash,images});
    // Only an explicit confirmation may reconcile this exact creation after interruption.
    await store.recover(workId,operationId);
    const result=await store.importPackage({operationId,expectedRevision:null,workId:null,payload,expectedImportHash:checked.payloadHash});
    return {workId:result.workId,report,portraitStored:report.members.some(r=>r.status==='静态图片'&&r.uses.some(u=>u.main))};
  }

  async function binding(work) {
    // Copies inherit their verified local parent, never a browser-provided source hash.
    const visited=new Set();
    while(work.origin?.kind==='copy') {
      if(visited.has(work.id)) fail('CHARACTER_CHARX_CONFLICT');
      visited.add(work.id);work=await store.read(work.origin.workId);
    }
    if(work.origin?.kind!=='import') return null;
    const meta=(await (await archive()).records.get(work.id))?.data;
    if(!meta) return null;
    if(identityFor(meta.hash)!==work.id || meta.payloadHash!==work.origin.payloadHash) fail('CHARACTER_CHARX_CONFLICT');
    return meta;
  }
  async function view(workId,versionId) {
    const work=await store.read(workId);
    const ref=await binding(work);
    if(!ref) return null;
    const snapshot=versionId===null?{document:work.draft,assets:work.assets}:await store.exportConfiguration(workId,versionId);
    const character=snapshot.document.character;
    if(character?.source?.sha256!==ref.definition) return null;
    const entries=new Map(ref.images.map(image=>[image.path,image]));
    const native=new Map(snapshot.assets.map(a=>[a.alias,a]));
    const declarations=character.card.data.assets??[];
    const icons=declarations.filter(a=>a.type==='icon');
    const main=icons.length===1?icons[0]:icons.find(a=>a.name==='main');
    const rows=declarations.map(asset=>{
      const local=asset.uri.startsWith(embedded);
      const image=local?entries.get(asset.uri.slice(embedded.length)):null;
      const base={type:asset.type,name:asset.name,main:asset===main,path:local?asset.uri.slice(embedded.length):asset.uri};
      if(!image) return {...base,status:local?'缺少包内图片':asset.uri==='ccdefault:'?'默认资源':'仅保留引用'};
      const attached=image.alias?native.get(image.alias):image;
      if(!attached || !attached.mediaType.startsWith('image/')) return {...base,status:'缺少包内图片'};
      return {...base,status:'图片',mediaType:attached.mediaType,size:attached.size,sha256:attached.sha256,alias:image.alias};
    });
    return {sourceHash:ref.hash,rows};
  }
  return {
    confirm(bytes,input) {
      const result=queue.then(()=>confirm(bytes,input));queue=result.catch(()=>{});return result;
    },
    view,
    exportVersion:(workId,versionId)=>exportCharxVersion({store,archive,binding},workId,versionId),
    async original(work,version) {
      const info=await view(work,version);
      return info?(await archive()).get(info.sourceHash):null;
    },
    async image(work,version,path,expectedHash) {
      const info=await view(work,version),row=info?.rows.find(r=>r.path===path && r.status==='图片');
      if(!row) fail('CHARACTER_SOURCE_NOT_FOUND');
      if(expectedHash!=null && expectedHash!==row.sha256) fail('AUTHORING_ASSET_REVISION_CONFLICT');
      if(row.alias) {
        const {asset,bytes}=await store.getAsset(work,row.alias,version);
        if(asset.sha256!==row.sha256) fail('AUTHORING_REVISION_CONFLICT');
        return {bytes,mediaType:asset.mediaType};
      }
      return {bytes:await (await archive()).get(row.sha256),mediaType:row.mediaType};
    }
  };
}
