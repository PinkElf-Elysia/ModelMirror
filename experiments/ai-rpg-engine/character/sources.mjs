import {SessionRecords} from '../authoring/session-storage.mjs';
import {sha256,canonical,fail} from '../authoring/contracts.mjs';
export class CharacterSources{
 static async open(directory,options={}){const s=new CharacterSources();s.records=await SessionRecords.open(directory,{...options,scope:'character-original-sources'});await s.records.reconcile();return s;}
 async #immutable(id,data){const old=await this.records.get(id);if(old){if(canonical(old.data)!==canonical(data))fail('CHARACTER_SOURCE_CONFLICT');return;}await this.records.put(id,null,data);}
 async put(bytes,{fileName='',origin='',report=null,selection=null}={}){
  const b=Buffer.from(bytes),hash=sha256(b);if(b.length>32*1024*1024||typeof fileName!=='string'||fileName.length>240||typeof origin!=='string'||origin.length>4096)fail('CHARACTER_SOURCE_INVALID');
  const count=Math.ceil(b.length/1048576);
  for(let i=0;i<count;i++)await this.#immutable('raw-'+hash+'-'+i,{base64:b.subarray(i*1048576,(i+1)*1048576).toString('base64')});
  // Provenance is an import receipt, not part of the exchanged character payload.
  await this.#immutable('source-'+hash,{hash,size:b.length,count});
  const receipt='origin-'+sha256(canonical({hash,fileName,origin,report,selection}));await this.#immutable(receipt,{hash,fileName,origin,report,selection});
  return hash;
 }
 async get(hash){if(!/^[a-f0-9]{64}$/.test(hash))fail('CHARACTER_SOURCE_INVALID');const r=await this.records.get('source-'+hash);if(!r)fail('CHARACTER_SOURCE_NOT_FOUND');
  const chunks=[];for(let i=0;i<r.data.count;i++){const part=await this.records.get('raw-'+hash+'-'+i);if(!part)fail('CHARACTER_SOURCE_CORRUPT');chunks.push(Buffer.from(part.data.base64,'base64'));}
  const b=Buffer.concat(chunks);if(b.length!==r.data.size||sha256(b)!==hash)fail('CHARACTER_SOURCE_CORRUPT');return b;
 }
 async close(){await this.records.close();}
}
