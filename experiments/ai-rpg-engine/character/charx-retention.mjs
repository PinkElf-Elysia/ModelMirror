import {fail,keys} from '../authoring/contracts.mjs';
import {decode64} from './png.mjs';
import {inspectCharx} from './charx.mjs';

const shelfId='retained-charx-list';
export function retainedCharx(sources) {
  const shelf=()=>sources.records.get(shelfId);
  let queue=Promise.resolve();
  async function keep(input) {
    keys(input,['base64','expectedHash','fileName']);
    if(typeof input.fileName!=='string'||input.fileName.length>240) fail('CHARACTER_SOURCE_INVALID');
    const bytes=decode64(input.base64,32*1024*1024);
    const info=await inspectCharx(bytes);
    const hash=info.source.sha256;
    if(hash!==input.expectedHash) fail('CHARACTER_IMPORT_CHANGED');
    const record=await shelf(), rows=record?.data??[];
    if(!rows.some(row=>row.hash===hash)) {
      if(rows.length>=256) fail('CHARACTER_CHARX_SHELF_FULL');
      await sources.put(bytes,{fileName:input.fileName});
      await sources.records.put(shelfId,record?.hash??null,[...rows,{hash,name:info.name.slice(0,160),size:bytes.length}]);
    }
    return {hash};
  }
  return {
    list:async ()=>(await shelf())?.data??[],
    keep(input) {
      const action=queue.then(()=>keep(input));
      queue=action.catch(()=>{});
      return action;
    },
    async original(hash) {
      if(!/^[a-f0-9]{64}$/.test(hash) || !(await shelf())?.data.some(row=>row.hash===hash)) fail('CHARACTER_SOURCE_NOT_FOUND');
      return sources.get(hash);
    }
  };
}
