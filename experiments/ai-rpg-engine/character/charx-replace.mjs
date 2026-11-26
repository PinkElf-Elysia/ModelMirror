import {randomUUID} from 'node:crypto';
import {canonical,fail,isId,keys,sha256} from '../authoring/contracts.mjs';
import {charxLimits,safeArchivePath} from './charx-archive.mjs';
import {buildCharx} from './charx-export.mjs';
import {inspectMedia} from './charx-media.mjs';
import {decode64} from './png.mjs';

const embedded='embeded://';
const generated=/(?:^|\/)media-[0-9a-f-]{36}\.(wav|mp3|mp4)$/;
const mime={wav:'audio/wav',mp3:'audio/mpeg',mp4:'video/mp4'};
const keyFor=(owner,path)=>'media-path-'+sha256(canonical([owner,path]));
const reject=reason=>fail('CHARACTER_CHARX_REPLACEMENT_INVALID','',{diagnostic:reason});

export async function withReplacements(ref,card,lineage,records) {
  const paths=new Set((card.data.assets??[]).filter(a=>a.uri.startsWith(embedded)).map(a=>a.uri.slice(embedded.length)).filter(p=>generated.test(p)));
  if(!paths.size) return ref;
  const media=[...(ref.media??[])];
  const imported=new Set([...ref.images,...media].map(r=>r.path));
  for(const path of paths) {
    if(imported.has(path)) continue;
    for(const {id,revision} of lineage) {
      const record=(await records.get(keyFor(id,path)))?.data;
      if(record && record.revision<=revision && record.sourceHash===ref.hash) {
        media.push(record.entry);
        break;
      }
    }
  }
  return {...ref,media};
}

export async function replaceMedia({store,archive,binding},input) {
  keys(input,['workId','expectedRevision','operationId','index','extension','base64']);
  const {workId,expectedRevision,operationId,index,extension}=input;
  if(!isId(operationId)||!Number.isSafeInteger(expectedRevision)||expectedRevision<0||
     !Number.isSafeInteger(index)||index<0||typeof extension!=='string'||!Object.hasOwn(mime,extension)) reject('请选择草稿中已有的音视频引用和支持的文件。');
  const bytes=decode64(input.base64,charxLimits.file),hash=sha256(bytes);
  const inspected=inspectMedia(bytes,extension);
  if(!inspected?.verified || inspected.format!==extension) reject(inspected?.diagnostic??'文件格式与后缀不一致。');

  const local=await archive();
  const intentKey='media-intent-'+sha256(canonical([workId,operationId]));
  const intentHash=sha256(canonical({workId,expectedRevision,index,extension,hash}));
  const intent=(await local.records.get(intentKey))?.data;
  if(intent && intent.intentHash!==intentHash) fail('AUTHORING_OPERATION_COLLISION');
  // Recovery is restricted to an explicit retry of this operation. Preview and reads never reconcile.
  const receipt=intent?(await store.recover(workId,operationId)).receipt:null;
  if(receipt) {
    if(!intent || intent.saveHash!==receipt.requestHash) fail('AUTHORING_OPERATION_COLLISION');
    return {...receipt,replayed:true};
  }
  const work=await store.read(workId);
  if(work.pending) fail('AUTHORING_OPERATION_PENDING','',{workId:work.pending.workId,operationId:work.pending.operationId});
  if(work.revision!==expectedRevision) fail('AUTHORING_REVISION_CONFLICT');
  const draft=work.draft, card=draft.character?.card, asset=card?.data.assets?.[index];
  const ref=asset && await binding(work);
  const previous=ref?.media?.find(row=>asset.uri===embedded+row.path);
  if(!previous || !previous.mediaType.startsWith(inspected.kind+'/')) reject('只能将已绑定的音频换为音频、视频换为视频。');

  // The new URI is the versioned reference. Even single-use replacements cannot change older snapshots.
  const name='media-'+randomUUID()+'.'+extension;
  const sibling=previous.path.slice(0,previous.path.lastIndexOf('/')+1)+name;
  const path=intent?.path??(safeArchivePath(sibling)?sibling:name);
  if([...ref.images,...ref.media].some(r=>r.path===path)) reject('新资源路径与现有文件冲突。');
  const entry={path,mediaType:mime[extension],size:bytes.length,sha256:hash};
  asset.uri=embedded+path;asset.ext=extension;
  const save={operationId,expectedRevision,document:draft};
  const saveHash=sha256(canonical({kind:'save',workId,input:save}));
  if(intent && intent.saveHash!==saveHash) fail('AUTHORING_OPERATION_COLLISION');

  // Run the same package/size checks as a sealed export before making the change visible.
  await buildCharx({store,archive},workId,null,{document:draft,assets:work.assets},
    {...ref,media:[...ref.media,entry]},digest=>digest===hash?bytes:local.get(digest));

  if(!intent) await local.records.put(intentKey,null,{intentHash,saveHash,path});
  await local.put(bytes);
  const key=keyFor(workId,path), value={entry,sourceHash:ref.hash,revision:expectedRevision+1};
  const old=(await local.records.get(key))?.data;
  if(!old) await local.records.put(key,null,value);
  else if(canonical(old)!==canonical(value)) fail('CHARACTER_CHARX_CONFLICT');
  return store.save(workId,save);
}
