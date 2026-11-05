// Gallery reads never create display records. Each dialog owns its frame tokens.
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(label, action, className) {
  const node = element('button', className, label);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

export function createSpecialCGGallery({ api, getSession, notice }) {
  const dialogs = new Set();
  const base = '/joint/playback';
  function popup(title, kind, afterClose) {
    const dialog = element('dialog', 'special-cg-dialog '+kind);
    dialog.setAttribute('aria-label', title);
    const header = element('header', 'special-cg-heading');
    const heading = element('h2', '', title);
    header.append(heading);
    if (kind !== 'cg-hint') {
      const close = button('×', () => dialog.close(), 'special-cg-close');
      close.setAttribute('aria-label','关闭'+title);
      header.append(close);
    }
    const body = element('div','special-cg-body');
    dialog.append(header,body);
    const previous = document.activeElement;
    document.body.append(dialog);
    dialogs.add(dialog);
    dialog.addEventListener('close', () => {
      dialogs.delete(dialog);
      afterClose?.();
      dialog.remove();
      if (previous?.isConnected) previous.focus({preventScroll:true});
    },{once:true});
    dialog.showModal();
    return {dialog,body,heading};
  }
  function confirm(dialog,body) {
    const footer = element('footer');
    const ok = button('确认', () => dialog.close(),'special-cg-confirm');
    footer.append(ok);
    body.append(footer);
    if (dialog.classList.contains('cg-hint')) ok.focus();
  }
  function hint(card) {
    const {dialog,body} = popup('解锁提示','cg-hint');
    dialog.setAttribute('role','alertdialog');
    body.append(element('p','special-cg-hint',card.hint || '作者暂未填写提示。'));
    confirm(dialog,body);
  }
  function artwork(card) {
    const img = element('img','special-cg-art');
    img.src = card.unlock.imageUrl;
    img.alt = card.name;
    img.loading = 'lazy';
    img.addEventListener('error',()=>{img.alt=card.name+'（图片暂时无法载入）';},{once:true});
    return img;
  }
  function viewImage(card) {
    const {dialog,body} = popup(card.name,'cg-image');
    const frame=element('div','special-cg-card special-cg-large');
    frame.append(element('strong','special-cg-ribbon',card.name));
    frame.append(artwork(card));body.append(frame);confirm(dialog,body);
  }
  async function recap(sessionId,card) {
    const turnNumber=card.unlock.turnNumber;
    const session=getSession();
    if(session?.id!==sessionId || !session.turns[turnNumber-1])return;
    const turn=session.turns[turnNumber-1],tokens=[];
    const release=token=>api('/presentations/release',{token}).catch(()=>{});
    const {dialog,body}=popup('剧情回顾','cg-recap',()=>tokens.forEach(release));
    const rows = ['user','assistant'].map((role,index)=>{
      const article=element('article','special-cg-message '+role);
      const label=index?'剧情回复':'玩家输入';
      article.append(element('strong','',label));
      const content=element('div','special-cg-message-content');
      content.append(element('p','',index?turn.assistant:turn.user));
      article.append(content);body.append(article);
      return {role,label,content};
    });
    // Start with safe text and replace it only with the normal isolated turn renderer.
    await Promise.all(rows.map(async({role,label,content})=>{
      try{
        const frame=await api(base+'/prepare',{sessionId,turnNumber,role,device:innerWidth<=600?'mobile':'desktop'});
        if(!dialog.open || getSession()?.id!==sessionId){void release(frame.token);return;}
        tokens.push(frame.token);
        const iframe=element('iframe','');iframe.title=label;
        iframe.setAttribute('sandbox','allow-scripts');iframe.referrerPolicy='no-referrer';
        iframe.setAttribute('allow',"camera 'none'; microphone 'none'; clipboard-read 'none'; clipboard-write 'none'");
        iframe.src=frame.frameUrl;content.replaceChildren(iframe);
      }catch{if(dialog.open)content.prepend(element('small','','暂时无法载入排版，以下为已保存的原文。'));}
    }));
  }
  function cardNode(card,sessionId) {
    const node=element('article','special-cg-card'+(card.unlock?'':' locked'));
    node.append(element('strong','special-cg-ribbon',card.name));
    const center=element('div','special-cg-face');
    if(card.unlock){
      center.append(artwork(card));
      const actions=element('div','special-cg-actions');
      actions.append(button('查看',()=>viewImage(card)),button('剧情回顾',()=>void recap(sessionId,card)));
      center.append(actions);
    }else{
      const mark=element('span','special-cg-mark','✧');mark.setAttribute('aria-hidden','true');center.append(mark);
      if(card.hint!==null)center.append(button('解锁提示',()=>hint(card),'special-cg-hint-button'));
    }
    node.append(center);return node;
  }
  async function open() {
    const id=getSession()?.id;
    if(!id){notice('请先打开一个作品会话。');return;}
    const {dialog,body,heading}=popup('CG 画廊','cg-gallery');
    const status=element('p','special-cg-status','正在读取画廊…');status.setAttribute('role','status');body.append(status);
    try{
      const data=await api(base+'/gallery/'+encodeURIComponent(id));
      if(!dialog.open || getSession()?.id!==id || data.sessionId!==id)return;
      heading.textContent=data.title;
      heading.append(element('small','','（解锁进度'+data.unlocked+'/'+data.cards.length+'）'));
      body.replaceChildren();
      if(!data.cards.length)body.append(element('p','special-cg-status','这个作品版本没有设置特殊 CG。'));
      if(data.waitingForTurn)body.append(element('p','special-cg-status','第 '+data.waitingForTurn+' 回合的展示待核对。恢复该回合展示后可在此查看收藏。'));
      const groups=new Map();
      for(const card of data.cards){
        if(!groups.has(card.groupId)){
          body.append(element('h3','',card.group));
          const grid=element('div','special-cg-grid');body.append(grid);groups.set(card.groupId,grid);
        }
        groups.get(card.groupId).append(cardNode(card,id));
      }
    }catch{if(dialog.open)status.textContent='暂时无法读取画廊，请关闭后重新打开。';}
  }
  return {open,close(){for(const dialog of [...dialogs].reverse())if(dialog.open)dialog.close();}};
}
