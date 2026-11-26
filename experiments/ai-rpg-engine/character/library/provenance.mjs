import {readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,fail,sha256} from '../../authoring/contracts.mjs';

// Older imports already retain immutable receipts keyed by the complete receipt digest.
export async function provenance(work,imports,sources,archive,directory){
  if(work.draft.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
  const ch=work.draft.character;
  const pkg=['copy','import'].includes(work.origin?.kind)?await imports.view(work.id,null):null;
  const hash=pkg?.sourceHash??ch.source?.sha256,origins=[];
  if(hash){
    const storage=pkg?await archive():sources;
    const root=join(directory,pkg?'charx-imports':'sources','records');
    for(const entry of await readdir(root,{withFileTypes:true})){
      if(!entry.isFile()||!/^origin-[a-f0-9]{64}\.json$/.test(entry.name))continue;
      const id=entry.name.slice(0,-5),receipt=(await storage.records.get(id))?.data;
      if(receipt?.hash!==hash)continue;
      if(id!=='origin-'+sha256(canonical(receipt)))fail('CHARACTER_SOURCE_CORRUPT');
      origins.push({fileName:receipt.fileName,origin:receipt.origin});
    }
  }
  const data=ch.card.data;
  return {hash:hash??null,origins,cardSources:data.source??[],creatorNotes:data.creator_notes,
    parent:work.origin?.kind==='copy'?{id:work.origin.workId,revision:work.origin.revision}:null};
}
