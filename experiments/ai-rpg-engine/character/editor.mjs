export function readBook(raw){
 const book=raw.trim()?JSON.parse(raw):{scan_depth:4,token_budget:1024,recursive_scanning:false,extensions:{},entries:[]};
 if(!book||typeof book!=='object'||!Array.isArray(book.entries)||book.entries.some(e=>!e||typeof e!=='object'||Array.isArray(e)||!Array.isArray(e.keys)||e.keys.some(k=>typeof k!=='string')||typeof e.content!=='string'||typeof e.enabled!=='boolean'))throw Error('请检查角色知识的高级数据。');return book;
}
export function updateBookEntry(raw,index,field,value){
 const book=readBook(raw),entry=book.entries[index];if(!entry||!['keys','content','enabled'].includes(field))throw Error('知识条目不存在。');
 entry[field]=field==='keys'?value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean):value;return JSON.stringify(book,null,2);
}
export function appendBookEntry(raw){const book=readBook(raw);book.entries.push({keys:[],content:'',enabled:true,insertion_order:book.entries.length,extensions:{}});return JSON.stringify(book,null,2);}
export async function mountCharacterStudio({window,document,fetch,EventSource}){
 const {location,history,crypto,localStorage,URL,Blob,Option,Uint8Array}=window;
 const confirm=window.confirm.bind(window),atob=window.atob.bind(window),btoa=window.btoa.bind(window);
const $=s=>document.querySelector(s),api='/rpg-app/api/authoring',cp='/characters';
let work=null,doc=null,version=null,dirty=false,session=null,stream=null,importing=null,catalog=null,busy=false;
let groupsUrl=null;
let screen='library',activeSection='basics',worksList=[];
const sectionNames={basics:'基础与肖像',definition:'人物设定',openings:'开场与示例',book:'角色知识',prompts:'高级设置',metadata:'作者与分享'};
const uid=()=>crypto.randomUUID();
const error=e=>{const messages={CHARACTER_CHARX_UNSUPPORTED:'暂不支持 CHARX / ZIP，请选择 JSON 或 PNG 角色卡。',CHARACTER_FIELD_TYPE:'角色字段类型不正确，请检查标出的字段。',CHARACTER_PNG_CRC:'PNG 数据损坏（校验和不匹配）。',CHARACTER_FILE_SIZE:'文件超过角色卡大小上限。',CHARACTER_METADATA_MISSING:'此 PNG 没有角色卡数据，可使用“上传肖像”。',CHARACTER_METADATA_SELECTION_REQUIRED:'角色数据需要明确选择版本。',CHARACTER_REVISION_CONFLICT:'另一页面已更新此作品或会话。当前输入保留，请刷新核对。',AUTHORING_REVISION_CONFLICT:'作品修订发生冲突，当前输入保留。请先导出或复制未保存内容，再刷新。',CHARACTER_OPERATION_PENDING:'原请求结果尚未确认，请先核对原请求。',CHARACTER_MODEL_REQUIRED:'请先在会话设置中选择模型。',CHARACTER_CONTEXT_CAPACITY:'当前上下文超出容量。请减少历史回合或精简角色内容。'};$('#error').hidden=false;$('#error').textContent=(messages[e.code]||e.message||e.code||'操作失败')+(e.path?'\n字段：'+e.path:'');};
const notice=text=>{$('#notice').textContent=text;};
async function request(path,body){const r=await fetch(api+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','x-authoring-request':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});const v=await r.json();if(!r.ok)throw Object.assign(new Error(v.error?.message||v.message||v.error?.code||v.error||v.code||'请求失败'),v,v.error&&typeof v.error==='object'?v.error:{},{code:v.code||v.error?.code||v.error});return v;}
async function run(fn){if(busy)return;busy=true;readonly();$('#error').hidden=true;document.querySelectorAll('.top-actions button').forEach(b=>b.disabled=true);try{await fn();}catch(e){error(e);}finally{busy=false;document.querySelectorAll('.top-actions button').forEach(b=>b.disabled=false);readonly();}}
function text(tag,value){const n=document.createElement(tag);n.textContent=value;return n;}
function readonly(){
 const groupLink=$('#create-group');groupLink.hidden=!groupsUrl||!version||!work;if(!groupLink.hidden){const target=new URL(groupsUrl);target.searchParams.set('create','1');target.searchParams.set('work',work.id);target.searchParams.set('version',version);groupLink.href=target.href;}
 const locked=!!version;
 document.querySelectorAll('#editor-form input,#editor-form textarea').forEach(e=>e.readOnly=locked||busy);
 document.querySelectorAll('#book-entries input,#book-entries textarea,#book-entries button').forEach(e=>e.disabled=locked||busy);
 for(const id of ['save','publish','add-greeting','add-book','upload-portrait'])$('#'+id).disabled=locked||busy;
 $('#start-chat').disabled=!work||!version||busy;$('#make-version').hidden=!!version;$('#make-version').disabled=busy;$('#show-chat').disabled=busy||!session;$('#show-preview').disabled=busy;$('#back-edit').disabled=busy;const pending=session&&Object.values(session.operations).some(o=>['prepared','dispatching','unknown'].includes(o.state));$('#send').disabled=busy||!session||!!pending;$('#recover').disabled=busy;
 for(const id of ['new','import','to-library','library-new','library-import','header-library','works','versions'])if($('#'+id))$('#'+id).disabled=busy;
}
function setScreen(value){
 screen=value;document.body.dataset.screen=value;document.body.classList.toggle('character-chat',value==='chat');
 $('#character-library').hidden=value!=='library';
 $('.character-editor').hidden=value!=='edit';$('.character-preview').hidden=!['preview','chat'].includes(value);
 $('#preview-panel').hidden=value!=='preview';$('#chat-panel').hidden=value!=='chat';
 $('#header-library').hidden=value==='library';$('#show-preview').setAttribute('aria-pressed',String(value==='preview'));$('#show-chat').setAttribute('aria-pressed',String(value==='chat'));
 $('#back-edit').hidden=false;$('#back-edit').setAttribute('aria-pressed',String(value==='edit'));$('#title').textContent=value==='library'?'角色工作台':doc?.character.card.data.name||'新建角色';$('#chat-empty').hidden=!!session;
 selectSection(activeSection,false);setUrl();
}
function selectSection(id,focus=true){
 if(!sectionNames[id])return;activeSection=id;
 for(const section of document.querySelectorAll('.editor-section'))section.hidden=section.id!==id;
 for(const link of document.querySelectorAll('[data-section]'))link.setAttribute('aria-current',link.dataset.section===id?'page':'false');
 $('#section-select').value=id;if($('#section-label'))$('#section-label').textContent=sectionNames[id];
 if(focus){setScreen('edit');const heading=$('#'+id).querySelector('h1,h2');if(heading){heading.tabIndex=-1;heading.focus();}}
}
function profile(){
 const d=doc?.character.card.data;if(!d)return;
 $('#preview-name').textContent=d.name||'未命名角色';$('#preview-description').textContent=d.description||'写下人物描述，让角色的模样更清晰。';$('#preview-personality').textContent=d.personality||'';
 $('#portrait-placeholder').textContent=Array.from(d.name||'角')[0];$('#portrait-placeholder').hidden=!$('#portrait').hidden;
}
async function openWork(id,v=null){
 if(dirty&&!confirm('草稿有未保存的修改。切换角色或版本？浏览器备份会保留。'))return;
 stream?.close();session=null;await load(id,v);setScreen(v?'preview':'edit');
}
function renderLibrary(){
 const query=$('#library-search').value.trim().toLocaleLowerCase(),list=worksList.filter(w=>(w.name+' '+w.tags.join(' ')).toLocaleLowerCase().includes(query));
 const grid=$('#library-grid');grid.replaceChildren();$('#library-empty').hidden=!!list.length;
 $('#library-empty').textContent=query?'没有找到这个角色，试试其他名字。':'从一个名字开始，或导入已有的角色卡。';
 for(const w of list){
  const card=text('article','');card.className='character-card';
  const portrait=text('div',Array.from(w.name||'角')[0]);portrait.className='character-card-portrait';portrait.setAttribute('aria-hidden','true');
  const body=text('div','');body.className='character-card-body';const name=text('h2',w.name||'未命名角色'),description=text('p','');description.className='character-card-description';
  const badge=text('small',w.versions?`${w.versions} 个可用版本`:'草稿');badge.className='character-card-status';
  const actions=text('div','');actions.className='character-card-actions';const edit=text('button','继续编辑');edit.onclick=()=>run(()=>openWork(w.id));actions.append(edit);
  if(w.versions){const read=text('button','预览与对话');read.onclick=()=>run(async()=>{const entry=await request('/works/'+w.id);await openWork(w.id,entry.versions.at(-1).id);});actions.append(read);}
  body.append(badge,name,description,actions);card.append(portrait,body);grid.append(card);
  description.textContent=w.description||'人物故事，等你继续书写。';if(w.cover){const img=document.createElement('img');img.alt='';img.loading='lazy';img.src=api+'/works/'+w.id+'/assets/'+encodeURIComponent(w.cover);img.onerror=()=>{img.remove();portrait.textContent=Array.from(w.name||'角')[0];};portrait.replaceChildren(img);}
 }
}
function renderBook(){
 const container=$('#book-entries');container.replaceChildren();let book;
 try{book=readBook($('#book-json').value);}catch{container.append(text('p','知识数据暂时无法读取。请检查下方的高级数据，原文已保留。'));return;}
 if(!book.entries.length)container.append(text('p','补充人物熟悉的地点、事物或经历，在对话提及时作为参考。'));
 book.entries.forEach((entry,index)=>{
  const row=text('section','');row.className='knowledge-entry';const heading=text('h3',entry.name||`知识条目 ${index+1}`);
  const field=(label,value,kind,change)=>{const wrapper=text('label',label),input=document.createElement(kind);input.value=value;if(kind==='textarea')input.rows=kind==='textarea'&&label==='关键词（每行一个）'?2:4;input.oninput=()=>{$('#book-json').value=updateBookEntry($('#book-json').value,index,change,input.value);changed();};wrapper.append(input);return wrapper;};
  const keys=field('关键词（每行一个）',(entry.keys||[]).join('\n'),'textarea','keys'),content=field('知识内容',entry.content||'','textarea','content');
  const enabled=text('label','');enabled.className='check';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=entry.enabled;checkbox.onchange=()=>{$('#book-json').value=updateBookEntry($('#book-json').value,index,'enabled',checkbox.checked);changed();};enabled.append(checkbox,text('span','启用此条目'));
  const remove=text('button','删除条目');remove.type='button';remove.onclick=()=>{if(!confirm('删除这条知识？保存草稿后生效。'))return;const current=readBook($('#book-json').value);current.entries.splice(index,1);$('#book-json').value=JSON.stringify(current,null,2);changed();renderBook();readonly();};
  row.append(heading,keys,content,enabled);
  if(entry.use_regex||/^\s*@@/m.test(entry.content))row.append(text('p','此条目包含暂未支持的匹配方式，保留内容，但不会用于对话。'));
  row.append(remove);container.append(row);
 });readonly();
}

function key(){return 'rpg-character-draft:'+location.origin+':'+(work?.id||'new');}
function changed(){dirty=true;$('#save-state').textContent='未保存';profile();try{localStorage.setItem(key(),JSON.stringify({revision:work?.revision??null,document:doc,book:$('#book-json').value,tags:$('#tags-json').value}));}catch{notice('浏览器草稿备份不可用，请及时保存到本地作品库。');}}
function collect(){const next=structuredClone(doc);let tags;try{tags=JSON.parse($('#tags-json').value||'[]');if(!Array.isArray(tags)||tags.some(t=>typeof t!=='string'))throw Error();}catch{throw Object.assign(new Error('标签格式不正确，请检查“作者与标签”中的高级数据。'),{path:'tags'});}next.character.card.data.tags=tags;const book=$('#book-json').value.trim();if(book){try{next.character.card.data.character_book=readBook(book);}catch{throw Object.assign(new Error('知识条目格式不正确，请检查“知识条目”中的高级数据。'),{path:'character_book'});}}else delete next.character.card.data.character_book;next.metadata.name=next.character.card.data.name.slice(0,120);return next;}
function setUrl(){const url=new URL(location.href);url.search='';url.hash='';if(screen!=='library'){if(work)url.searchParams.set('work',work.id);if(version)url.searchParams.set('version',version);if(session&&screen==='chat')url.searchParams.set('session',session.id);if(screen==='preview')url.searchParams.set('view','preview');if(screen==='edit')url.searchParams.set('section',activeSection);}history.replaceState(null,'',url);}
async function refreshWorks(){const listed=await request(cp+'/works');worksList=await Promise.all(listed.map(async w=>{try{const entry=await request('/works/'+w.id),d=entry.draft.character.card.data;return {...w,tags:d.tags,description:d.description,cover:entry.draft.settings.media.cover};}catch{return {...w,description:'详情暂时未能读取，仍可打开角色。'};}}));$('#works').replaceChildren(new Option('选择角色',''),...worksList.map(w=>new Option(w.name||'未命名角色',w.id)));$('#works').value=work?.id||'';renderLibrary();}
function render(){
 const d=doc.character.card.data;$('#title').textContent=d.name||'新建单角色卡';$('#save-state').textContent=version?'可用版本 · 只读':work?'草稿已保存':'新角色 · 未保存';
 for(const e of document.querySelectorAll('[data-field]'))e.value=d[e.dataset.field];
 $('#tags-json').value=JSON.stringify(d.tags,null,2);$('#tag-input').value=d.tags.join('，');$('#book-json').value=d.character_book?JSON.stringify(d.character_book,null,2):'';
 $('#raw-card').textContent=JSON.stringify(doc.character.card,null,2);
 $('#alternates').replaceChildren();d.alternate_greetings.forEach((value,i)=>{const div=text('div','');div.className='greeting-row';const label=text('label','候选开场白 '+(i+1)),input=document.createElement('textarea');input.rows=3;input.value=value;input.readOnly=!!version;input.addEventListener('input',()=>{d.alternate_greetings[i]=input.value;changed();});label.append(input);div.append(label);if(!version){const remove=text('button','删除此候选');remove.type='button';remove.onclick=()=>run(async()=>{doc=collect();doc.character.card.data.alternate_greetings.splice(i,1);render();changed();});div.append(remove);}$('#alternates').append(div);});
 $('#versions').replaceChildren(new Option('当前草稿',''),...(work?.versions||[]).map(v=>new Option('可用版本 '+v.number,v.id)));$('#versions').value=version||'';
 $('#greeting-index').replaceChildren(...[d.first_mes,...d.alternate_greetings].map((v,i)=>new Option((i?'候选 '+i:'主开场')+' · '+v.slice(0,25),String(i))));
 $('#version-note').textContent=version?'对话使用这个版本，后续编辑不会改变已有对话。':'保存草稿，再创建可用版本，即可开始对话。';
 const alias=doc.settings.media.cover;$('#portrait').hidden=!alias;if(alias&&work)$('#portrait').src=api+'/works/'+work.id+'/assets/'+alias+(version?'?version='+version:'');
 $('#original').hidden=!doc.character.source||!work;if(doc.character.source&&work)$('#original').href=api+cp+'/source?work='+work.id+(version?'&version='+version:'');
 profile();renderBook();readonly();selectSection(activeSection,false);
}
function restoreDraft(revision){try{const draft=JSON.parse(localStorage.getItem(key())||'null');if(draft?.revision===revision){doc=draft.document;render();$('#book-json').value=draft.book;$('#tags-json').value=draft.tags;try{$('#tag-input').value=JSON.parse(draft.tags).join('，');}catch{}renderBook();dirty=true;$('#save-state').textContent='未保存';notice('已恢复此修订对应的浏览器未保存草稿。');}else if(draft)notice('发现不同修订的浏览器草稿，已保留备份，未覆盖作品。');}catch{}}
async function load(id,v=null){const w=await request('/works/'+id);if(w.draft.kind!=='character')throw new Error('此作品是世界卡，请从世界卡工作台打开。');work=w;version=v;doc=v?(await request('/works/'+id+'/published?version='+v)).draft:structuredClone(w.draft);dirty=false;render();
 if(!v)restoreDraft(w.revision);
 $('#works').value=id;setUrl();await preview();
}
async function save(){doc=collect();const priorKey=key();let result;if(!work)result=await request('/works',{operationId:uid(),expectedRevision:null,document:doc});else result=await request('/works/'+work.id+'/save',{operationId:uid(),expectedRevision:work.revision,document:doc});
 localStorage.removeItem(priorKey);await load(result.workId||work.id);await refreshWorks();notice('草稿已保存。');}
async function preview(){if(!doc)return;const value=await request(cp+'/preview',{document:collect(),player:$('#player-name').value||'玩家',openingIndex:Number($('#greeting-index').value||0),input:$('#preview-input').value});
 profile();
 const bookStatus={triggered:'会用于这段对话','not-triggered':'未提及关键词',disabled:'未启用','unsupported-behavior':'暂不支持此触发方式','budget-excluded':'超出本次容量','recursion-limit':'已到递归上限'};
 const hits=value.assembly.trace?.book||[],entries=collect().character.card.data.character_book?.entries||[];
 $('#knowledge-preview').replaceChildren(...(hits.length?hits.map(item=>text('p',(entries[item.index]?.name||`知识条目 ${item.index+1}`)+' · '+(bookStatus[item.status]||item.status))):[text('p','还没有知识条目，可在编辑页添加。')]));
 $('#greeting-preview').textContent=value.assembly.messages.find(m=>m.role==='assistant')?.content||'';$('#context-preview').textContent=JSON.stringify(value.assembly,null,2);
 $('#compatibility').replaceChildren(text('h3',value.report.format==='chara_card_v3'?'格式说明 · V3':'格式说明 · V2'),...value.report.warnings.map(w=>{const p=text('p',w.message);p.className='warning';return p;}));$('#raw-card').textContent=JSON.stringify(collect().character.card,null,2);
}
function download(base64,type,name){const bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function asBase64(file){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(binary);}
async function inspect(){try{const result=await request(cp+'/imports/inspect',{base64:importing.base64,selection:importing.selection});importing.inspection=result;$('#import-report').textContent=[result.card.data.name,`角色卡格式：${result.report.version}`,result.report.warnings.length?'需要留意：':'常用角色字段可以读取。',...result.report.warnings.map(w=>'• '+w.message)].join('\n');$('#confirm-import').disabled=false;$('#fallback').hidden=true;}catch(e){$('#import-report').textContent=JSON.stringify(e.details||{error:e.code},null,2);$('#confirm-import').disabled=true;$('#fallback').hidden=e.code!=='CHARACTER_METADATA_SELECTION_REQUIRED';}}
function showChat(value=true){setScreen(value?'chat':'edit');}
function displaySession(){if(!session)return;const box=$('#messages'),follow=box.scrollHeight-box.scrollTop-box.clientHeight<80;$('#messages').replaceChildren(...session.messages.map(m=>{const p=text('div','');p.className='message '+m.role;p.append(text('strong',m.role==='assistant'?doc?.character.card.data.name||'角色':session.binding.player.name),text('span',m.content));return p;}));
 const latest=Object.values(session.operations).at(-1);if(latest&&['prepared','dispatching','unknown','failed'].includes(latest.state)){const p=text('div','');p.className='message user';p.append(text('strong',latest.state==='failed'?'上次未完成的输入':'待确认的输入'),text('span',latest.input));$('#messages').append(p);}
 const pending=Object.entries(session.operations).find(([,o])=>['prepared','dispatching','unknown'].includes(o.state));
 $('#send').disabled=!!pending;$('#recover').hidden=!pending||!pending[1].canRecover;$('#recover').dataset.operation=pending?.[0]||'';$('#operation-status').textContent=pending?(pending[1].canRecover?'原请求结果待核对；不会自动重发。':'正在等待回复…'):latest?.state==='failed'?'上次请求未形成完整回复（'+latest.error+'）。输入保留在上方，不会自动重发。':'已保存 '+session.turns+' 回合';
 $('#partial').hidden=!pending?.[1].partial;$('#partial').textContent=pending?.[1].partial||'';
 $('#memory-enabled').checked=session.settings.memoryEnabled;$('#history-turns').value=session.settings.historyTurns??'';
 if(follow)box.scrollTop=box.scrollHeight;$('#memory-list').replaceChildren(...session.memories.filter(m=>!m.deleted).map(m=>text('p',m.value.name+'：'+m.value.content)));setUrl();
}
async function reloadSession(){if(session){session=await request(cp+'/sessions/'+session.id);displaySession();}}
async function attachSession(s){notice('');stream?.close();session=s;showChat();displaySession();
 const status=await request(cp+'/status');$('#mode-note').textContent=status.mode==='offline-fixture'?'演示对话 · 使用固定示例回复，不调用模型。':'回复完成后显示，发送会使用已授权的模型额度。';
 catalog=await request(cp+'/models');$('#model').replaceChildren(new Option('保留当前模型',''),...(catalog.models||[]).filter(m=>m.available).map(m=>new Option(m.name+' · '+m.modelId,m.selectionId)));
 stream=new EventSource(api+cp+'/sessions/'+session.id+'/events');stream.onmessage=e=>{const event=JSON.parse(e.data);if(event.type==='delta'){$('#partial').hidden=false;$('#partial').textContent+=event.text;}else if(event.type==='settled')reloadSession().catch(error);else if(event.type==='snapshot'){session=event.session;displaySession();}};
 stream.onerror=()=>notice('会话连接暂时中断，正在重新读取；不会重新发送消息。');
 try{$('#message').value=localStorage.getItem('rpg-character-message:'+session.id)||'';}catch{}
}
document.querySelectorAll('[data-field]').forEach(e=>e.addEventListener('input',()=>{doc.character.card.data[e.dataset.field]=e.value;doc.metadata.name=doc.character.card.data.name.slice(0,120);changed();}));
for(const id of ['tags-json','book-json'])$('#'+id).oninput=changed;
$('#book-json').onchange=renderBook;
$('#tags-json').onchange=()=>{try{const tags=JSON.parse($('#tags-json').value);if(!Array.isArray(tags)||tags.some(t=>typeof t!=='string'))throw Error();$('#tag-input').value=tags.join('，');}catch{notice('标签需要是文字列表，请检查高级数据。');}};
$('#tag-input').oninput=()=>{$('#tags-json').value=JSON.stringify($('#tag-input').value.split(/[,，]/).map(t=>t.trim()).filter(Boolean),null,2);changed();};
$('#editor-form').onsubmit=e=>e.preventDefault();$('#save').onclick=()=>run(save);
$('#publish').onclick=()=>run(async()=>{if(dirty||!work)await save();const result=await request('/works/'+work.id+'/publish',{operationId:uid(),expectedRevision:work.revision});await load(work.id,result.result.versionId);notice(groupsUrl?'可用版本已创建。可以开始单聊，或用此版本创建群聊。旧对话继续使用原版本。':'可用版本已创建。你可以预览开场或开始对话。');await refreshWorks();setScreen('preview');});
$('#make-version').onclick=()=>$('#publish').click();
$('#new').onclick=()=>run(async()=>{if(dirty&&!confirm('当前草稿尚未保存，是否新建角色？浏览器备份会保留。'))return;stream?.close();work=null;version=null;session=null;doc=await request(cp+'/blank');dirty=false;activeSection='basics';render();setUrl();showChat(false);notice('先给角色一个名字，再慢慢写下他的故事。');try{if(localStorage.getItem(key())&&confirm('发现尚未保存的新角色草稿，是否恢复？'))restoreDraft(null);}catch{}});
$('#works').onchange=()=>run(async()=>{if(dirty&&!confirm('当前修改尚未保存，是否切换？浏览器备份会保留。')){$('#works').value=work?.id||'';return;}if($('#works').value){stream?.close();session=null;await load($('#works').value);}showChat(false);});$('#versions').onchange=()=>run(async()=>{if(dirty&&!confirm('当前草稿尚未保存，是否切换版本？浏览器备份会保留。')){$('#versions').value=version||'';return;}stream?.close();session=null;showChat(false);await load(work.id,$('#versions').value||null);});
$('#add-greeting').onclick=()=>run(async()=>{doc=collect();doc.character.card.data.alternate_greetings.push('');render();changed();});
$('#add-book').onclick=()=>run(async()=>{$('#book-json').value=appendBookEntry($('#book-json').value);changed();renderBook();});
$('#preview').onclick=()=>run(preview);$('#greeting-index').onchange=()=>run(preview);$('#player-name').onchange=()=>run(preview);
$('#show-preview').onclick=()=>run(async()=>{await preview();setScreen('preview');});$('#show-chat').onclick=()=>showChat(true);
$('#start-chat').onclick=()=>run(async()=>{const s=await request(cp+'/sessions',{operationId:uid(),reference:{workId:work.id,versionId:version},openingIndex:Number($('#greeting-index').value),player:{id:null,name:$('#player-name').value||'玩家'}});await attachSession(s);});
$('#refresh-chat').onclick=()=>run(reloadSession);
$('#send').onclick=()=>run(async()=>{if(!session)throw new Error('请先选择可用版本并开始对话。');const input=$('#message').value;if(!input.trim())return;
 const intent={operationId:uid(),expectedRevision:session.revision,input};localStorage.setItem('rpg-character-request:'+session.id,JSON.stringify(intent));$('#partial').textContent='';
 session=await request(cp+'/sessions/'+session.id+'/send',intent);$('#message').value='';localStorage.removeItem('rpg-character-message:'+session.id);localStorage.removeItem('rpg-character-request:'+session.id);displaySession();});
$('#message').oninput=()=>{if(session)try{localStorage.setItem('rpg-character-message:'+session.id,$('#message').value);}catch{}};
$('#recover').onclick=()=>run(async()=>{session=await request(cp+'/sessions/'+session.id+'/recover',{operationId:$('#recover').dataset.operation});displaySession();});
$('#save-settings').onclick=()=>run(async()=>{if(!session)return;let selection=null;const e=catalog?.models.find(m=>m.selectionId===$('#model').value);if(e)selection={catalogRevision:catalog.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,parameters:{maxOutputTokens:Number($('#max-output').value)}};
 session=await request(cp+'/sessions/'+session.id+'/settings',{expectedRevision:session.revision,settings:{memoryEnabled:$('#memory-enabled').checked,historyTurns:$('#history-turns').value?Number($('#history-turns').value):null},selection});displaySession();notice('会话设置已保存。');});
$('#add-memory').onclick=()=>run(async()=>{if(!session)return;session=await request(cp+'/sessions/'+session.id+'/memory',{expectedRevision:session.revision,operationId:uid(),change:{action:'create',value:{name:$('#memory-name').value,content:$('#memory-content').value,enabled:true,match:'any',roles:['user','assistant'],keywords:$('#memory-keys').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)}}});displaySession();notice('记忆仅保存在当前会话。');});
$('#import').onclick=()=>$('#card-file').click();$('#card-file').onchange=()=>run(async()=>{const file=$('#card-file').files[0];if(!file)return;if(file.size>32*1024*1024)throw new Error('文件超过 32 MiB。');importing={fileName:file.name,base64:await asBase64(file),selection:null};$('#import-dialog').showModal();await inspect();});
$('#choose-v2').onclick=()=>run(async()=>{importing.selection='chara';await inspect();});
$('#close-import').onclick=()=>$('#import-dialog').close();
$('#confirm-import').onclick=()=>run(async()=>{const r=await request(cp+'/imports',{base64:importing.base64,selection:importing.selection,expectedHash:importing.inspection.source.sha256,operationId:uid(),fileName:importing.fileName,origin:$('#import-origin').value});$('#import-dialog').close();stream?.close();session=null;showChat(false);await refreshWorks();await load(r.workId);notice('角色已导入。可以继续编辑，原始文件也已保留。');});
$('#export').onclick=()=>run(async()=>{if(dirty||!work)throw new Error('请先保存草稿再导出。');const format=$('#export-format').value,body={workId:work.id,versionId:version,format:format==='v2-png'?'png':'json',version:format==='original'?'original':'v2',acceptLoss:false};let result=await request(cp+'/export',body);if(result.confirmationRequired){if(!confirm('降级将不再表达以下内容：\n'+result.losses.join('\n')+'\n原始材料不会改变。是否导出？'))return;result=await request(cp+'/export',{...body,acceptLoss:true});}download(result.base64,result.mediaType,'character.'+body.format);notice('角色卡已导出，不包含会话记录或私有记忆。');});
$('#upload-portrait').onclick=()=>$('#portrait-file').click();$('#portrait-file').onchange=()=>run(async()=>{const file=$('#portrait-file').files[0];if(!file)return;if(dirty||!work)await save();await request(cp+'/portrait',{workId:work.id,expectedRevision:work.revision,operationId:uid(),base64:await asBase64(file)});const updated=await request('/works/'+work.id);work=updated;doc=updated.draft;doc.settings.media.cover='portrait';await save();});
$('#header-library').onclick=$('#to-library').onclick=()=>{if(busy)return;setScreen('library');renderLibrary();notice('');};
$('#library-new').onclick=()=>$('#new').click();$('#library-import').onclick=()=>$('#import').click();$('#library-search').oninput=renderLibrary;
$('#back-edit').onclick=()=>setScreen('edit');
for(const link of document.querySelectorAll('[data-section]'))link.onclick=e=>{e.preventDefault();selectSection(link.dataset.section);};
$('#section-select').onchange=()=>selectSection($('#section-select').value);
$('#portrait').onerror=()=>{$('#portrait').hidden=true;profile();};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
for(const id of ['groups-navigation','mobile-groups','create-group'])$('#'+id).onclick=e=>{if(busy)e.preventDefault();};
await run(async()=>{const navigation=await request(cp+'/navigation').catch(e=>{if(e.code==='CHARACTER_ROUTE_NOT_FOUND')return {groupsUrl:null};throw e;});groupsUrl=navigation.groupsUrl;for(const id of ['groups-navigation','mobile-groups']){$('#'+id).hidden=!groupsUrl;if(groupsUrl)$('#'+id).href=groupsUrl;}await refreshWorks();const params=new URLSearchParams(location.search);if(sectionNames[params.get('section')])activeSection=params.get('section');if(params.get('session')){const s=await request(cp+'/sessions/'+params.get('session'));await load(s.binding.reference.workId,s.binding.reference.versionId);await attachSession(s);}
 else if(params.get('work')){await load(params.get('work'),params.get('version'));setScreen(params.get('version')||params.get('view')==='preview'?'preview':'edit');}else {doc=await request(cp+'/blank');render();setScreen(params.has('section')?'edit':params.get('view')==='preview'?'preview':'library');notice('');if(screen!=='library')restoreDraft(null);}});

return {close(){stream?.close();}};
}
if(typeof window!=='undefined'&&window.document)await mountCharacterStudio({window,document,fetch:window.fetch.bind(window),EventSource:window.EventSource});
