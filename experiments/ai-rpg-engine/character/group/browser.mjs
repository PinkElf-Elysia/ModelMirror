import {renderMessageBody} from './message-display.mjs';

// Dependencies are explicit so interaction regressions can run without a model or browser network.
export async function mountGroupUI({document,window,fetch,location,history,crypto}) {
 const $=id=>document.getElementById(id);
 const el=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
 let boot,state=null,busy=false,slots=2,screen='home';
 const drafts=new Map();let setupDirty=false;const setupKey='rpg-group-setup:1';
 const labels={prepared:'已准备，尚未派发',dispatching:'正在等待回复',unknown:'结果未知，额度已保留',completed:'已完成',failed:'失败，未追加回复'};
 const errors={GROUP_REQUEST_PENDING:'有尚未解决的请求，请先核对原请求结果。',GROUP_REVISION_CONFLICT:'会话已在其他页面更新，请刷新后重新操作。',GROUP_EXECUTION_DISABLED:'当前宿主未开放发送。你仍可查看和创建群聊。',GROUP_TASK_BUDGET_EXHAUSTED:'任务总额度不足，创建新群聊不会增加额度。',GROUP_BUDGET_EXHAUSTED:'本群剩余额度不足。',GROUP_PRICE_UNKNOWN:'无法确认费用上界，已阻止发送。',GROUP_RECOVERY_REQUIRED:'本地写入结果需要核对。请先刷新状态，不要重复发送。'};
 const pending=s=>s.requests.find(r=>['prepared','dispatching','unknown'].includes(r.status));
 const modelName=id=>boot.models.find(m=>m.id===id)?.label??id;
 const characterNames=g=>g.members.filter(m=>m.kind==='character').map(m=>m.name).join('、');
 const groupLabel=g=>`${g.scene.slice(0,24)} · ${g.id.slice(-6)}`;
 function notice(error){$('error').textContent=errors[error.message]??error.message??'操作失败';$('error').hidden=false;}
 async function api(path,body){const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Group-CSRF':boot.csrf}:{},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok)throw Error(value.error);return value;}
 function options(select,values,selected){select.replaceChildren(...values.map(([value,label])=>{const option=el('option',label);option.value=value;return option;}));if(selected!==undefined)select.value=selected;}
 const cardKey=c=>c?.reference?c.reference.workId+'/'+c.reference.versionId:'';
 function catalog(value){boot=value;boot.cards.sort((a,b)=>a.name.localeCompare(b.name,'zh-CN')||b.version-a.version);$('character-library').hidden=!boot.charactersUrl;if(boot.charactersUrl)$('character-library').href=boot.charactersUrl;}
 const choices=()=>Array.from({length:slots},(_,i)=>{const card=$('card-'+i);return card?{key:card.value===''?'':cardKey(boot.cards[Number(card.value)]),model:$('model-'+i)?.value}:undefined;});
 const cardOptions=()=>[['','选择已封存版本'],...boot.cards.map((c,i)=>[String(i),`${c.name} · 版本 ${c.version}`])];
 const modelOptions=()=>boot.models.map(m=>[m.id,m.label]);
 const envelope=payload=>({operationId:crypto.randomUUID(),expectedRevision:state.revision,payload});
 function remember(){const i=boot.groups.findIndex(g=>g.id===state.id);if(i>=0)boot.groups[i]=state;else boot.groups.unshift(state);history.replaceState(null,'','/groups?id='+state.id);}
 function closeMembers(){const dialog=$('member-dialog');if(dialog.open)dialog.close();$('member-dock').append($('members'));}
 function show(next){if(next!==screen)closeMembers();screen=next;document.body.dataset.view=next;for(const [id,view] of [['welcome-history','home'],['setup','setup'],['chat','chat']])$(id).hidden=next!==view;$('toggle-members').hidden=next!=='chat';$('home').setAttribute('aria-current',next==='home'?'page':'false');}
 function lock(){for(const id of ['new','home','start','refresh','cancel-create','history'])$(id).disabled=busy;for(const node of $('create-form').elements)node.disabled=busy||!boot.cards.length||!boot.models.length;$('create').disabled=busy||!boot.models.length||Array.from({length:slots},(_,i)=>!$('card-'+i)?.value).some(Boolean);}
 async function run(action){
  if(busy)return;busy=true;lock();$('error').hidden=true;$('status').textContent='正在核对并保存…';if(screen==='chat')render();
  try{await action();$('status').textContent='已更新本地状态';}
  catch(error){notice(error);$('status').textContent='操作未确认。请刷新或核对原请求，系统不会自动补跑。';if(state)try{state=await api('/api/groups/'+state.id);remember();}catch{}}
  finally{busy=false;lock();if(screen==='chat')render();}
 }
 function openingPreview(){const selected=$('opening').value?JSON.parse($('opening').value):null;$('opening-preview').textContent=selected?boot.cards[Number($('card-'+selected.memberIndex).value)].openings.find(x=>x.kind===selected.kind).texts[selected.index]:'';$('create-summary').textContent=`${slots} 位角色 · ${selected?'一条预设开场':'由你先发言'} · 创建后保存到本地`;}
 function openingOptions(){
  const choices=[['','由玩家先发言']];for(let n=0;n<slots;n++){const c=$('card-'+n).value===''?null:boot.cards[Number($('card-'+n).value)];if(c)for(const list of c.openings)list.texts.forEach((text,i)=>{if(text.trim())choices.push([JSON.stringify({memberIndex:n,kind:list.kind,index:i}),`${c.name}（成员 ${n+1}）· ${list.kind==='group'?'群聊':'普通'}开场 ${i+1}`]);});}
  const previous=$('opening').value;options($('opening'),choices,choices.some(([value])=>value===previous)?previous:'');openingPreview();
 }
 function renderSlots(previous=choices()){
  const defaults=boot.cards.filter((c,i,all)=>all.findIndex(x=>x.reference.workId===c.reference.workId)===i),assigned=new Set();$('slots').replaceChildren();
  for(let n=0;n<slots;n++){
   const row=el('div');row.className='slot';const cardLabel=el('label','角色 '+(n+1)),card=el('select');card.id='card-'+n;const selected=previous[n]?boot.cards.findIndex(c=>cardKey(c)===previous[n].key):boot.cards.indexOf(defaults.find(c=>!assigned.has(c.reference.workId))??defaults[0]);if(selected>=0)assigned.add(boot.cards[selected].reference.workId);options(card,cardOptions(),selected<0?'':String(selected));cardLabel.append(card);
   const modelLabel=el('label','使用模型'),model=el('select');model.id='model-'+n;options(model,modelOptions(),previous[n]?.model??$('batch-model').value??boot.models[0]?.id);modelLabel.append(model);
   const info=el('p');info.className='slot-info';const update=()=>{const c=card.value===''?null:boot.cards[Number(card.value)];info.textContent=c?`固定版本 ${c.version} · ${c.reference.versionId.slice(-8)}`:'';$('opening').value='';openingOptions();lock();};card.onchange=update;row.append(cardLabel,modelLabel,info);$('slots').append(row);const c=card.value===''?null:boot.cards[Number(card.value)];info.textContent=c?`固定版本 ${c.version} · ${c.reference.versionId.slice(-8)}`:'';
  }
  $('add-slot').textContent=slots===2?'添加第三位角色':'移除第三个选择';openingOptions();
 }
 function saveSetup(){
  if(screen!=='setup'||!setupDirty)return;
  const fields=Object.fromEntries(['player','scene','calls','money','batch-model'].map(id=>[id,$(id).value]));
  try{window.sessionStorage.setItem(setupKey,JSON.stringify({format:1,fields,slots,selected:choices(),opening:$('opening').value}));}catch{notice(Error('本标签页无法暂存草稿，请勿刷新或离开，先完成创建。'));}
 }
 function restoreSetup(preferred=null){
  let saved;try{saved=JSON.parse(window.sessionStorage.getItem(setupKey)||'null');}catch{}
  const valid=saved?.format===1&&[2,3].includes(saved.slots)&&Array.isArray(saved.selected)&&saved.selected.length===saved.slots&&saved.selected.every(x=>x&&typeof x.key==='string'&&typeof x.model==='string')&&saved.fields&&['player','scene','calls','money','batch-model'].every(id=>typeof saved.fields[id]==='string')&&typeof saved.opening==='string';
  setupDirty=!!valid;
  if(valid){slots=saved.slots;for(const [id,value] of Object.entries(saved.fields))if(['player','scene','calls','money','batch-model'].includes(id))$(id).value=value;}
  const selected=valid?saved.selected:[];
  const same=preferred&&selected[0]?.key===preferred;if(preferred)selected[0]={key:preferred,model:selected[0]?.model};
  renderSlots(selected);if(valid&&(!preferred||same)&&[...$('opening').options].some(x=>x.value===saved.opening)){$('opening').value=saved.opening;openingPreview();}
  if(valid)$('status').textContent='已恢复本标签页的群聊创建草稿，尚未创建群聊。';
 }
 function display(member){return member.kind==='player'?member.name:`${member.name} · ${state.members.filter(m=>m.kind==='character').findIndex(m=>m.id===member.id)+1}`;}
 function composer(){
  const target=state.members.find(m=>m.id===$('target').value),blocked=busy||!!pending(state)||boot.mode==='disabled'||!target;
  $('send').disabled=blocked||!$('input').value.trim();$('continue').disabled=blocked;
  $('send').textContent=target?`发送给 ${display(target)}`:'发送消息';$('continue').textContent=target?`请 ${display(target)} 接话`:'让角色接话';
  $('target-model').textContent=target?modelName(target.modelId):state.members.some(m=>m.kind==='character'&&m.active)?'选择一位在场角色回应':'请先让一位角色回到群聊';
  $('input-count').textContent=$('input').value.length?`${$('input').value.length.toLocaleString('zh-CN')} 字符`:'';
  $('send-hint').textContent=pending(state)?'请求尚未结束。请核对原请求结果，刷新不会再次生成。':boot.mode==='disabled'?'当前未开放发送。你可以阅读消息、查看成员或创建群聊。':'消息对在场成员公开。接话不发送输入框中的草稿。';
 }
 function render(){
  show('chat');$('chat-title').textContent=characterNames(state);$('scene-label').textContent=state.scene;
  options($('history'),boot.groups.map(g=>[g.id,groupLabel(g)]),state.id);
  const unresolved=pending(state),locked=busy||!!unresolved;
  const selected=$('target').value;options($('target'),[['','请选择回应者'],...state.members.filter(m=>m.kind==='character'&&m.active).map(m=>[m.id,display(m)])]);if([...$('target').options].some(o=>o.value===selected))$('target').value=selected;
  $('target').disabled=locked; $('input').disabled=locked||boot.mode==='disabled';composer();
  const messages=$('messages'),firstPaint=!messages.childElementCount,scroll=messages.scrollTop,atBottom=messages.scrollHeight-messages.clientHeight-scroll<60;
  messages.replaceChildren();if(!state.messages.length)messages.append(el('p','还没有消息。选择回应者，写下你想说的话。'));
  for(const message of state.messages){const row=el('article');row.className='message';row.id='message-'+message.id;const member=state.members.find(m=>m.id===message.speakerId);row.dataset.speaker=member.kind;row.append(el('strong',display(member)),el('small',message.source.kind==='opening'?'预设开场 · 未调用模型':message.source.kind==='model-result'?`模型：${modelName(message.source.modelId)}`:'玩家消息 · 群内公开'));renderMessageBody(document,row,message);messages.append(row);}
  messages.scrollTop=firstPaint?0:atBottom?messages.scrollHeight:scroll;
  $('member-list').replaceChildren();for(const member of state.members){const row=el('section');row.className='member';row.append(el('strong',display(member)),el('small',member.active?'在场':'已离场 · 缺席期间消息不可见'));
   if(member.kind==='character'){const model=el('select');model.setAttribute('aria-label',display(member)+'的模型');options(model,modelOptions(),member.modelId);model.disabled=locked;model.onchange=()=>run(async()=>{state=await api(`/api/groups/${state.id}/model`,envelope({memberId:member.id,modelId:model.value}));remember();});const presence=el('button',member.active?'让角色离场':'让角色重新加入');presence.disabled=locked;presence.onclick=()=>run(async()=>{state=await api(`/api/groups/${state.id}/${member.active?'leave':'rejoin'}`,envelope({memberId:member.id}));remember();});row.append(model,presence);}$('member-list').append(row);
  }
  $('join-panel').hidden=state.members.filter(m=>m.kind==='character').length>=3;options($('join-card'),cardOptions(),boot.cards.length?'0':'');options($('join-model'),modelOptions());for(const id of ['join','join-card','join-model'])$(id).disabled=locked;
  const b=state.budget;$('budget').textContent=`剩余调用 ${Math.max(0,b.calls-b.usedCalls)} / ${b.calls} 次\n剩余可预留 US$${(Math.max(0,b.microUsd-b.usedMicroUsd)/1e6).toFixed(4)}\n已用及预留 US$${(b.usedMicroUsd/1e6).toFixed(4)} / US$${(b.microUsd/1e6).toFixed(2)}`;
  $('request-panel').replaceChildren();const recent=state.requests.slice(-3);if(recent.length){const attention=unresolved||recent.at(-1).status==='failed';const container=attention?$('request-panel'):el('details');if(!attention){container.append(el('summary',`最近请求 · ${labels[recent.at(-1).status]}`));container.className='request';$('request-panel').append(container);}
   for(const request of recent){const row=el('section');row.className='request';row.dataset.pending=String(['prepared','dispatching','unknown'].includes(request.status));row.append(el('p',`${display(state.members.find(m=>m.id===request.targetId))}：${labels[request.status]}`),el('small',`请求 ${request.id.slice(-10)}`));if(request.partial)row.append(el('small','未完成内容，不计入聊天历史'),el('pre',request.partial));
    if(request.canRecover){const button=el('button','核对原请求结果');button.disabled=busy;button.onclick=()=>run(async()=>{state=await api(`/api/groups/${state.id}/recover`,{requestId:request.id});remember();});row.append(button);}
    if(request.canClose){const button=el('button','关闭尚未派发的请求');button.disabled=busy;button.onclick=()=>run(async()=>{state=await api(`/api/groups/${state.id}/close-prepared`,envelope({requestId:request.id}));remember();});row.append(button);}container.append(row);
   }
  }
 }
 function renderList(){
  const query=$('search').value.trim().toLocaleLowerCase();$('group-list').replaceChildren();const groups=boot.groups.filter(g=>(g.scene+' '+characterNames(g)).toLocaleLowerCase().includes(query));
  for(const group of groups){const button=el('button');button.className='group-item';const text=el('span');text.append(el('strong',group.scene.slice(0,60)),el('small',`${characterNames(group)} · ${group.messages.length} 条消息 · ${group.id.slice(-6)}`));button.append(text,el('em',pending(group)?'有请求待核对':'继续聊天'));button.disabled=busy;button.onclick=()=>openGroup(group.id);$('group-list').append(button);}
  if(!groups.length)$('group-list').append(el('p',query?'没有匹配的群聊，试试角色名称或清空搜索。':'还没有群聊。点击“创建群聊”，选择两位角色和相遇的场景。'));
 }
 function saveDraft(){if(state)drafts.set(state.id,$('input').value);if(screen==='setup')saveSetup();}
 async function openGroup(id){saveDraft();await run(async()=>{state=await api('/api/groups/'+id);$('input').value=drafts.get(id)??'';remember();show('chat');});if(screen==='chat')$('chat-title').focus();}
 async function newGroup(preferred=null){if(busy)return;saveDraft();await run(async()=>{catalog(await api('/api/bootstrap'));state=null;history.replaceState(null,'','/groups?create=1');show('setup');restoreSetup(preferred);if(preferred&&!boot.cards.some(c=>cardKey(c)===preferred))notice(Error('指定的角色版本不在当前目录中，请重新选择已封存版本。'));$('setup-title').focus();});}
 function goHome(){if(busy)return;saveDraft();state=null;history.replaceState(null,'','/groups');show('home');renderList();$('library-title').focus();}
 $('create-form').onsubmit=event=>{event.preventDefault();run(async()=>{const members=Array.from({length:slots},(_,i)=>({reference:boot.cards[Number($('card-'+i).value)].reference,modelId:$('model-'+i).value}));state=await api('/api/groups',{operationId:crypto.randomUUID(),expectedRevision:null,payload:{player:{id:null,name:$('player').value},scene:$('scene').value,members,opening:$('opening').value?JSON.parse($('opening').value):null,budget:{calls:Number($('calls').value),microUsd:Math.round(Number($('money').value)*1e6)}}});$('input').value='';setupDirty=false;try{window.sessionStorage.removeItem(setupKey);}catch{}remember();show('chat');$('chat-title').focus();});};
 async function send(input){const caret=[$('input').selectionStart,$('input').selectionEnd];await run(async()=>{state=await api(`/api/groups/${state.id}/send`,envelope({targetId:$('target').value,input}));if(input!==null){$('input').value='';drafts.delete(state.id);}remember();});if(!$('input').disabled){$('input').focus();if(input===null)$('input').setSelectionRange(...caret);}}
 $('send-form').onsubmit=event=>{event.preventDefault();if($('input').value.trim())send($('input').value);};$('continue').onclick=()=>send(null);
 $('target').onchange=composer;$('input').oninput=composer;
 $('refresh').onclick=()=>run(async()=>{const selected=choices();catalog(await api('/api/bootstrap'));if(state){state=await api('/api/groups/'+state.id);remember();}else if(screen==='setup'){renderSlots(selected);saveSetup();}else renderList();});
 $('new').onclick=()=>newGroup();$('start').onclick=()=>newGroup();$('home').onclick=goHome;$('cancel-create').onclick=goHome;
 $('add-slot').onclick=()=>{const selected=choices();slots=slots===2?3:2;renderSlots(selected);lock();setupDirty=true;saveSetup();};$('batch-model').onchange=()=>{for(let i=0;i<slots;i++)$('model-'+i).value=$('batch-model').value;};$('opening').onchange=openingPreview;$('search').oninput=renderList;
 $('history').onchange=()=>openGroup($('history').value);
 $('join').onclick=()=>run(async()=>{state=await api(`/api/groups/${state.id}/join`,envelope({reference:boot.cards[Number($('join-card').value)].reference,modelId:$('join-model').value}));remember();});
 $('toggle-members').onclick=()=>{const dialog=$('member-dialog');dialog.append($('members'));dialog.showModal();};$('close-members').onclick=closeMembers;$('member-dialog').addEventListener('close',()=>{$('member-dock').append($('members'));$('toggle-members').focus();});
 window.addEventListener('resize',()=>{if(window.innerWidth>820&&$('member-dialog').open)closeMembers();});
 for(const type of ['input','change'])$('create-form').addEventListener(type,()=>{setupDirty=true;saveSetup();});
 $('character-library').onclick=event=>{if(busy)event.preventDefault();};
 window.addEventListener('beforeunload',event=>{if($('input').value.trim()||(screen==='setup'&&setupDirty)){event.preventDefault();event.returnValue='';}});
 try{catalog(await api('/api/bootstrap'));$('mode').textContent=boot.mode==='fixture'?'离线演示 · 确定性回复，不调用模型':boot.mode==='disabled'?'发送未开放 · 可查看和创建':'受控模型 · 发送受共享授权限制';options($('batch-model'),modelOptions());renderSlots();renderList();lock();if(!boot.cards.length||!boot.models.length)notice(Error('尚无可用的角色版本或模型目录，请先由本地宿主载入。'));const id=new URL(location.href).searchParams.get('id');if(id){state=await api('/api/groups/'+id);remember();render();}else {const params=new URL(location.href).searchParams;if(params.get('create')==='1'){const preferred=params.get('work')&&params.get('version')?params.get('work')+'/'+params.get('version'):null;show('setup');restoreSetup(preferred);history.replaceState(null,'','/groups?create=1');lock();if(preferred&&!boot.cards.some(c=>cardKey(c)===preferred))notice(Error('指定的角色版本不在当前目录中，请重新选择已封存版本。'));}else show('home');}}catch(error){notice(error);}
}
if(typeof window!=='undefined'&&window.document)await mountGroupUI({document,window,fetch:window.fetch.bind(window),location:window.location,history:window.history,crypto:window.crypto});
