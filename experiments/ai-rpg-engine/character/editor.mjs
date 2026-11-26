// Optional loopback navigation; never grants execution or changes saved bindings.
const revisionNavigation = (()=>{try{const v=new URL(location.href).searchParams.get('revisions')||sessionStorage.getItem('character-revisions-url');if(v&&/^http:\/\/127\.0\.0\.1:[0-9]+\/revisions$/.test(v)){sessionStorage.setItem('character-revisions-url',v);return v;}}catch{}return null;})();
import {createMediaPlayer} from './presentation/player.mjs';
import {createLookPanel} from './presentation/panel.mjs';
import {exampleDialog} from './v3/examples.mjs';
import {metadataPanel,upgradeCard} from './v3/metadata.mjs';
import {mountBookEditor} from './book-editor.mjs';
import {renderRich,playerPicker} from './daily/client.mjs';
export function readBook(raw){
 const invalid=path=>{throw Object.assign(new Error('知识条目格式不正确，请检查角色书原始数据。'),{path:'/data/character_book'+path});};
 let book;
 try {book=raw.trim()?JSON.parse(raw):{scan_depth:4,token_budget:1024,recursive_scanning:false,extensions:{},entries:[]};}
 catch {invalid('');}
 if(!book||!Array.isArray(book.entries))invalid('');
 book.entries.forEach((entry,index)=>{
  const path='/entries/'+index;
  if(!entry||typeof entry!=='object'||Array.isArray(entry))invalid(path);
  for(const field of ['keys','secondary_keys']){
   if(field==='secondary_keys'&&entry[field]===undefined)continue;
   if(!Array.isArray(entry[field])||entry[field].some(value=>typeof value!=='string'))invalid(path+'/'+field);
  }
  if(typeof entry.content!=='string')invalid(path+'/content');
  if(typeof entry.enabled!=='boolean')invalid(path+'/enabled');
 });
 return book;
}
export function updateBookEntry(raw,index,field,value){
 const book=readBook(raw),entry=book.entries[index];if(!entry||!['keys','content','enabled'].includes(field))throw Error('知识条目不存在。');
 entry[field]=field==='keys'?value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean):value;return JSON.stringify(book,null,2);
}
export function appendBookEntry(raw,v3=false){const book=readBook(raw);book.entries.push({...(v3?{use_regex:false}:{}),keys:[],content:'',enabled:true,insertion_order:book.entries.length,extensions:{}});return JSON.stringify(book,null,2);}
export async function mountCharacterStudio({window,document,fetch,EventSource}){
 const {location,history,crypto,localStorage,URL,Blob,Option,Uint8Array}=window;
 const initialMessage=new URLSearchParams(location.search).get('message');
 const confirm=window.confirm.bind(window),atob=window.atob.bind(window),btoa=window.btoa.bind(window);
const $=s=>document.querySelector(s),api='/rpg-app/api/authoring',cp='/characters';
let work=null,doc=null,version=null,dirty=false,session=null,stream=null,importing=null,catalog=null,busy=false;
let packageView=null, mediaTarget=null;
let groupsUrl=null;let selectedPlayer=()=>({id:null,name:$('#player-name').value||'玩家'});
let screen='library',activeSection='basics',worksList=[];
const sectionNames={basics:'基础与肖像',definition:'人物设定',openings:'开场与示例',book:'角色知识',prompts:'高级设置',metadata:'作者与分享'};
const uid=()=>crypto.randomUUID();
const error=e=>{const messages={CHARACTER_PREVIEW_HISTORY_INVALID:'模拟历史最多 200 条，每条最多 16000 个字符。',CHARACTER_CHARX_UNSUPPORTED:'请先检查 CHARX 资源清单，再确认导入。',CHARACTER_FIELD_TYPE:'角色字段类型不正确，请检查标出的字段。',CHARACTER_PNG_CRC:'PNG 数据损坏（校验和不匹配）。',CHARACTER_FILE_SIZE:'文件超过角色卡大小上限。',CHARACTER_METADATA_MISSING:'此 PNG 没有角色卡数据，可使用“上传肖像”。',CHARACTER_METADATA_SELECTION_REQUIRED:'角色数据需要明确选择版本。',CHARACTER_REVISION_CONFLICT:'另一页面已更新此作品或会话。当前输入保留，请刷新核对。',AUTHORING_REVISION_CONFLICT:'作品修订发生冲突，当前输入保留。请先导出或复制未保存内容，再刷新。',CHARACTER_OPERATION_PENDING:'原请求结果尚未确认，请先核对原请求。',CHARACTER_MODEL_REQUIRED:'请先在会话设置中选择模型。',CHARACTER_CONTEXT_CAPACITY:'当前上下文超出容量。请减少历史回合或精简角色内容。'};$('#error').hidden=false;$('#error').textContent=(messages[e.code]||e.message||e.code||'操作失败')+(e.path?'\n字段：'+e.path:'');if(screen==='edit'&&e.path?.includes('character_book')){selectSection('book',false);bookPanel.locate(e.path);}};
const notice=text=>{$('#notice').textContent=text;};
async function request(path,body){const r=await fetch(api+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','x-authoring-request':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});const v=await r.json();if(!r.ok)throw Object.assign(new Error(v.error?.message||v.message||v.error?.code||v.error||v.code||'请求失败'),v,v.error&&typeof v.error==='object'?v.error:{},{code:v.code||v.error?.code||v.error});return v;}
async function run(fn){if(busy)return;busy=true;readonly();$('#error').hidden=true;document.querySelectorAll('.top-actions button').forEach(b=>b.disabled=true);try{await fn();}catch(e){error(e);}finally{busy=false;document.querySelectorAll('.top-actions button').forEach(b=>b.disabled=false);readonly();}}
function text(tag,value){const n=document.createElement(tag);n.textContent=value;return n;}
function readonly(){
 const groupLink=$('#create-group');groupLink.hidden=!groupsUrl||!version||!work;if(!groupLink.hidden){const target=new URL(groupsUrl);target.searchParams.set('create','1');target.searchParams.set('work',work.id);target.searchParams.set('version',version);groupLink.href=target.href;}
 const locked=!!version;
 document.querySelectorAll('[data-replace-media]').forEach(b=>b.disabled=locked||busy);
 const canExport=locked&&doc?.character?.card.spec==='chara_card_v3';
 $('#export-charx-nav').hidden=!canExport;$('#charx-version-downloads').hidden=!canExport;
 $('#export-charx-nav').disabled=busy||!canExport;$('#export-charx-preview').disabled=busy||!canExport;
 document.querySelectorAll('[data-v3-write]').forEach(e=>e.disabled=locked||busy);
 $('#upgrade-v3').disabled=locked||busy;
 document.querySelectorAll('#editor-form input,#editor-form textarea').forEach(e=>e.readOnly=locked||busy);
 document.querySelectorAll('[data-book-write]').forEach(e=>e.disabled=locked||busy);
 for(const id of ['book-filter','book-sample','book-history'])$('#'+id).readOnly=busy;
 for(const id of ['book-enable','book-disable'])$('#'+id).disabled=locked||busy||!$('#book-entries .knowledge-entry');
 $('#book-try').disabled=busy;
 for(const id of ['save','publish','add-greeting','add-book','upload-portrait'])$('#'+id).disabled=locked||busy;
 $('#assign-books').href='/characters?books=1'+(work?'&work='+work.id:'')+'#assign';
 $('#start-books').disabled=!work||!version||busy;$('#start-chat').disabled=!work||!version||busy;$('#make-version').hidden=!!version;$('#make-version').disabled=busy;$('#show-chat').disabled=busy||!session;$('#show-preview').disabled=busy;$('#back-edit').disabled=busy;const pending=session&&Object.values(session.operations).some(o=>['prepared','dispatching','unknown'].includes(o.state));$('#send').disabled=busy||!session||!!pending;$('#recover').disabled=busy;
 for(const id of ['new','import','to-library','library-new','library-import','header-library','works','versions'])if($('#'+id))$('#'+id).disabled=busy;
}
const appearanceApi=(path,body)=>request(path.slice(api.length),body);
const singleMedia=createMediaPlayer(document);
const versionLook=createLookPanel($('#version-look'),appearanceApi,createMediaPlayer(document)),singleLook=createLookPanel($('#single-look'),appearanceApi,singleMedia);
function setScreen(value){
 const look=api+cp+'/appearance/';
 versionLook(value==='preview' && work && version?look+'preview/'+work.id+'/'+version:'','',true);
 singleLook(value==='chat' && session?look+'single/'+session.id:'');
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
  description.textContent=w.description||'人物故事，等你继续书写。';if(w.cover||w.charxCover){const img=document.createElement('img');img.alt='';img.loading='lazy';img.src=w.cover?api+'/works/'+w.id+'/assets/'+encodeURIComponent(w.cover)+(w.coverHash?'?hash='+w.coverHash:''):packageImage(w.id,null,w.charxCover);img.onerror=()=>{img.remove();portrait.textContent=Array.from(w.name||'角')[0];};portrait.replaceChildren(img);}
 }
}
const examples=exampleDialog({dialog:$('#samples'),confirm,read:()=>doc.character.card.data.mes_example,write:value=>{doc.character.card.data.mes_example=value;$('[data-field="mes_example"]').value=value;changed();}});
$('#structure-examples').onclick=()=>examples.open(!!version||busy);
const bookPanel = mountBookEditor({
 document, confirm, isV3:()=>doc?.character.card.spec==='chara_card_v3', read:()=>readBook($('#book-json').value), locked:()=>!!version||busy, report:error,
 write(book){$('#book-json').value=JSON.stringify(book,null,2);changed();},
 async preview(input,history){
  const result=await request(cp+'/preview',{document:collect(),player:selectedPlayer(),openingIndex:Number($('#greeting-index').value||0),input,history});
  return result.assembly;
 }
});
function renderBook(){bookPanel.render();readonly();}

function key(){return 'rpg-character-draft:'+location.origin+':'+(work?.id||'new');}
function changed(){bookPanel.invalidate();dirty=true;$('#save-state').textContent='未保存';profile();try{localStorage.setItem(key(),JSON.stringify({revision:work?.revision??null,document:doc,book:$('#book-json').value,tags:$('#tags-json').value}));}catch{notice('浏览器草稿备份不可用，请及时保存到本地作品库。');}}
function collect(){bookPanel.checkFields();const next=structuredClone(doc);let tags;try{tags=JSON.parse($('#tags-json').value||'[]');if(!Array.isArray(tags)||tags.some(t=>typeof t!=='string'))throw Error();}catch{throw Object.assign(new Error('标签格式不正确，请检查“作者与标签”中的高级数据。'),{path:'tags'});}next.character.card.data.tags=tags;const book=$('#book-json').value.trim();if(book)next.character.card.data.character_book=readBook(book);else delete next.character.card.data.character_book;next.metadata.name=next.character.card.data.name.slice(0,120);return next;}
function setUrl(){const url=new URL(location.href);url.search='';url.hash='';if(screen!=='library'){if(work)url.searchParams.set('work',work.id);if(version)url.searchParams.set('version',version);if(session&&screen==='chat')url.searchParams.set('session',session.id);if(screen==='preview')url.searchParams.set('view','preview');if(screen==='edit')url.searchParams.set('section',activeSection);}history.replaceState(null,'',url);}
async function refreshWorks(){const listed=await request(cp+'/works');worksList=await Promise.all(listed.map(async w=>{try{const entry=await request('/works/'+w.id),d=entry.draft.character.card.data;return {...w,tags:d.tags,description:d.description,cover:entry.draft.settings.media.cover,coverHash:entry.assets?.find(a=>a.alias===entry.draft.settings.media.cover)?.sha256};}catch{return {...w,description:'详情暂时未能读取，仍可打开角色。'};}}));$('#works').replaceChildren(new Option('选择角色',''),...worksList.map(w=>new Option(w.name||'未命名角色',w.id)));$('#works').value=work?.id||'';renderLibrary();}
function packageImage(id,v,path,hash){
 const params=new URLSearchParams({work:id,path});if(hash)params.set('hash',hash);if(v)params.set('version',v);
 return api+cp+'/charx/image?'+params;
}
function renderPackage(){
 const info=work&&packageView;
 $('#charx-bound').hidden=!info;$('#charx-export-note').hidden=!info;
 const rows=info?.rows??[], uses=new Map();
 for(const row of rows)uses.set(row.path,(uses.get(row.path)??0)+1);
 const pending=work?.pending;
 $('#recover-media').hidden=!!version||!pending||pending.workId!==work?.id||!pending.operationId.startsWith('media-');
 $('#charx-bound-list').replaceChildren(...rows.map((row,index)=>{
  const li=document.createElement('li');
  if(row.status==='图片'){
   const img=document.createElement('img');img.loading='lazy';img.width=64;img.height=48;img.alt=row.name;img.src=packageImage(work.id,version,row.path,row.sha256);li.append(img);
  }
  li.append(text('span',[row.main?'主肖像':row.type,row.name,row.path,row.status,row.size?row.size+' B':''].filter(Boolean).join(' · ')));
  if(!version && ['音频','视频'].includes(row.status)){
   const count=uses.get(row.path);
   if(count>1)li.append(text('small','共 '+count+' 处引用，仅替换此项'));
   const button=text('button','替换'+row.status);button.type='button';button.dataset.replaceMedia=String(index);
   button.setAttribute('aria-label','替换'+row.status+'：'+row.name+'（第 '+(index+1)+' 项）');
   button.onclick=()=>{
    if(busy||dirty||examples.pending){notice('请先保存当前草稿，再替换文件。');return;}
    mediaTarget={workId:work.id,expectedRevision:work.revision,index,operationId:'media-'+uid()};
    const picker=$('#charx-media-file');picker.accept=row.status==='音频'?'.wav,.mp3':'.mp4';picker.value='';picker.click();
   };
   li.append(button);
  }
  return li;
 }));
 return info?.rows.find(row=>row.main&&row.status==='图片'&&!row.alias);
}
function render(){
 bookPanel.reset();
 $('#upgrade-v3').hidden=doc.character.card.spec==='chara_card_v3';metadataPanel($('#v3-fields'),doc.character.card,changed,!!version);
 const d=doc.character.card.data;$('#title').textContent=d.name||'新建单角色卡';$('#save-state').textContent=version?'可用版本 · 只读':work?'草稿已保存':'新角色 · 未保存';
 for(const e of document.querySelectorAll('[data-field]'))e.value=d[e.dataset.field];
 $('#tags-json').value=JSON.stringify(d.tags,null,2);$('#tag-input').value=d.tags.join('，');$('#book-json').value=d.character_book?JSON.stringify(d.character_book,null,2):'';
 $('#raw-card').textContent=JSON.stringify(doc.character.card,null,2);
 $('#alternates').replaceChildren();d.alternate_greetings.forEach((value,i)=>{const div=text('div','');div.className='greeting-row';const label=text('label','候选开场白 '+(i+1)),input=document.createElement('textarea');input.rows=3;input.value=value;input.readOnly=!!version;input.addEventListener('input',()=>{d.alternate_greetings[i]=input.value;changed();});label.append(input);div.append(label);if(!version){const remove=text('button','删除此候选');remove.type='button';remove.onclick=()=>run(async()=>{doc=collect();doc.character.card.data.alternate_greetings.splice(i,1);render();changed();});div.append(remove);}$('#alternates').append(div);});
 $('#versions').replaceChildren(new Option('当前草稿',''),...(work?.versions||[]).map(v=>new Option('可用版本 '+v.number,v.id)));$('#versions').value=version||'';
 $('#greeting-index').replaceChildren(...[d.first_mes,...d.alternate_greetings].map((v,i)=>new Option((i?'候选 '+i:'主开场')+' · '+v.slice(0,25),String(i))));
 $('#version-note').textContent=version?'对话使用这个版本，后续编辑不会改变已有对话。':'保存草稿，再创建可用版本，即可开始对话。';
 const large=renderPackage(),alias=doc.settings.media.cover;
 const draftHash=!version&&work?.assets?.find(a=>a.alias===alias)?.sha256;
 $('#portrait').hidden=!alias&&!large;
 if(!alias&&large)$('#portrait').src=packageImage(work.id,version,large.path,large.sha256);
 if(alias&&work)$('#portrait').src=api+'/works/'+work.id+'/assets/'+alias+(version?'?version='+version:draftHash?'?hash='+draftHash:'');
 $('#original').hidden=!doc.character.source||!work;if(doc.character.source&&work)$('#original').href=api+cp+'/source?work='+work.id+(version?'&version='+version:'');
 $('#preview-original').hidden=$('#original').hidden;if(!$('#original').hidden)$('#preview-original').href=$('#original').href;
 profile();renderBook();readonly();selectSection(activeSection,false);
}
function restoreDraft(revision){try{const draft=JSON.parse(localStorage.getItem(key())||'null');if(draft?.revision===revision){doc=draft.document;render();$('#book-json').value=draft.book;$('#tags-json').value=draft.tags;try{$('#tag-input').value=JSON.parse(draft.tags).join('，');}catch{}renderBook();dirty=true;$('#save-state').textContent='未保存';notice('已恢复此修订对应的浏览器未保存草稿。');}else if(draft)notice('发现不同修订的浏览器草稿，已保留备份，未覆盖作品。');}catch{}}
async function load(id,v=null){if(work?.id!==id || version!==v){versionLook('');singleLook('');}const w=await request('/works/'+id);if(w.draft.kind!=='character')throw new Error('此作品是世界卡，请从世界卡工作台打开。');packageView=['copy','import'].includes(w.origin?.kind)?await request(cp+'/charx/resources?work='+id+(v?'&version='+v:'')):null;
 work=w;version=v;doc=v?(await request('/works/'+id+'/published?version='+v)).draft:structuredClone(w.draft);dirty=false;render();
 if(!v)restoreDraft(w.revision);
 $('#works').value=id;setUrl();await preview();
}
async function save(){doc=collect();const priorKey=key();let result;if(!work)result=await request('/works',{operationId:uid(),expectedRevision:null,document:doc});else result=await request('/works/'+work.id+'/save',{operationId:uid(),expectedRevision:work.revision,document:doc});
 localStorage.removeItem(priorKey);await load(result.workId||work.id);await refreshWorks();notice('草稿已保存。');}
async function preview(){if(!doc)return;const value=await request(cp+'/preview',{document:collect(),player:selectedPlayer(),openingIndex:Number($('#greeting-index').value||0),input:$('#preview-input').value});
 profile();
 const bookStatus={'regex-invalid':'表达式无效','regex-unsupported':'语法未支持','regex-empty':'可能空匹配','regex-limit':'规则超过上限','regex-request-limit':'本次正则步数已耗尽','regex-scan-limit':'可见文本超过上限',triggered:'会用于这段对话','not-triggered':'未提及关键词',disabled:'未启用','unsupported-behavior':'暂不支持此触发方式','budget-excluded':'超出本次容量','recursion-limit':'已到递归上限'};
 const hits=value.assembly.trace?.book||[],entries=collect().character.card.data.character_book?.entries||[];
 $('#knowledge-preview').replaceChildren(...(hits.length?hits.map(item=>text('p',(entries[item.index]?.name||`知识条目 ${item.index+1}`)+' · '+(bookStatus[item.status]||item.status))):[text('p','还没有知识条目，可在编辑页添加。')]));
 $('#greeting-preview').textContent=value.assembly.messages.find(m=>m.role==='assistant')?.content||'';$('#context-preview').textContent=JSON.stringify(value.assembly,null,2);
 $('#compatibility').replaceChildren(text('h3',value.report.format==='chara_card_v3'?'格式说明 · V3':'格式说明 · V2'),...value.report.warnings.map(w=>{const p=text('p',w.message);p.className='warning';return p;}));$('#raw-card').textContent=JSON.stringify(collect().character.card,null,2);
}
function download(base64,type,name){const bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('#charx-media-file').onchange=()=>run(async()=>{
 const file=$('#charx-media-file').files[0], target=mediaTarget;mediaTarget=null;
 if(!file||!target)return;
 if(version||dirty||examples.pending||work?.id!==target.workId||work.revision!==target.expectedRevision)throw Error('草稿已变化，请重新选择要替换的引用。');
 if(!file.size||file.size>16*1024*1024)throw Error('请选择不超过 16 MiB 的音视频文件。');
 const extension=file.name.split('.').pop().toLowerCase();
 try{
  await request(cp+'/charx/replace',{...target,extension,base64:await asBase64(file)});
 }catch(e){
  if(e.code==='CHARACTER_CHARX_REPLACEMENT_INVALID')throw Error(e.details?.diagnostic||'文件与所选引用不匹配。');
  if(e.code==='AUTHORING_RESULT_UNKNOWN')await load(target.workId);
  throw e;
 }
 await load(target.workId);notice('此项引用已替换并保存到草稿。创建版本后即可导出。');
});
$('#recover-media').onclick=()=>run(async()=>{
 if(dirty||examples.pending)throw Error('请先保留当前草稿修改。');
 const {workId,operationId}=work.pending;
 const result=await request('/operations/recover',{workId,operationId});await load(workId);
 notice(result.status==='committed'?'已核对此次替换已保存。':'已核对原请求未保存，可重新选择文件。');
});
async function asBase64(file){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(binary);}
function originalLink(hash,label){
 const a=document.createElement('a');a.href=api+cp+'/charx/original?hash='+encodeURIComponent(hash);a.download='original-character.charx';a.textContent=label;return a;
}
$('#charx-shelf').ontoggle=async()=>{
 if(!$('#charx-shelf').open)return;
 const list=$('#charx-originals');
 try {
  const rows=await request(cp+'/charx/originals');
  list.replaceChildren(...rows.map(row=>{const li=document.createElement('li');li.append(originalLink(row.hash,(row.name||'未命名角色')+' · 下载原包'));return li;}));
  if(!rows.length)list.textContent='还没有保留的原包。';
 }catch{list.textContent='无法读取原包列表。请重新打开重试。';}
};
function showPackage(result){
 const mixed=!['images','media'].includes(result.classification);
 const passed=result.classification==='media'?'音视频角色包检查通过':'图片包检查通过';
 $('#import-report').textContent=[result.name||'未命名角色',mixed?'仅可保留原包':passed,mixed?'以下项目需要保留在原包中。':'确认后可编辑角色并创建可用版本。',...result.diagnostics].join('\n');
 $('#charx-members').replaceChildren(...result.members.map(member=>{
  const li=document.createElement('li'), label=document.createElement('strong'), info=document.createElement('span');
  label.textContent=member.path;
  info.textContent=[member.status,member.size===null?'':member.size+' B',member.width?member.width+' × '+member.height:'',
    ...member.uses.map(use=>(use.main?'主肖像 · ':'')+use.type+' / '+use.name)].filter(Boolean).join(' · ');
  li.append(label,info);return li;
 }));
 $('#charx-resources').hidden=false;$('#keep-charx').hidden=false;$('#keep-charx').disabled=false;
 $('#confirm-import').textContent='确认导入角色';$('#confirm-import').hidden=mixed;$('#confirm-import').disabled=mixed;$('#import-origin-label').hidden=mixed;
}
async function inspect(){
 $('#import-report').textContent='正在检查文件…';$('#charx-resources').hidden=true;$('#charx-members').replaceChildren();
 $('#charx-feedback').replaceChildren();$('#keep-charx').hidden=true;$('#confirm-import').hidden=false;
 $('#confirm-import').textContent='确认导入为新角色';$('#confirm-import').disabled=true;$('#import-origin-label').hidden=false;$('#fallback').hidden=true;
 try{const result=await request(cp+'/imports/inspect',{base64:importing.base64,selection:importing.selection,...(/\.charx$/i.test(importing.fileName)?{formatHint:'charx'}:{})});importing.inspection=result;if(result.source.container==='charx'){showPackage(result);return;}$('#import-report').textContent=[result.card.data.name,`角色卡格式：${result.report.version}`,result.report.warnings.length?'需要留意：':'常用角色字段可以读取。',...result.report.warnings.map(w=>'• '+w.message)].join('\n');$('#confirm-import').disabled=false;$('#fallback').hidden=true;}catch(e){$('#import-report').textContent=e.code==='CHARACTER_CHARX_INVALID'?[e.details?.diagnostic||'无法读取 CHARX 文件',e.path].filter(Boolean).join(' · '):JSON.stringify(e.details||{error:e.code},null,2);$('#confirm-import').disabled=true;$('#fallback').hidden=e.code!=='CHARACTER_METADATA_SELECTION_REQUIRED';}}
function showChat(value=true){setScreen(value?'chat':'edit');}
function displaySession(){if(!session)return;const activeMessage=document.activeElement?.id?.startsWith('single-message-')?document.activeElement.id:null;const box=$('#messages'),follow=box.scrollHeight-box.scrollTop-box.clientHeight<80;$('#messages').replaceChildren(...session.messages.map(m=>{const p=text('div','');p.className='message '+m.role;p.id='single-message-'+session.messages.indexOf(m);p.append(text('strong',m.role==='assistant'?doc?.character.card.data.name||'角色':session.binding.player.name));renderRich(document,p,m);return p;}));
 const latest=Object.values(session.operations).at(-1);if(latest&&['prepared','dispatching','unknown','failed'].includes(latest.state)){const p=text('div','');p.className='message user';p.append(text('strong',latest.state==='failed'?'上次未完成的输入':'待确认的输入'),text('span',latest.input));$('#messages').append(p);}
 const pending=Object.entries(session.operations).find(([,o])=>['prepared','dispatching','unknown'].includes(o.state));
 $('#send').disabled=!!pending;$('#recover').hidden=!pending||!pending[1].canRecover;$('#recover').dataset.operation=pending?.[0]||'';$('#operation-status').textContent=pending?(pending[1].canRecover?'原请求结果待核对；不会自动重发。':'正在等待回复…'):latest?.state==='failed'?'上次请求未形成完整回复（'+latest.error+'）。输入保留在上方，不会自动重发。':'已保存 '+session.turns+' 回合';
 $('#partial').hidden=!pending?.[1].partial;$('#partial').textContent=pending?.[1].partial||'';
 $('#memory-enabled').checked=session.settings.memoryEnabled;$('#history-turns').value=session.settings.historyTurns??'';
 if(follow)box.scrollTop=box.scrollHeight;$('#memory-list').replaceChildren(...session.memories.filter(m=>!m.deleted).map(m=>text('p',m.value.name+'：'+m.value.content)));setUrl();if(activeMessage){const hit=document.getElementById(activeMessage);if(hit){hit.tabIndex=-1;hit.focus({preventScroll:true});}}
}
async function reloadSession(){if(session){session=await request(cp+'/sessions/'+session.id);displaySession();}}
async function attachSession(s){
 const avatar=$('#charx-chat-portrait'),ref=s.binding.reference;
 avatar.hidden=true;avatar.removeAttribute('src');
 if(packageView&&work?.id===ref.workId&&version===ref.versionId){
  const alias=doc.settings.media.cover,main=packageView.rows.find(row=>row.main&&row.status==='图片'&&!row.alias);
  if(alias||main){avatar.onerror=()=>{avatar.hidden=true;};avatar.src=alias?api+'/works/'+ref.workId+'/assets/'+encodeURIComponent(alias)+'?version='+ref.versionId:packageImage(ref.workId,ref.versionId,main.path);avatar.hidden=false;}
 }
 notice('');stream?.close();session=s;showChat();displaySession();
 const status=await request(cp+'/status');$('#mode-note').textContent=status.mode==='offline-fixture'?'演示对话 · 使用固定示例回复，不调用模型。':'回复完成后显示，发送会使用已授权的模型额度。';
 catalog=await request(cp+'/models');$('#model').replaceChildren(new Option('保留当前模型',''),...(catalog.models||[]).filter(m=>m.available).map(m=>new Option(m.name+' · '+m.modelId,m.selectionId)));
 stream=new EventSource(api+cp+'/sessions/'+session.id+'/events');stream.onmessage=e=>{const event=JSON.parse(e.data);if(event.type==='delta'){$('#partial').hidden=false;$('#partial').textContent+=event.text;}else if(event.type==='settled')reloadSession().catch(error);else if(event.type==='snapshot'){session=event.session;displaySession();}};
 stream.onerror=()=>notice('会话连接暂时中断，正在重新读取；不会重新发送消息。');
 try{$('#message').value=localStorage.getItem('rpg-character-message:'+session.id)||'';}catch{}
}
document.querySelectorAll('[data-field]').forEach(e=>e.addEventListener('input',()=>{doc.character.card.data[e.dataset.field]=e.value;doc.metadata.name=doc.character.card.data.name.slice(0,120);changed();}));
for(const id of ['tags-json','book-json'])$('#'+id).oninput=changed;
$('#book-json').onchange=()=>{bookPanel.invalidate();renderBook();};
$('#tags-json').onchange=()=>{try{const tags=JSON.parse($('#tags-json').value);if(!Array.isArray(tags)||tags.some(t=>typeof t!=='string'))throw Error();$('#tag-input').value=tags.join('，');}catch{notice('标签需要是文字列表，请检查高级数据。');}};
$('#tag-input').oninput=()=>{$('#tags-json').value=JSON.stringify($('#tag-input').value.split(/[,，]/).map(t=>t.trim()).filter(Boolean),null,2);changed();};
$('#editor-form').onsubmit=e=>e.preventDefault();$('#save').onclick=()=>run(save);
$('#publish').onclick=()=>run(async()=>{if(dirty||!work)await save();const result=await request('/works/'+work.id+'/publish',{operationId:uid(),expectedRevision:work.revision});await load(work.id,result.result.versionId);notice(groupsUrl?'可用版本已创建。可以开始单聊，或用此版本创建群聊。旧对话继续使用原版本。':'可用版本已创建。你可以预览开场或开始对话。');await refreshWorks();setScreen('preview');});
$('#make-version').onclick=()=>$('#publish').click();
$('#new').onclick=()=>run(async()=>{if(dirty&&!confirm('当前草稿尚未保存，是否新建角色？浏览器备份会保留。'))return;stream?.close();work=null;version=null;session=null;doc=await request(cp+'/blank');dirty=false;activeSection='basics';render();setUrl();showChat(false);notice('先给角色一个名字，再慢慢写下他的故事。');try{if(localStorage.getItem(key())&&confirm('发现尚未保存的新角色草稿，是否恢复？'))restoreDraft(null);}catch{}});
$('#works').onchange=()=>run(async()=>{if(dirty&&!confirm('当前修改尚未保存，是否切换？浏览器备份会保留。')){$('#works').value=work?.id||'';return;}if($('#works').value){stream?.close();session=null;await load($('#works').value);}showChat(false);});$('#versions').onchange=()=>run(async()=>{if(dirty&&!confirm('当前草稿尚未保存，是否切换版本？浏览器备份会保留。')){$('#versions').value=version||'';return;}stream?.close();session=null;showChat(false);await load(work.id,$('#versions').value||null);});
$('#add-greeting').onclick=()=>run(async()=>{doc=collect();doc.character.card.data.alternate_greetings.push('');render();changed();});
$('#add-book').onclick=()=>run(async()=>{bookPanel.checkFields();$('#book-filter').value='';$('#book-json').value=appendBookEntry($('#book-json').value,doc.character.card.spec==='chara_card_v3');changed();bookPanel.revealLast();});
$('#preview').onclick=()=>run(preview);$('#greeting-index').onchange=()=>run(preview);$('#player-name').onchange=()=>run(preview);
$('#show-preview').onclick=()=>run(async()=>{await preview();setScreen('preview');});$('#show-chat').onclick=()=>showChat(true);
$('#start-books').onclick=()=>run(async()=>{const s=await request(cp+'/sessions',{operationId:uid(),reference:{workId:work.id,versionId:version},openingIndex:Number($('#greeting-index').value),player:selectedPlayer()});location.assign('/characters?books=1&kind=single&source='+s.id+'#branches');});
$('#start-chat').onclick=()=>run(async()=>{const s=await request(cp+'/sessions',{operationId:uid(),reference:{workId:work.id,versionId:version},openingIndex:Number($('#greeting-index').value),player:selectedPlayer()});await attachSession(s);});
$('#refresh-chat').onclick=()=>{singleMedia.stop();return run(reloadSession);};
$('#send').onclick=()=>run(async()=>{if(!session)throw new Error('请先选择可用版本并开始对话。');const input=$('#message').value;if(!input.trim())return;
 const intent={operationId:uid(),expectedRevision:session.revision,input};localStorage.setItem('rpg-character-request:'+session.id,JSON.stringify(intent));$('#partial').textContent='';
 session=await request(cp+'/sessions/'+session.id+'/send',intent);$('#message').value='';localStorage.removeItem('rpg-character-message:'+session.id);localStorage.removeItem('rpg-character-request:'+session.id);displaySession();});
$('#message').oninput=()=>{if(session)try{localStorage.setItem('rpg-character-message:'+session.id,$('#message').value);}catch{}};
$('#recover').onclick=()=>run(async()=>{session=await request(cp+'/sessions/'+session.id+'/recover',{operationId:$('#recover').dataset.operation});displaySession();});
$('#save-settings').onclick=()=>run(async()=>{if(!session)return;let selection=null;const e=catalog?.models.find(m=>m.selectionId===$('#model').value);if(e)selection={catalogRevision:catalog.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,parameters:{maxOutputTokens:Number($('#max-output').value)}};
 session=await request(cp+'/sessions/'+session.id+'/settings',{expectedRevision:session.revision,settings:{memoryEnabled:$('#memory-enabled').checked,historyTurns:$('#history-turns').value?Number($('#history-turns').value):null},selection});displaySession();notice('会话设置已保存。');});
$('#add-memory').onclick=()=>run(async()=>{if(!session)return;session=await request(cp+'/sessions/'+session.id+'/memory',{expectedRevision:session.revision,operationId:uid(),change:{action:'create',value:{name:$('#memory-name').value,content:$('#memory-content').value,enabled:true,match:'any',roles:['user','assistant'],keywords:$('#memory-keys').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean)}}});displaySession();notice('记忆仅保存在当前会话。');});
$('#import').onclick=()=>$('#card-file').click();$('#card-file').onchange=()=>run(async()=>{const file=$('#card-file').files[0];if(!file)return;$('#card-file').value='';if(file.size>32*1024*1024)throw new Error('文件超过 32 MiB。');importing={operationId:uid(),fileName:file.name,base64:await asBase64(file),selection:null};$('#import-dialog').showModal();await inspect();});
$('#keep-charx').onclick=()=>run(async()=>{
 const button=$('#keep-charx'),feedback=$('#charx-feedback');button.disabled=true;
 try{
  const saved=await request(cp+'/charx/originals',{base64:importing.base64,expectedHash:importing.inspection.source.sha256,fileName:importing.fileName});
  feedback.replaceChildren('原包已保存在本地。',originalLink(saved.hash,'下载原始 CHARX'));
  $('#charx-shelf').open=false;
 }catch(e){feedback.textContent=e.details?.diagnostic||e.code||'原包保存失败';button.disabled=false;}
});
$('#choose-v2').onclick=()=>run(async()=>{importing.selection='chara';await inspect();});
$('#close-import').onclick=()=>$('#import-dialog').close();
$('#confirm-import').onclick=()=>run(async()=>{if(dirty&&!confirm('当前草稿有未保存的修改，是否打开导入的角色？'))return;const r=await request(cp+'/imports',{base64:importing.base64,selection:importing.selection,expectedHash:importing.inspection.source.sha256,operationId:importing.operationId,fileName:importing.fileName,origin:$('#import-origin').value});$('#import-dialog').close();stream?.close();session=null;showChat(false);await refreshWorks();await load(r.workId);setScreen('edit');notice('角色已导入。可以继续编辑，原始文件也已保留。');});
$('#upgrade-v3').onclick=()=>run(async()=>{
 if(work&&dirty)throw Error('请先保存当前草稿，再创建独立 V3 副本。');
 if(work){const c=await request('/works/'+work.id+'/copy',{operationId:uid(),expectedRevision:work.revision,name:work.name||doc.metadata.name||'V3 草稿'});await load(c.workId);}
 doc=collect();upgradeCard(doc.character.card);render();changed();if(work)await save();notice('V3 草稿已就绪。');
});
async function downloadVersion(){
 if(!work||!version||dirty)throw Error('请选择已封存的 V3 角色版本。');
 const number=work.versions.find(v=>v.id===version)?.number;
 const response=await fetch(api+cp+'/charx/export',{method:'POST',headers:{'Content-Type':'application/json','x-authoring-request':'1'},body:JSON.stringify({workId:work.id,versionId:version})});
 if(!response.ok){const result=await response.json();const err=result.error;throw Error([err?.details?.diagnostic||err?.code||'导出失败',err?.path].filter(Boolean).join(' · '));}
 const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');
 a.href=url;a.download='character-v'+number+'.charx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 notice('已导出可用版本 '+number+' 的角色包。');
}
for(const id of ['export-charx-nav','export-charx-preview'])$('#'+id).onclick=()=>run(downloadVersion);
$('#export').onclick=()=>run(async()=>{if(dirty||!work)throw new Error('请先保存草稿再导出。');const format=$('#export-format').value,body={workId:work.id,versionId:version,format:format.includes('png')||format==='v3-backfill'?'png':'json',version:format==='v3-png'?'v3':format==='v3-backfill'?format:format==='original'?'original':'v2',acceptLoss:false};let result=await request(cp+'/export',body);if(result.confirmationRequired){if(!confirm('请核对本次导出的差异：\n'+result.losses.join('\n')+'\n原始材料不会改变。是否导出？'))return;result=await request(cp+'/export',{...body,acceptLoss:true});}download(result.base64,result.mediaType,'character.'+body.format);notice('角色卡已导出，不包含会话记录或私有记忆。');});
$('#upload-portrait').onclick=()=>$('#portrait-file').click();$('#portrait-file').onchange=()=>run(async()=>{const file=$('#portrait-file').files[0];if(!file)return;if(dirty||!work)await save();await request(cp+'/portrait',{workId:work.id,expectedRevision:work.revision,operationId:uid(),base64:await asBase64(file)});const updated=await request('/works/'+work.id);work=updated;doc=updated.draft;doc.settings.media.cover='portrait';await save();});
$('#header-library').onclick=$('#to-library').onclick=()=>{if(busy)return;setScreen('library');renderLibrary();notice('');};
$('#library-new').onclick=()=>$('#new').click();$('#library-import').onclick=()=>$('#import').click();$('#library-search').oninput=renderLibrary;
$('#back-edit').onclick=()=>setScreen('edit');
for(const link of document.querySelectorAll('[data-section]'))link.onclick=e=>{e.preventDefault();selectSection(link.dataset.section);};
$('#section-select').onchange=()=>selectSection($('#section-select').value);
$('#portrait').onerror=()=>{$('#portrait').hidden=true;profile();};
window.addEventListener('beforeunload',e=>{if(dirty||examples.pending){e.preventDefault();e.returnValue='';}});
for(const id of ['groups-navigation','mobile-groups','create-group'])$('#'+id).onclick=e=>{if(busy)e.preventDefault();};
selectedPlayer=await playerPicker(document,path=>request(cp+'/daily'+path),'player-name');
await run(async()=>{const navigation=await request(cp+'/navigation').catch(e=>{if(e.code==='CHARACTER_ROUTE_NOT_FOUND')return {groupsUrl:null};throw e;});groupsUrl=navigation.groupsUrl;for(const id of ['groups-navigation','mobile-groups']){$('#'+id).hidden=!groupsUrl;if(groupsUrl)$('#'+id).href=groupsUrl;}await refreshWorks();const params=new URLSearchParams(location.search);if(sectionNames[params.get('section')])activeSection=params.get('section');if(params.get('session')){const s=await request(cp+'/sessions/'+params.get('session'));await load(s.binding.reference.workId,s.binding.reference.versionId);await attachSession(s);}
 else if(params.get('work')){await load(params.get('work'),params.get('version'));setScreen(params.get('version')||params.get('view')==='preview'?'preview':'edit');}else {doc=await request(cp+'/blank');render();setScreen(params.has('section')?'edit':params.get('view')==='preview'?'preview':'library');notice('');if(screen!=='library')restoreDraft(null);}});

if(initialMessage){const hit=document.getElementById(initialMessage);if(hit){hit.tabIndex=-1;hit.focus();hit.scrollIntoView?.({block:'center'});}}
return {close(){stream?.close();}};
}
if(typeof window!=='undefined'&&window.document)await mountCharacterStudio({window,document,fetch:window.fetch.bind(window),EventSource:window.EventSource});

if(revisionNavigation){const nav=document.querySelector('header');if(nav){const a=document.createElement('a');a.href=revisionNavigation;a.textContent='续写分支';nav.append(a);}}
