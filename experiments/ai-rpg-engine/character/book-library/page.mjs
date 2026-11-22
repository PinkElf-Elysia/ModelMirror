const $=id=>document.getElementById(id);
const el=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
const option=(s,value,label)=>s.add(new Option(label,value));
const op=()=>crypto.randomUUID();
let catalog,bookId=null,draftRevision=null,book,dirty=false,rawDirty=false,busy=false,source,view,consent,prepared;
const empty=()=>({extensions:{},entries:[]});
async function api(action,args){
  const response=await fetch('/rpg-app/api/authoring/characters/books',{method:'POST',headers:{'Content-Type':'application/json','x-authoring-request':'1'},body:JSON.stringify({action,args})});
  const value=await response.json();if(!response.ok)throw Error(value.code??value.error?.code??JSON.stringify(value));return value;
}
async function run(task){
  if(busy)return;busy=true;
  try{await task();}catch(error){$('status').textContent='操作未完成 · '+error.message;clearReview();}
  finally{busy=false;}
}
const bind=(id,fn)=>$(id).onclick=()=>run(fn);
function clearReview(){consent=null;prepared=null;$('confirm').disabled=true;$('send').disabled=true;$('review').textContent='';$('assembly').textContent='';$('explain').replaceChildren();}
function dirtyBook(){dirty=true;$('status').textContent='草稿尚未保存';}
function sync(){rawDirty=false;$('json').value=JSON.stringify(book,null,2);}
function renderEntries(){
  $('entries').replaceChildren();
  book.entries.forEach((e,i)=>{const row=el('fieldset');row.append(el('legend','条目 '+(i+1)));
    for(const [key,text,tag] of [['keys','触发词（每行一个）','textarea'],['content','插入内容','textarea']]){
      const l=el('label',text),field=el(tag);field.value=key==='keys'?e.keys.join('\n'):e.content;
      field.oninput=()=>{e[key]=key==='keys'?field.value.split('\n').filter(Boolean):field.value;sync();dirtyBook();};l.append(field);row.append(l);
    }
    const remove=el('button','移除此条');remove.onclick=()=>{book.entries.splice(i,1);renderEntries();dirtyBook();};row.append(remove);$('entries').append(row);
  });sync();
}
async function loadBook(id){
  if(dirty&&!confirm('放弃尚未保存的角色书修改？')){$('book').value=bookId??'';return;}
  const d=id?await api('draft',{bookId:id}):{title:'',book:empty()};bookId=id||null;draftRevision=d.revision??catalog.revision;book=d.book;$('title').value=d.title;dirty=false;renderEntries();
  $('seal').disabled=$('archive').disabled=!id||catalog.books.find(b=>b.id===id)?.archived;
}
async function refresh(){
  const oldSource=$('source').value,oldBook=bookId??'',oldRole=$('role').value,oldProfile=$('profile').value;
  catalog=await api('catalog',{});$('book').replaceChildren();option($('book'),'','新建角色书');$('refs').replaceChildren();
  for(const b of catalog.books){option($('book'),b.id,b.title+(b.archived?' · 已归档':''));if(!b.archived)for(const v of b.versions)option($('refs'),v.bookVersionId,v.title+' · v'+v.number);}
  $('book').value=oldBook;$('player-refs').replaceChildren(...[...$('refs').options].map(o=>new Option(o.text,o.value)));
  for(const [id,items] of [['role',catalog.roles],['profile',catalog.profiles]]){$(id).replaceChildren();for(const item of items)option($(id),item.id,item.name+' · '+item.id.slice(-6));}
  if(catalog.roles.some(r=>r.id===oldRole))$('role').value=oldRole;
  if(catalog.profiles.some(p=>p.id===oldProfile))$('profile').value=oldProfile;
  restoreRoleDraft();await restorePlayer();
  $('source').replaceChildren();
  for(const s of catalog.sessions)option($('source'),s.kind+'/'+s.id,s.title+' · '+(s.kind==='group'?'群聊':'单聊'));
  for(const f of catalog.families)for(const b of f.branches)option($('source'),f.id+'/'+b.id,b.title+' · '+f.id.slice(-4)+'/'+b.id.slice(-4));
  if([...$('source').options].some(o=>o.value===oldSource))$('source').value=oldSource;
}
const versions=()=>catalog.books.flatMap(b=>b.versions);
const pin=v=>({bookId:v.bookId,bookVersionId:v.bookVersionId,hash:v.hash});
function restoreRoleDraft(){const ids=new Set((catalog.roleDrafts[$('role').value]??[]).map(r=>r.bookVersionId));for(const o of $('refs').options)o.selected=ids.has(o.value);}
$('role').onchange=restoreRoleDraft;
bind('save-role',async()=>{const role=catalog.roles.find(r=>r.id===$('role').value);if(!role)throw Error('请选择角色');await api('role-draft',cmd({workId:role.id,workRevision:role.revision,refs:refs()}));await refresh();$('role').value=role.id;restoreRoleDraft();$('status').textContent='已保存角色草稿书目';});
const refs=(field='refs')=>[...$(field).selectedOptions].map(o=>pin(versions().find(v=>v.bookVersionId===o.value)));
const cmd=payload=>({operationId:op(),expectedRevision:catalog.revision,payload});
$('book').onchange=()=>run(()=>loadBook($('book').value));
$('title').oninput=dirtyBook;$('json').oninput=()=>{rawDirty=true;dirtyBook();};
bind('apply',async()=>{const parsed=JSON.parse($('json').value);await api('trial',{title:$('title').value||'草稿',book:parsed,input:''});book=parsed;renderEntries();dirtyBook();});
bind('add',async()=>{if(rawDirty)throw Error('请先应用完整 JSON');book.entries.push({keys:[],content:'',extensions:{},enabled:true,insertion_order:book.entries.length});renderEntries();dirtyBook();});
function savedForm(){if(rawDirty)throw Error('请先应用完整 JSON');return {title:$('title').value,book};}
bind('trial',async()=>{const result=await api('trial',{...savedForm(),input:$('trigger').value});$('trial-result').textContent=[...result.before,...result.after].join('\n')||'没有命中条目';});
bind('save',async()=>{const r=await api('save',{...cmd({bookId,...savedForm()}),expectedRevision:draftRevision});dirty=false;bookId=r.bookId;await refresh();await loadBook(bookId);$('status').textContent='草稿已保存';});
for(const action of ['seal','archive'])bind(action,async()=>{if(dirty)throw Error('请先保存草稿');if(action==='archive'&&!confirm('归档此书？已有分支仍可使用已固定版本。'))return;await api(action,cmd({bookId}));await refresh();await loadBook(bookId);$('status').textContent=action==='seal'?'已封存新版本':'已归档';});
bind('seal-role',async()=>{const role=catalog.roles.find(r=>r.id===$('role').value);if(!role)throw Error('请选择角色');if(!confirm(role.name+' 将封存为新角色版本，绑定 '+([...$('refs').selectedOptions].map(o=>o.textContent).join('、')||'零本独立书')+'。继续？'))return;await api('role',cmd({workId:role.id,workRevision:role.revision,refs:refs()}));await refresh();$('status').textContent='角色新版本及绑定已封存';});
async function restorePlayer(){const p=catalog.profiles.find(r=>r.id===$('profile').value);if(!p)return;const ids=new Set((await api('recommendations',{profileId:p.id,profileRevision:p.revision})).map(r=>r.bookVersionId));for(const o of $('player-refs').options)o.selected=ids.has(o.value);}
$('profile').onchange=()=>run(restorePlayer);
bind('recommend',async()=>{const p=catalog.profiles.find(r=>r.id===$('profile').value);if(!p)throw Error('请选择档案');await api('profile',cmd({profileId:p.id,profileRevision:p.revision,refs:refs('player-refs')}));await refresh();$('profile').value=p.id;await restorePlayer();$('status').textContent='已保存推荐书，角色阅读范围须在分支中确认';});
function grantForm(members,recommended=[]){
  const host=$('grants');host.replaceChildren();
  for(const [owner,title] of [['session','会话书'],['player','玩家书']]){
    const section=el('fieldset');section.append(el('legend',title));
    const inherit=el('label','继承分支点设置 '),box=el('input');box.type='checkbox';box.id=owner+'-inherit';box.checked=source.kind==='fork';inherit.append(box);if(source.kind!=='fork')inherit.hidden=true;section.append(inherit);
    for(const b of catalog.books.filter(b=>!b.archived&&b.versions.length)){
      const row=el('fieldset');row.dataset.owner=owner;row.append(el('legend',b.title));
      const s=el('select');s.setAttribute('aria-label',b.title+'版本');option(s,'','不绑定');for(const v of b.versions)option(s,v.bookVersionId,'v'+v.number);
      if(owner==='player'){const r=recommended.find(r=>r.bookId===b.id);if(r)s.value=r.bookVersionId;}
      row.append(s);
      for(const member of [{id:'all',name:'当前全部角色'},...members]){const l=el('label'),input=el('input');input.type='checkbox';input.value=member.id;l.append(input,document.createTextNode(' '+member.name+(member.id==='all'?'':' · '+member.id.slice(-6))));row.append(l);}
      row.disabled=box.checked;section.append(row);
    }
    box.onchange=()=>{for(const row of section.querySelectorAll('fieldset'))row.disabled=box.checked;};host.append(section);
  }
}
function grants(owner){if(source.kind==='fork'&&$(owner+'-inherit').checked)return null;
  return [...$('grants').querySelectorAll('fieldset[data-owner='+owner+']')].filter(n=>n.querySelector('select').value).map(row=>{
    const ref=pin(versions().find(v=>v.bookVersionId===row.querySelector('select').value)),ids=[...row.querySelectorAll('input:checked')].map(i=>i.value);return {ref,readers:ids.includes('all')?'all':ids};
  });}
async function showSource(target=null){
  clearReview();const [a,b]=$('source').value.split('/');source=null;$('chat').hidden=true;$('parent-controls').hidden=true;$('grants').replaceChildren();if(!b)return;
  if(a.startsWith('book-family-')){
    source={kind:'fork',familyId:a,branchId:b};view=await api('view',{familyId:a,branchId:b,targetId:target});
    $('chat').hidden=$('parent-controls').hidden=false;$('actor').replaceChildren();for(const m of view.members)option($('actor'),m.id,m.name+' · '+m.id.slice(-6));$('actor').value=view.targetId;
    $('bindings').textContent='可用角色书 · '+(view.bindings.map(v=>v.title).join('、')||'无独立书');
    $('point').replaceChildren();option($('point'),'','分支当前末尾');$('message').replaceChildren();option($('message'),'','新消息或接话');$('history').replaceChildren();
    for(const m of view.messages){$('history').append(el('li',m.content));option($('point'),m.id,m.content.slice(0,40));if(m.canRegenerate&&m.speakerId===view.targetId)option($('message'),m.id,'精确重生成 · '+m.content.slice(0,32));}
    $('requests').replaceChildren();for(const r of view.requests){const l=el('p','请求 · '+r.status);if(['prepared','unknown','dispatching'].includes(r.status)){const btn=el('button','核对原请求');btn.onclick=()=>run(async()=>{await api('recover',{familyId:a,requestId:r.id});await refresh();await showSource(target);});l.append(btn);}$('requests').append(l);}
    grantForm(view.members);
  }else{const info=await api('source',{kind:a,sourceId:b});source={kind:a,sourceId:b,sourceHash:info.sourceHash};grantForm(info.members,info.recommended);}
}
$('point').onchange=()=>run(async()=>{if(source?.kind==='fork'){const a=await api('anchor',{familyId:source.familyId,branchId:source.branchId,point:$('point').value||null});grantForm(a.members);clearReview();}});
$('source').onchange=()=>run(()=>showSource());$('actor').onchange=()=>run(()=>showSource($('actor').value));
for(const id of ['v3-rules','grants','point','branch-title','message','input'])$(id).addEventListener('input',clearReview);
bind('inspect',async()=>{if(!source)throw Error('请先创建单聊或群聊');const {kind,...base}=source;
  const payload={...base,player:grants('player'),session:grants('session')};
  if(kind==='fork')Object.assign(payload,{point:$('point').value||null,title:$('branch-title').value});else payload.kind=kind;
  if($('v3-rules').checked)payload.regexPolicy='v3-bounded/1';
  const command=cmd(payload),action=kind==='fork'?'fork':'open',review=await api('inspect-'+action,command);
  $('review').textContent=review.actors.map(m=>m.name+' · '+m.id.slice(-6)+'：'+(m.books.map(v=>v.title+' v'+v.number+'（'+v.sources.map(s=>({session:'会话',role:'角色',player:'玩家'})[s.owner]+(s.bookVersionId===v.sources[0].bookVersionId?'':' · 已覆盖')).join('、')+'）').join('，')||'无独立书')).join('\n');
  consent={action,command};$('confirm').disabled=false;
});
bind('confirm',async()=>{if(!consent)return;const r=await api(consent.action,consent.command);await refresh();$('source').value=r.familyId+'/'+r.branchId;history.replaceState(null,'','?books=1&family='+r.familyId+'&branch='+r.branchId+'#branches');await showSource();$('status').textContent='已创建独立分支';});
bind('preview',async()=>{const intent={branchId:source.branchId,targetId:view.targetId,messageId:$('message').value||null,input:$('message').value?null:$('input').value.trim()||null},operationId=op();
  const result=await api('preview',{familyId:source.familyId,intent,operationId});prepared={operationId,expectedRevision:result.revision,payload:{familyId:source.familyId,intent,previewHash:result.previewHash}};
  const names={'regex-invalid':'表达式无效','regex-unsupported':'语法未支持','regex-limit':'规则超过上限','regex-empty':'可能空匹配','regex-scan-limit':'可见文本超过上限','regex-request-limit':'本次正则总步数已耗尽',triggered:'已纳入','not-triggered':'未命中','book-budget':'超过本书预算','request-budget':'超过本次总预算','budget-excluded':'超过角色书预算',disabled:'已停用','unsupported-behavior':'保留的未支持行为','recursion-limit':'达到递归上限'};
  const owners={card:'卡内',role:'角色',session:'会话',player:'玩家'};
  const summary=el('p',result.books.exact?'沿用原请求上下文':('本次角色书预算 '+result.bookBudget+' · 纳入 '+result.entries.filter(e=>e.status==='triggered').length+' 条'));
  const list=el('ul');for(const e of result.entries){const version=e.version?' v'+e.version:'';list.append(el('li',owners[e.owner]+' · '+e.title+version+' / '+(e.name||'条目 '+(e.index+1))+' · '+(names[e.status]??e.status)+(Number.isInteger(e.key)?' · 第 '+(e.key+1)+' 个模式':'')+(e.status==='triggered'?' · '+(e.position==='before_char'?'人设前':'人设后'):'')));}
  $('explain').replaceChildren(summary,list);
  $('assembly').textContent=JSON.stringify({messages:result.messages,books:result.books,hash:result.requestHash},null,2);$('send').disabled=view.pending;
});
bind('send',async()=>{if(!prepared)return;await api('send',prepared);$('input').value='';await refresh();await showSource(view.targetId);$('status').textContent='离线回复已保存';});
function tab(){const active=['assign','branches'].includes(location.hash.slice(1))?location.hash.slice(1):'library';
  for(const node of document.querySelectorAll('main>section'))node.hidden=node.id!==active;
  for(const a of document.querySelectorAll('nav a'))if(a.hash==='#'+active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
}
addEventListener('hashchange',tab);addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
tab();run(async()=>{await refresh();await loadBook(null);const q=new URLSearchParams(location.search),v=q.has('source')?q.get('kind')+'/'+q.get('source'):q.get('family')+'/'+q.get('branch');if(q.has('work')&&catalog.roles.some(r=>r.id===q.get('work')))$('role').value=q.get('work');if([...$('source').options].some(o=>o.value===v))$('source').value=v;restoreRoleDraft();await restorePlayer();await showSource();}).finally(()=>{
  const main=document.querySelector('main');main.inert=false;main.removeAttribute('aria-busy');
});
