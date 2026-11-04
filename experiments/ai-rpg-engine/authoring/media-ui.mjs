// Gallery UI uses its own operation scope. It never dispatches models or edits source text.
export const imageTypes = ['image/png','image/jpeg','image/webp','image/gif'];
export const mediaName = name => name.replace(/\.[^.]+$/, '');
export const selectedInGroup = (catalog, group, selected) => catalog.images.filter(item => item.groupId === group && selected.has(item.id));
export const mediaReference = value => value?.alias || null;
const copy = value => structuredClone(value);
const definiteFailure = error => error.code === 'AUTHORING_WRITE_FAILED' ||
  (error.httpStatus >= 400 && error.httpStatus < 500 && !['AUTHORING_RESULT_UNKNOWN','AUTHORING_OPERATION_PENDING'].includes(error.code));

/** The UI journal is written before dispatch; recovery never retries a write. */
export function createGalleryClient({api, journal, context = () => ({}), uuid = () => crypto.randomUUID()}) {
  let catalog = null, pending = null, writing = false;
  return {
    get catalog() { return catalog; }, get pending() { return pending; }, get writing() { return writing; },
    hydrate(records) { pending = records.find(item => item.scope === 'gallery') || null; },
    async refresh() {
      catalog = await api('/gallery');
      if (catalog.pending && !pending) pending = {...catalog.pending,scope:'gallery',serverOnly:true};
      return catalog;
    },
    async update(path, fields, expectedRevision = catalog?.revision) {
      if (writing || pending) throw {code:'AUTHORING_OPERATION_PENDING'};
      const operationId = uuid(), body = {operationId,expectedRevision,...copy(fields)};
      const record = {operationId,scope:'gallery',kind:path,at:new Date().toISOString(),...context()};
      writing = true;
      try {
        await journal('put',record); pending = record;
        const receipt = await api(path,body);
        if (receipt.scope !== 'gallery' || receipt.operationId !== operationId || !Number.isInteger(receipt.revision)) throw {code:'NETWORK_RESULT_UNKNOWN'};
        catalog = await api('/gallery');
        await journal('delete',operationId); pending = null;
        return receipt;
      } catch (error) {
        if (definiteFailure(error)) { await journal('delete',operationId); pending = null; }
        if (error.code === 'AUTHORING_OPERATION_PENDING' && error.details?.scope === 'gallery' && error.details.operationId) {
          await journal('delete',operationId);
          pending = {...record,...error.details,serverOnly:true}; await journal('put',pending);
        }
        throw error;
      } finally { writing = false; }
    },
    async recover() {
      if (writing) throw {code:'AUTHORING_OPERATION_PENDING'};
      if (!pending) await this.refresh();
      if (!pending) return {status:'none'};
      writing = true;
      try {
        const result = await api('/gallery/operations/recover',{operationId:pending.operationId});
        if (result.status === 'not_found') return result;
        if (!['committed','not_committed'].includes(result.status) ||
            (result.status === 'committed' && result.receipt?.operationId !== pending.operationId)) throw {code:'NETWORK_RESULT_UNKNOWN'};
        catalog = await api('/gallery');
        await journal('delete',pending.operationId); pending = null;
        return result;
      } finally { writing = false; }
    },
  };
}

const icons = {
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
  edit:'<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14z"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  play:'<path d="m8 5 11 7-11 7z"/>', pause:'<path d="M8 5v14M16 5v14"/>',
  folder:'<path d="M3 7V4h6l3 3h9v13H3z"/>',
};
const icon = name => '<svg viewBox="0 0 24 24" aria-hidden="true">'+icons[name]+'</svg>';
const sizeText = bytes => bytes < 1048576 ? (bytes/1024).toFixed(1)+'KB' : (bytes/1048576).toFixed(2)+'MB';

export function createMediaUI(host) {
  const {editor,api,journal} = host, esc = editor.escapeHtml;
  const client = createGalleryClient({api,journal,context:host.context});
  let galleryDialog = null, target = null, activeGroup = null, editing = false, selected = new Set(), locked = false;
  const sources = new Map(), disposals = new Map(), dialogs = new Set();
  const imageUrl = image => '/rpg-app/api/authoring/gallery/images/'+image.id+'?hash='+image.asset.sha256;
  const action = (label, name, extra='') => '<button type="button" data-media="'+name+'" '+extra+'>'+label+'</button>';
  const errorText = error => error.scope === 'work' ? host.describe(error) : ({
    AUTHORING_REVISION_CONFLICT:'图库已在其他标签页改变。本窗口输入仍保留，请刷新图库核对后再确认。',
    AUTHORING_GALLERY_REVISION_CONFLICT:'图库已在其他标签页改变。本窗口输入仍保留，请先刷新图库并核对，再明确提交。',
    AUTHORING_GALLERY_NAME_INVALID:'名称不能为空、含控制符或首尾空格；分组限 6 字，图片名称限 120 字。',
    AUTHORING_GALLERY_NAME_DUPLICATE:'已存在同名分组，请使用其他名称。',
    AUTHORING_GALLERY_DUPLICATE:'已存在同名分组，请使用其他名称。',
    AUTHORING_GALLERY_NAME_RESERVED:'默认图库为保留名称，请使用其他名称。',
    AUTHORING_GALLERY_GROUP_CAPACITY:'最多创建 20 个分组。',
    AUTHORING_GALLERY_IMAGE_CAPACITY:'图库图片数量已达本地上限。',
    AUTHORING_GALLERY_SELECTION_INVALID:'请先选择图片。',
    AUTHORING_OPERATION_PENDING:'有操作结果待核对。请关闭编辑浮层，在图库核对原操作；不会自动重发。',
    AUTHORING_RESULT_UNKNOWN:'结果仍未确认。已保留原操作编号，核对前不会再次提交。',
    NETWORK_RESULT_UNKNOWN:'连接中断，结果尚未确认。请核对原操作，不会自动重发。',
    AUTHORING_IMAGE_DECODE:'浏览器无法解码此图片，未上传。请换用有效的图片文件。',
    AUTHORING_AUDIO_DECODE:'浏览器无法播放此 MP3，未上传。请换用有效的音频文件。',
    AUTHORING_MEDIA_TYPE:'请选择 PNG、JPEG、WEBP、GIF 图片，或在音乐字段选择 MP3。',
    AUTHORING_ASSET_SIZE:'图片须在 5 MiB 以内，音乐须在 10 MiB 以内，且文件不能为空。',
    AUTHORING_GALLERY_BATCH_CAPACITY:'单次最多上传 20 张图片。',
    LOCAL_JOURNAL_UNAVAILABLE:'无法保存操作保护记录，本次未派发；请检查浏览器本地存储。',
  }[error.code] || host.describe(error));
  function showError(dialog,error) {
    if (!dialog?.isConnected) dialog = galleryDialog?.isConnected ? galleryDialog : modal('操作未完成','media-small','');
    dialog.querySelector('[data-media-progress]')?.remove();
    const node = dialog?.querySelector('.media-error');
    if(node){node.hidden=false;node.textContent=errorText(error);
      if(error.scope !== 'work' && ['AUTHORING_GALLERY_REVISION_CONFLICT','AUTHORING_REVISION_CONFLICT'].includes(error.code)){
        const refresh=document.createElement('button');refresh.type='button';refresh.textContent='刷新图库并保留输入';
        refresh.onclick=()=>perform(dialog,async()=>{await client.refresh();dialog.dispatchEvent(new Event('gallery-refreshed'));refresh.remove();node.textContent='已读取最新图库；输入仍保留，请核对后明确确认。';if(galleryDialog)renderGallery();});node.append(refresh);
      }
    }
    else editor.toast(errorText(error));
  }
  function close(dialog) { if(!locked) dialog.close(); }
  function modal(title,kind,body,submit=null,submitLabel='确认') {
    const previous=document.activeElement, dialog=document.createElement('dialog');
    dialog.className='media-dialog '+kind; dialog.setAttribute('aria-label',title);
    dialog.innerHTML='<form><header><h2>'+esc(title)+'</h2>'+action(icon('close'),'close','class="media-close" aria-label="关闭'+esc(title)+'"')+'</header><div class="media-body">'+body+'<p class="media-error" role="alert" hidden></p></div><footer>'+action(submit?'取消':'关闭','cancel')+(submit?'<button type="submit" class="primary">'+submitLabel+'</button>':'')+'</footer></form>';
    document.body.append(dialog); dialogs.add(dialog);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close(dialog);});
    dialog.addEventListener('click',event=>{
      if(event.target.closest('[data-media="close"],[data-media="cancel"]'))close(dialog);
      if(event.target===dialog){const b=dialog.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)close(dialog);}
    });
    dialog.addEventListener('close',()=>{
      disposals.get(dialog)?.();disposals.delete(dialog);dialogs.delete(dialog);dialog.remove();
      if(dialog===galleryDialog)galleryDialog=null;
      if(previous?.isConnected)previous.focus({preventScroll:true});
    });
    dialog.querySelector('form').addEventListener('submit',event=>{event.preventDefault();if(submit&&!locked)submit(dialog);});
    dialog.showModal();return dialog;
  }
  async function perform(dialog, task) {
    if(locked)return;
    locked=true;
    const previous=[];
    for(const node of document.querySelectorAll('.media-dialog button,.media-dialog input')){previous.push([node,node.disabled]);node.disabled=true;}
    try { await task(); }
    catch(error){showError(dialog,error);}
    finally {
      for(const [node,disabled]of previous)if(node.isConnected)node.disabled=disabled;
      locked=false;
      if(client.pending&&galleryDialog)renderGallery();
    }
  }
  const groupName = value => client.catalog?.groups.find(item=>item.id===value)?.name || '默认图库';
  function groupControl(dialog,label,initial,onChange) {
    const wrapper=document.createElement('div');wrapper.className='media-group-control';
    const text=document.createElement('label');text.textContent=label;
    const trigger=document.createElement('button');trigger.type='button';trigger.className='media-group-trigger';
    trigger.setAttribute('role','combobox');trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-haspopup','listbox');
    let value=initial,popup=null,position=null;
    const update=()=>{trigger.innerHTML='<span>'+esc(groupName(value))+'</span><span aria-hidden="true">⌄</span>';};update();
    wrapper.append(text,trigger);dialog.querySelector('.media-body').insertBefore(wrapper,dialog.querySelector('.media-error'));
    const dismiss=restore=>{popup?.remove();popup=null;if(position){window.removeEventListener('resize',position);document.removeEventListener('scroll',position,true);position=null;}trigger.setAttribute('aria-expanded','false');if(restore)trigger.focus();};
    dialog.addEventListener('close',()=>dismiss(false),{once:true});
    const open=()=>{
      if(popup){dismiss(true);return;}
      popup=document.createElement('div');popup.className='media-group-options';popup.setAttribute('role','listbox');popup.setAttribute('aria-label',label);
      const choices=[{id:'new',name:'＋ 新增'},{id:null,name:'默认图库'},...client.catalog.groups];
      popup.innerHTML=choices.map((item,index)=>'<button type="button" role="option" aria-selected="'+(item.id===value)+'" data-choice="'+index+'">'+esc(item.name)+(item.id===value?'<span aria-hidden="true">✓</span>':'')+'</button>').join('');
      wrapper.append(popup);trigger.setAttribute('aria-expanded','true');
      position=()=>{if(!popup)return;const b=trigger.getBoundingClientRect();const below=innerHeight-b.bottom-8,above=b.top-8;const up=below<120&&above>below;const height=Math.min(190,Math.max(64,up?above:below));popup.style.width=b.width+'px';popup.style.left=b.left+'px';popup.style.maxHeight=height+'px';popup.style.top=(up?Math.max(8,b.top-Math.min(popup.scrollHeight,height)-5):b.bottom+5)+'px';};position();
      window.addEventListener('resize',position);document.addEventListener('scroll',position,true);
      const controls=[...popup.querySelectorAll('button')];controls[Math.max(0,choices.findIndex(item=>item.id===value))].focus();
      popup.addEventListener('click',event=>{const item=event.target.closest('[data-choice]');if(!item)return;const choice=choices[Number(item.dataset.choice)];dismiss(false);
        if(choice.id==='new'){createGroup(id=>{value=id;update();onChange(id,true);trigger.focus();});}
        else{value=choice.id;update();onChange(value,false);trigger.focus();}});
      popup.addEventListener('keydown',event=>{let at=controls.indexOf(document.activeElement),next=at;
        if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss(true);return;}
        if(event.key==='ArrowDown')next=(at+1)%controls.length;if(event.key==='ArrowUp')next=(at+controls.length-1)%controls.length;
        if(event.key==='Home')next=0;if(event.key==='End')next=controls.length-1;
        if(next!==at){event.preventDefault();controls[next].focus();}
        if(event.key==='Tab')dismiss(false);
      });
    };
    trigger.onclick=open;
    trigger.onkeydown=event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();if(!popup)open();}};
    dialog.addEventListener('click',event=>{if(!wrapper.contains(event.target))dismiss(false);});
    return {get value(){return value;}};
  }
  function createGroup(onCreated) {
    const dialog=modal('创建文件夹','media-small','<p class="hint">请输入文件夹名称，6 个字以内</p><input aria-label="文件夹名称" autocomplete="off"><p class="hint">确认后分组立即保存。取消外层图片编辑不会撤销已创建的分组。</p>',d=>{
      const name=d.querySelector('input').value;
      perform(d,async()=>{const receipt=await client.update('/gallery/groups',{name});d.close();onCreated(receipt.result.groupId);if(galleryDialog)renderGallery();});
    });
    const input=dialog.querySelector('input'),confirm=dialog.querySelector('[type="submit"]');
    const check=()=>{confirm.disabled=!input.value.trim()||[...input.value].length>6;};input.oninput=check;check();input.focus();
  }
  function renderGallery() {
    const dialog=galleryDialog;if(!dialog)return;
    const catalog=client.catalog;
    if(!catalog)return;
    if(activeGroup&&!catalog.groups.some(group=>group.id===activeGroup))activeGroup=null;
    const oldScroll=dialog.querySelector('.media-gallery-grid')?.scrollTop||0;
    dialog.querySelector('h2').textContent='创作图库（容量 '+catalog.images.length+'/'+catalog.limits.images+'）';
    const list=catalog.images.filter(image=>image.groupId===activeGroup),visible=selectedInGroup(catalog,activeGroup,selected);
    dialog.querySelector('.media-body').innerHTML='<div class="media-gallery-toolbar"><div class="media-group-tabs" role="tablist" aria-label="图库分组">'+[{id:null,name:'默认图库'},...catalog.groups].map(group=>action(esc(group.name),'group-tab','role="tab" aria-selected="'+(group.id===activeGroup)+'" data-group="'+(group.id||'')+'"')).join('')+'</div>'+action(icon('upload')+'<span>上传图片</span>','upload','aria-label="上传图片"'+(client.pending?' disabled':''))+'</div>'+
      (client.pending?'<div class="media-pending"><p>有图库操作结果待核对。不会自动重发。</p><small>'+esc(client.pending.operationId)+'</small>'+action('核对原操作','recover')+'</div>':'')+
      '<p class="media-feedback" role="status" hidden></p><div class="media-gallery-grid">'+list.map(image=>'<article class="media-image-card" data-image="'+image.id+'"><button type="button" class="media-image-preview" data-media="image" data-id="'+image.id+'" aria-label="'+(editing?'选择 ':target?'选用 ':'查看 ')+esc(image.name)+'"'+(editing?' aria-pressed="'+selected.has(image.id)+'"':'')+'><img src="'+imageUrl(image)+'" alt="'+esc(image.name)+'"><span class="media-image-failure" hidden>图片无法显示</span></button>'+
      (editing?'<label class="media-image-check"><input type="checkbox" aria-label="勾选 '+esc(image.name)+'" data-image-select="'+image.id+'"'+(selected.has(image.id)?' checked':'')+'></label>':'<div class="media-image-tools">'+action(icon('copy'),'copy','aria-label="复制 '+esc(image.name)+' 的资源引用" data-id="'+image.id+'"')+action(icon('edit'),'edit-image','aria-label="编辑 '+esc(image.name)+'" data-id="'+image.id+'"')+'</div>')+
      '<div class="media-image-caption"><strong>'+esc(image.name)+'</strong><small>'+sizeText(image.asset.size)+' '+image.asset.mediaType.split('/')[1].toUpperCase()+'</small><small data-dimensions></small></div></article>').join('')+(list.length?'':'<p class="media-gallery-empty">暂无图片</p>')+'</div><p class="media-error" role="alert" hidden></p>';
    dialog.querySelector('footer').innerHTML=editing?'<label><input type="checkbox" data-select-all'+(visible.length&&visible.length===list.length?' checked':'')+'>全选</label><span>已选择 '+selected.size+' 张</span>'+action('移动分组','move',selected.size&&!client.pending?'':'disabled')+action('取消编辑','edit-mode'):
      '<span class="media-gallery-note">本地图库 · 选用后保存作品</span>'+action('分组管理','groups')+action('编辑','edit-mode');
    dialog.querySelector('.media-gallery-grid').scrollTop=oldScroll;
    for(const image of dialog.querySelectorAll('img')){
      const ready=()=>{const card=image.closest('article');if(image.naturalWidth){card.querySelector('[data-dimensions]').textContent=image.naturalWidth+' × '+image.naturalHeight+' 像素';}
        else{card.querySelector('.media-image-failure').hidden=false;image.hidden=true;}};
      image.onload=ready;image.onerror=ready;if(image.complete)ready();
    }
    dialog.querySelector('[data-select-all]')?.addEventListener('change',event=>{for(const item of list)event.target.checked?selected.add(item.id):selected.delete(item.id);renderGallery();});
    for(const input of dialog.querySelectorAll('[data-image-select]'))input.onchange=()=>{input.checked?selected.add(input.dataset.imageSelect):selected.delete(input.dataset.imageSelect);};
    dialog.querySelectorAll('[data-image-select]').forEach(input=>input.addEventListener('change',renderGallery));
  }
  async function openGallery(field=null) {
    if(galleryDialog)return;
    host.writable();target=field;activeGroup=null;editing=false;selected=new Set();
    galleryDialog=modal('创作图库','media-gallery','<p>正在读取图库…</p>');
    const dialog=galleryDialog;
    dialog.addEventListener('click',event=>{
      const button=event.target.closest('[data-media]');if(!button||locked)return;
      const image=client.catalog?.images.find(item=>item.id===button.dataset.id),kind=button.dataset.media;
      if(kind==='group-tab'){activeGroup=button.dataset.group||null;renderGallery();}
      if(kind==='edit-mode'){editing=!editing;if(!editing)selected.clear();renderGallery();}
      if(kind==='image'){
        if(editing){selected.has(image.id)?selected.delete(image.id):selected.add(image.id);renderGallery();}
        else if(target)selectImage(image);
        else{dialog.querySelectorAll('.media-image-card.revealed').forEach(item=>item.classList.remove('revealed'));button.closest('article').classList.add('revealed');}
      }
      if(kind==='upload')chooseFile(false,true,files=>uploadImages(files,target));
      if(kind==='edit-image')editImage(image);
      if(kind==='move')moveImages();
      if(kind==='groups')manageGroups();
      if(kind==='recover')perform(dialog,async()=>{const result=await client.recover();renderGallery();showError(dialog,{code:'GALLERY_RECOVERY_MESSAGE',message:result.status});dialog.querySelector('.media-error').textContent=result.status==='not_found'?'原操作尚未确认，保留记录，不会重发。':result.status==='committed'?'原操作已提交并恢复，未重发。':'已确认原操作未提交，可核对后明确再次操作。';});
      if(kind==='copy')perform(dialog,async()=>{const alias=await host.adopt(image,client.catalog.revision);try{await navigator.clipboard.writeText('asset://'+alias);const feedback=dialog.querySelector('.media-feedback');feedback.hidden=false;feedback.textContent='已复制图片资源引用：asset://'+alias;}catch{throw {code:'CLIPBOARD_UNAVAILABLE'};}});
    });
    try{await client.refresh();if(galleryDialog===dialog)renderGallery();}catch(error){showError(dialog,error);}
  }
  function selectImage(image) {
    perform(galleryDialog,async()=>{const alias=await host.adopt(image,client.catalog.revision);host.choose(alias,target);galleryDialog?.close();});
  }
  function editImage(image) {
    if(client.pending)return;
    let revision=client.catalog.revision;
    const dialog=modal('编辑图片','media-image-editor','<div class="media-upload-row"><img src="'+imageUrl(image)+'" alt="图片 1"><label>图片 1<input aria-label="图片名称" value="'+esc(image.name)+'" maxlength="240"></label></div>',d=>perform(d,async()=>{
      await client.update('/gallery/images/edit',{imageId:image.id,name:d.querySelector('input').value,groupId:group.value},revision);d.close();renderGallery();
    }));
    const group=groupControl(dialog,'选择分组',image.groupId,(_,created)=>{if(created)revision=client.catalog.revision;});
    dialog.addEventListener('gallery-refreshed',()=>{revision=client.catalog.revision;});
  }
  function moveImages() {
    let revision=client.catalog.revision;
    const ids=[...selected];
    const dialog=modal('批量移动到分组','media-small','<p>选择目标分组（已选 '+ids.length+' 条作品）</p>',d=>perform(d,async()=>{
      await client.update('/gallery/images/move',{imageIds:ids,groupId:group.value},revision);d.close();selected.clear();renderGallery();
    }));
    const group=groupControl(dialog,'选择目标分组',activeGroup,(_,created)=>{if(created)revision=client.catalog.revision;});
    dialog.addEventListener('gallery-refreshed',()=>{revision=client.catalog.revision;});
  }
  function manageGroups() {
    modal('分组管理（'+client.catalog.groups.length+'/20）','media-small',
      '<div class="media-group-list">'+client.catalog.groups.map(group=>'<p><span>'+esc(group.name)+'</span><span>'+client.catalog.images.filter(image=>image.groupId===group.id).length+' 张</span></p>').join('')+'</div><p class="hint">本批可在图片编辑的分组下拉中新建和选择分组。删除交互尚待原站核验，未提供删除入口。</p>');
  }
  function chooseFile(audio,multiple,ready) {
    const input=document.createElement('input');input.type='file';input.accept=audio?'.mp3,audio/mpeg':imageTypes.join(',');input.multiple=multiple;input.hidden=true;
    document.body.append(input);
    input.addEventListener('change',()=>{const files=[...input.files];input.remove();if(files.length)ready(files);},{once:true});
    input.addEventListener('cancel',()=>input.remove(),{once:true});input.click();
  }
  async function validateFile(file,audio) {
    if(audio ? !(file.type==='audio/mpeg'||(!file.type&&/\.mp3$/i.test(file.name))) : !imageTypes.includes(file.type))throw {code:'AUTHORING_MEDIA_TYPE'};
    if(!file.size||file.size>(audio?10:5)*1048576)throw {code:'AUTHORING_ASSET_SIZE'};
    const url=URL.createObjectURL(file);
    try {
      const dimensions=await new Promise((resolve,reject)=>{
        const element=audio?document.createElement('audio'):new Image();
        const done=()=>{clearTimeout(timer);element.onerror=null;element.onload=null;element.onloadedmetadata=null;if(audio){element.removeAttribute('src');element.load();}};
        const timer=setTimeout(()=>{done();reject({code:audio?'AUTHORING_AUDIO_DECODE':'AUTHORING_IMAGE_DECODE'});},10000);
        const success=()=>{const value=audio?{duration:element.duration}:{width:element.naturalWidth,height:element.naturalHeight};done();resolve(value);};
        element.onerror=()=>{done();reject({code:audio?'AUTHORING_AUDIO_DECODE':'AUTHORING_IMAGE_DECODE'});};
        if(audio){element.preload='metadata';element.onloadedmetadata=success;}else element.onload=success;
        element.src=url;
      });
      return {file,url,dimensions,name:mediaName(file.name)};
    } catch(error){URL.revokeObjectURL(url);throw error;}
  }
  async function uploadImages(files,field=null) {
    const items=[];
    let dialog=modal('编辑','media-image-editor','<p data-media-progress>正在校验所选图片…</p>');
    try {
      host.writable();
      if(files.length>20)throw {code:'AUTHORING_GALLERY_BATCH_CAPACITY'};
      for(const file of files)items.push(await validateFile(file,false));
      if(!dialog.open){items.forEach(item=>URL.revokeObjectURL(item.url));return;}
      if(!client.catalog)await client.refresh();
      dialog.close();
      let revision=client.catalog.revision;
      dialog=modal('编辑','media-image-editor','<p>请输入您新的图片命名</p>'+items.map((item,index)=>'<div class="media-upload-row"><img src="'+item.url+'" alt="图片 '+(index+1)+'"><label>图片 '+(index+1)+'<input aria-label="图片 '+(index+1)+' 名称" data-upload-name="'+index+'" value="'+esc(item.name)+'" maxlength="240"></label></div>').join(''),d=>perform(d,async()=>{
        const payload=await Promise.all(items.map(async(item,index)=>({name:d.querySelector('[data-upload-name="'+index+'"]').value,groupId:group.value,fileName:item.file.name,mediaType:item.file.type,base64:await host.base64(item.file)})));
        const receipt=await client.update('/gallery/images',{images:payload},revision);
        d.close();if(galleryDialog)renderGallery();
        if(field){const image=client.catalog.images.find(item=>item.id===receipt.result.imageIds[0]);const alias=await host.adopt(image,client.catalog.revision);host.choose(alias,field);galleryDialog?.close();}
      }));
      disposals.set(dialog,()=>items.forEach(item=>URL.revokeObjectURL(item.url)));
      const group=groupControl(dialog,'添加至',activeGroup,(_,created)=>{if(created)revision=client.catalog.revision;});
      dialog.addEventListener('gallery-refreshed',()=>{revision=client.catalog.revision;});
    } catch(error){items.forEach(item=>URL.revokeObjectURL(item.url));showError(dialog,error);}
  }
  function uploadDirect(file,path) {
    const dialog=modal('上传图片','media-small','<p data-media-progress>正在校验和保存图片…</p>');
    perform(dialog,async()=>{host.writable();const item=await validateFile(file,false);try{await host.uploadImage(file,path);dialog.close();}finally{URL.revokeObjectURL(item.url);}});
  }
  function sourceKey(path){return host.workId()+':'+path;}
  function sourceMode(path){const key=sourceKey(path);if(!sources.has(key)){let mode=false;try{mode=localStorage.getItem('authoring-media-source:'+key)==='gallery';}catch{}sources.set(key,mode);}return sources.get(key);}
  function field(title,path) {
    const ref=editor.read()[path], asset=host.assets().find(item=>item.alias===ref), mode=sourceMode(path);
    return '<section class="field media-field" data-media-field="'+path+'"><div class="field-row"><span class="field-title">'+title+'</span><label class="media-source-switch">使用图库图片<input type="checkbox" role="switch" aria-label="'+title+'使用图库图片" data-media-source="'+path+'"'+(mode?' checked':'')+'></label></div><div class="media-field-image"><button type="button" class="media-field-select" data-media-field-select="'+path+'" aria-label="选择'+title+'">'+(asset?'<img src="'+host.assetUrl(asset)+'" alt="'+esc(asset.fileName)+'">':'<span aria-hidden="true">＋</span>')+'</button>'+(ref?'<button type="button" class="media-field-clear" data-media-clear="'+path+'" aria-label="移除'+title+'">×</button>':'')+'</div><p class="hint">仅支持上传图片：PNG、JPG、JPEG、WEBP、GIF</p>'+(ref&&!asset?'<p class="danger">缺少资源：asset://'+esc(ref)+'。引用已保留。</p>':'')+'<p class="media-image-failure danger" hidden>图片无法显示，请重新选择有效资源。</p></section>';
  }
  function music() {
    const value=editor.read().bgm;
    return '<section class="field media-music"><h3>背景音乐</h3><p class="hint">格式为 MP3，大小 10 MiB 以内</p>'+(value?'<div class="media-music-row"><div class="media-music-player">'+action(icon('play'),'play-audio','class="media-play" aria-label="播放音乐"'+(!value.url?' disabled':''))+'<span title="'+esc(value.name)+'">'+esc(mediaName(value.name))+'</span><audio preload="metadata" '+(value.url?'src="'+esc(value.url)+'"':'')+'></audio></div>'+action(icon('trash'),'clear-audio','class="media-audio-clear" aria-label="移除背景音乐"')+'</div>':action('上传','upload-audio','class="primary"'))+(value?'<label class="media-autoplay"><input type="checkbox" data-bgm-autoplay'+(editor.read().bgmAutoplay===true?' checked':'')+'> 聊天自动播放</label>':'')+'<p class="hint">可在此试听，聊天中可播放、暂停或收起。启用自动播放后，浏览器仍可能要求先点击播放。</p><p class="media-audio-error danger" role="alert" hidden></p></section>';
  }
  function bindFields() {
    for(const input of document.querySelectorAll('[data-media-source]'))input.onchange=()=>{const key=sourceKey(input.dataset.mediaSource);sources.set(key,input.checked);try{localStorage.setItem('authoring-media-source:'+key,input.checked?'gallery':'file');}catch{}};
    for(const button of document.querySelectorAll('[data-media-field-select]'))button.onclick=()=>{
      const path=button.dataset.mediaFieldSelect;
      if(sourceMode(path))openGallery(path).catch(error=>editor.toast(errorText(error)));
      else chooseFile(false,false,files=>uploadDirect(files[0],path));
    };
    for(const button of document.querySelectorAll('[data-media-clear]'))button.onclick=()=>editor.change(button.dataset.mediaClear,null);
    for(const image of document.querySelectorAll('.media-field img')){
      const error=()=>{if(!image.naturalWidth)image.closest('.media-field').querySelector('.media-image-failure').hidden=false;};image.onerror=error;if(image.complete)error();
    }
    const container=document.querySelector('.media-music');if(!container)return;
    const audio=container.querySelector('audio'),play=container.querySelector('[data-media="play-audio"]');
    // Basic settings rebind before the old Other settings DOM is replaced.
    if(audio&&!editor.read().bgm){audio.pause();return;}
    container.querySelector('[data-bgm-autoplay]')?.addEventListener('change',event=>{audio?.pause();editor.change('bgmAutoplay',event.target.checked);});
    const audioError=message=>{const node=container.querySelector('.media-audio-error');node.hidden=false;node.textContent=message;};
    if(audio&&play){
      const update=()=>{play.setAttribute('aria-label',audio.paused?'播放音乐':'暂停音乐');play.innerHTML=icon(audio.paused?'play':'pause');};
      audio.onplay=audio.onpause=audio.onended=update;audio.onerror=()=>audioError('音频无法播放。已有引用保留，可移除后重新上传。');
      play.onclick=()=>{host.beforeAudio?.();if(audio.paused)audio.play().catch(()=>audioError('音频无法播放，请检查文件或浏览器播放权限。'));else audio.pause();};
      if(!editor.read().bgm.url)audioError('缺少音乐资源，引用已保留。');
    }
    container.querySelector('[data-media="clear-audio"]')?.addEventListener('click',()=>{audio?.pause();editor.change('bgm',null);});
    container.querySelector('[data-media="upload-audio"]')?.addEventListener('click',()=>chooseFile(true,false,files=>{
      const dialog=modal('上传音乐','media-small','<p data-media-progress>正在校验和保存音频…</p>');
      perform(dialog,async()=>{host.writable();const item=await validateFile(files[0],true);try{await host.uploadAudio(item.file);dialog.close();}finally{URL.revokeObjectURL(item.url);}});
    }));
  }
  return {client,open:openGallery,field,music,bindFields,uploadImages,get pending(){return client.pending;}};
}
