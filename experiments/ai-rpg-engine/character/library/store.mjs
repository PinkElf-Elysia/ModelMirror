import {canonical,fail,isId,keys,sha256} from '../../authoring/contracts.mjs';

const empty = () => ({favorite:false,tags:[]});
const initial = () => ({revision:0,items:{},undo:null,last:null});
const publicView = state => ({revision:state.revision,items:state.items,canUndo:!!state.undo});
const key = 'character-library-organization-v1';

// The local author's preferences have their own CAS record, outside work and session snapshots.
export function libraryStore(records,works) {
  const read=()=>records.get(key);
  async function edit(input) {
    keys(input,['operationId','expectedRevision','action','ids','value']);
    const {operationId,expectedRevision,action,ids,value}=input;
    if(!isId(operationId) || !Number.isSafeInteger(expectedRevision) || expectedRevision<0 ||
       !Array.isArray(ids) || ids.length>100 || new Set(ids).size!==ids.length ||
       ids.some(id=>typeof id!=='string'||!/^work-[a-f0-9]{64}$/.test(id)))fail('CHARACTER_LIBRARY_INPUT');
    const saved=await read(),state=saved?.data??initial();
    const intent=sha256(canonical(input));
    if(state.last?.operationId===operationId){
      if(state.last.intent!==intent)fail('CHARACTER_LIBRARY_COLLISION');
      return publicView(state);
    }
    if(state.revision!==expectedRevision)fail('CHARACTER_LIBRARY_CONFLICT');
    if(action==='undo'){
      if(ids.length||value!==null||!state.undo)fail('CHARACTER_LIBRARY_INPUT');
      for(const [id,old] of state.undo){if(old)state.items[id]=old;else delete state.items[id];}
      state.undo=null;
    }else{
      if(!ids.length||!['favorite','add-tag','remove-tag'].includes(action))fail('CHARACTER_LIBRARY_INPUT');
      if(action==='favorite'?typeof value!=='boolean':
        typeof value!=='string'||!value.trim()||value.trim()!==value||value.length>48||!value.isWellFormed())fail('CHARACTER_LIBRARY_INPUT');
      const changes=[];
      for(const id of ids){
        const work=await works.read(id);if(work.draft.kind!=='character')fail('CHARACTER_TYPE_REQUIRED');
        const old=state.items[id]??null,item=structuredClone(old??empty());
        if(action==='favorite')item.favorite=value;
        if(action==='add-tag'&&!item.tags.includes(value))item.tags.push(value);
        if(action==='remove-tag')item.tags=item.tags.filter(t=>t!==value);
        if(item.tags.length>64)fail('CHARACTER_LIBRARY_TAG_LIMIT');
        if(canonical(item)===canonical(old??empty()))continue;
        changes.push([id,old]);if(item.favorite||item.tags.length)state.items[id]=item;else delete state.items[id];
      }
      if(changes.length)state.undo=changes;
    }
    state.revision++;state.last={operationId,intent};
    try{await records.put(key,saved?.hash??null,state);}catch(e){if(e.code==='OFFLINE_STORAGE_CONFLICT')fail('CHARACTER_LIBRARY_CONFLICT');throw e;}
    return publicView(state);
  }
  return {view:async()=>publicView((await read())?.data??initial()),edit};
}

export async function duplicateWorks(store,imports,source,card){
  const digest=sha256(canonical(card)),matches=[];
  for(const row of await store.list()){
    const w=await store.read(row.id);if(w.draft.kind!=='character')continue;
    const ch=w.draft.character,packageHash=['copy','import'].includes(w.origin?.kind)?(await imports.view(row.id,null))?.sourceHash:null;
    const sameFile=(packageHash??ch.source?.sha256)===source.sha256;
    if(sameFile || sha256(canonical(ch.card))===digest)matches.push({id:w.id,name:row.name,relation:sameFile?'file':'definition'});
  }
  return matches;
}
