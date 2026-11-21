export function install({api, load, state, write, el}) {
  const $=id=>document.getElementById(id);
  $('previous').textContent='长对话工作台';
  document.querySelector('nav [aria-current="page"]').textContent='资料分支';
  $('derived').closest('details').hidden=true;
  const opener=el('button','总结与记忆');
  opener.type='button'; $('show-context').after(opener);
  const dialog=el('dialog');dialog.id='memory-dialog';dialog.setAttribute('aria-label','总结与记忆');document.body.append(dialog);
  let changed=false, locked=false, snapshot, actor, data;
  const error=el('p');error.setAttribute('role','alert');
  function close(){if(!changed||confirm('放弃尚未保存的资料编辑？')){changed=false;dialog.close();opener.focus();}}
  dialog.addEventListener('cancel',e=>{e.preventDefault();if(!locked)close();});
  window.addEventListener('beforeunload',e=>{if(changed){e.preventDefault();e.returnValue='';}});
  function btn(label,fn){const b=el('button',label);b.type='button';b.onclick=async()=>{
    if(locked)return;locked=true;b.disabled=true;
    try{error.textContent='';await fn();}catch(e){error.textContent=e.message;}
    finally{b.disabled=false;locked=false;}
  };return b;}
  function dropdown(label,opts,selected){
    const select=el('select');
    for(const [value,title] of opts){const o=el('option',title);o.value=value;select.append(o);}
    select.value=selected;return field(label,select);
  }
  function field(label,input){const l=el('label',label);l.append(input);dialog.append(l);return input;}
  function shell(){const head=el('div',undefined,'panel-heading');head.append(el('h2','总结与记忆'),btn('关闭资料面板',close));dialog.replaceChildren(head,error);dialog.scrollTop=0;}
  async function refresh(){snapshot=state();actor=$('target').value;
    data=await api('notes?'+new URLSearchParams({family:snapshot.id,branch:snapshot.branchId,actor}));list();}
  async function save(action,payload){
    const intent=write({branchId:snapshot.branchId,actor,...payload});intent.expectedRevision=snapshot.revision;
    await api(action,intent);changed=false;await load(snapshot.id,snapshot.branchId);await refresh();
  }
  const labels={valid:'已采纳',draft:'草稿',invalid:'依据已变更',disabled:'已停用','needs-review':'待确认'};
  function list(){
    changed=false;shell();dialog.append(btn('新增资料',()=>edit()),el('p','当前角色：'+snapshot.members.find(m=>m.id===actor).name));
    if(!data.records.length)dialog.append(el('p','还没有为这个角色整理资料。'));
    const search=field('搜索当前角色资料',el('input'));search.type='search';
    const index=[];
    search.oninput=()=>{const term=search.value.normalize('NFKC').toLowerCase();for(const [item,text] of index)item.hidden=!text.includes(term);};
    for(const n of data.records){
      const section=el('section');section.append(el('h3',(n.owner?'成员私有':'公共')+' · '+labels[n.status]),
        el('p',n.content),el('small',({author:'作者设定',experience:'对话经历',legacy:'旧资料'})[n.origin]+' · '+(n.mode==='fixed'?'固定保留':n.words.join('、'))));
      if(n.refs.length){const d=el('details');d.append(el('summary','查看消息依据'));for(const ref of n.refs){
        const m=data.messages.find(m=>m.id===ref.id&&m.revisionId===ref.revisionId);
        d.append(el('p','第 '+ref.position+' 条 · '+(m?m.content:'原修订已失效')));
        if(m)d.append(btn('定位第 '+ref.position+' 条消息',()=>{
          dialog.close();const row=[...$('messages').children].find(r=>r.dataset.messageId===m.id);
          if(row){row.tabIndex=-1;row.focus();row.scrollIntoView({block:'center'});}
        }));
      }section.append(d);}
      section.append(btn('编辑资料',()=>edit(n)));
      if(n.status==='draft')section.append(btn('采纳',()=>save('adopt',{noteId:n.noteId})));
      if(n.action==='adopt')section.append(btn('停用',()=>save('disable',{noteId:n.noteId})));
      if(n.status==='valid'&&!n.owner&&n.origin==='experience'&&snapshot.source.kind==='group')
        section.append(btn('告知当前群聊',()=>{shell();dialog.append(el('p','以玩家身份发送以下公开消息'));const body=field('告知正文',el('textarea'));body.value=n.content;dialog.append(btn('确认告知',()=>save('tell',{noteId:n.noteId,content:body.value})),btn('返回',list));}));
      index.push([section,[n.content,...n.words].join(' ').normalize('NFKC').toLowerCase()]);dialog.append(section);
    }
  }
  function edit(n){
    shell();const kind=dropdown('资料类型',[['summary','总结'],['memory','记忆']],n?.kind??'summary');
    const scope=dropdown('可见范围',[['public','公共'],['private','当前角色私有']],n?.owner?'private':'public');scope.disabled=!!n;
    const origin=dropdown('来源',[['experience','对话经历'],['author','作者设定']],n?.origin==='author'?'author':'experience');
    const body=field('资料正文',el('textarea'));body.value=n?.content??'';body.maxLength=16000;
    const mode=dropdown('使用方式',[['fixed','固定保留'],['keyword','关键词召回']],n?.mode??'fixed');
    const words=field('关键词（逗号分隔）',el('input'));words.value=n?.words.join('，')??'';
    const hint=el('p');const refs=el('fieldset');refs.append(el('legend','选择当前角色可见的消息'));dialog.append(hint,refs);
    for(const m of data.messages){
      const label=el('label'),box=el('input');box.type='checkbox';box.value=m.id;box.checked=!!n?.refs.some(r=>r.revisionId===m.revisionId);
      label.append(box,document.createTextNode(m.content));refs.append(label);
    }
    function update(){refs.hidden=origin.value==='author';words.disabled=mode.value==='fixed';
      hint.textContent=scope.value==='public'&&origin.value==='author'?'这是分支共享背景，后来加入的角色也能使用。':'';}
    kind.onchange=()=>{mode.value=kind.value==='summary'?'fixed':'keyword';update();};
    origin.onchange=scope.onchange=mode.onchange=update;update();
    dialog.append(btn('保存草稿',()=>save('draft',{noteId:n?.noteId??null,kind:kind.value,origin:origin.value,
      owner:scope.value==='private'?actor:null,content:body.value,mode:mode.value,
      words:words.value.split(/[,，]/).map(w=>w.trim()).filter(Boolean),
      refs:origin.value==='author'?[]:[...refs.querySelectorAll('input:checked')].map(box=>{
        const {id,revisionId}=data.messages.find(m=>m.id===box.value);return {id,revisionId};})})),
      btn('返回资料列表',()=>{if(!changed||confirm('放弃尚未保存的资料编辑？'))list();}));
    body.focus();
  }
  dialog.addEventListener('input',e=>{if(e.target.type!=='search')changed=true;});
  opener.onclick=async()=>{dialog.showModal();shell();dialog.querySelector('button').focus();try{await refresh();}catch(e){error.textContent=e.message;}};
}
