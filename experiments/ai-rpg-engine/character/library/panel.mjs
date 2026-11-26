const collator=new Intl.Collator('zh-CN',{numeric:true});
export function libraryPanel({document,window,request,run,render,refresh,open,notice}){
  const node=id=>document.getElementById(id),selected=new Set();
  const pref=id=>snapshot.items[id]??{tags:[],favorite:false};
  let snapshot={revision:0,items:{},canUndo:false};
  const option=(label,value)=>{const el=document.createElement('option');el.textContent=label;el.value=value;return el;};
  function selection(){
    node('library-count').textContent='已选 '+selected.size+' 位';
    node('apply-library').disabled=!selected.size;
    node('undo-library').disabled=!snapshot.canUndo;
  }
  for(const id of ['library-search','library-tag','library-favorites','library-sort']){
    node(id).addEventListener(id==='library-search'?'input':'change',()=>{selected.clear();render();});
  }
  async function reload(){
    snapshot=await request('/characters/library');selected.clear();
    const filter=node('library-tag'),current=filter.value;
    const tags=new Set(Object.values(snapshot.items).flatMap(i=>i.tags));filter.replaceChildren(option('所有个人标签',''),...[...tags].sort(collator.compare).map(t=>option(t,t)));
    filter.value=tags.has(current)?current:'';
    selection();
  }
  function filter(rows,query){
    const tag=node('library-tag').value;
    const list=rows.filter(w=>{const meta=pref(w.id);return (!node('library-favorites').checked||meta.favorite)&&(!tag||meta.tags.includes(tag))&&
      [w.name,...(w.tags??[]),...meta.tags].join(' ').toLocaleLowerCase().includes(query);});
    if(node('library-sort').value==='name')list.sort((a,b)=>collator.compare(a.name,b.name)||a.id.localeCompare(b.id));
    selection();return list;
  }
  async function write(action,ids,value){
    try{await request('/characters/library',{operationId:window.crypto.randomUUID(),expectedRevision:snapshot.revision,action,ids,value});}
    finally{await reload();render();}
    notice(action==='undo'?'已撤销上次整理。':'已更新个人角色库。');
  }
  node('library-action').onchange=()=>{node('library-label').hidden=!node('library-action').value.endsWith('tag');};
  node('apply-library').onclick=()=>run(async()=>{
    const action=node('library-action').value,ids=[...selected],tag=node('library-label-value').value.trim();
    if(action.endsWith('tag')&&!tag){node('library-label-value').focus();return;}
    if(ids.length&&window.confirm('对选中的 '+ids.length+' 位角色执行“'+node('library-action').selectedOptions[0].textContent+'”？'))
      await write(action==='unfavorite'?'favorite':action,ids,action.endsWith('tag')?tag:action==='favorite');
  });
  node('undo-library').onclick=()=>run(()=>write('undo',[],null));
  function decorate(w,body,actions){
    const meta=pref(w.id),label=document.createElement('label'),cb=document.createElement('input');
    cb.type='checkbox';cb.checked=selected.has(w.id);cb.setAttribute('aria-label','选择 '+w.name);
    cb.onchange=()=>{if(cb.checked)selected.add(w.id);else selected.delete(w.id);selection();};
    label.append(cb,' 选择');body.prepend(label);
    if(meta.tags.length){const tags=document.createElement('small');tags.textContent='个人标签 · '+meta.tags.join('、');body.append(tags);}
    const button=(text,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>run(fn);actions.append(b);return b;};
    button(meta.favorite?'已收藏':'收藏',()=>write('favorite',[w.id],!meta.favorite)).setAttribute('aria-pressed',String(meta.favorite));
    button('复制当前草稿',async()=>{
      const name=window.prompt('副本名称',w.name+' 副本');if(!name?.trim())return;
      await request('/works/'+w.id+'/copy',{operationId:window.crypto.randomUUID(),expectedRevision:w.revision,name:name.trim()});await refresh();notice('已复制草稿和资源，可在库中打开副本。');
    });
    button('查看来源',async()=>{
      const info=await request('/characters/library/source?work='+encodeURIComponent(w.id));
      node('library-source-body').textContent=['素材来源：'+(info.origins.map(o=>[o.fileName,o.origin].filter(Boolean).join(' · ')).filter(Boolean).join('\n')||'未提供'),
        '卡内来源：'+(info.cardSources.join('、')||'未提供'),'作者说明（含作者填写的分发条件）：'+(info.creatorNotes||'未提供'),
        ...(info.parent?['复制自 '+info.parent.id+' · 修订 '+info.parent.revision]:[]),...(info.hash?['原文件 SHA-256：'+info.hash]:[])].join('\n\n');
      node('library-source-dialog').showModal();
    });
  }
  node('library-source-close').onclick=()=>node('library-source-dialog').close();
  function matches(rows){
    const box=node('import-matches');box.replaceChildren();box.hidden=!rows?.length;
    for(const match of rows??[]){
      const line=document.createElement('p'),button=document.createElement('button');
      line.append((match.relation==='file'?'原文件相同':'角色定义相同，封装或资源可能不同')+' · '+match.name+' ');
      button.type='button';button.textContent='打开已有角色';button.onclick=()=>run(async()=>{node('import-dialog').close();await open(match.id);});
      line.append(button);box.append(line);
    }
  }
  return {filter,reload,decorate,matches};
}
