import {messagePresentation} from '../group/message-display.mjs';

export const defaultDisplay=()=>({enabled:false,paragraphs:true,find:'',replacement:''});
export function validDisplay(p){
  return p&&typeof p.enabled==='boolean'&&typeof p.paragraphs==='boolean'&&
    typeof p.find==='string'&&p.find.length<=80&&typeof p.replacement==='string'&&p.replacement.length<=160;
}
export function displayText(raw,p){
  if(!validDisplay(p)||!p.enabled)return {text:raw,changed:false};
  let text=p.paragraphs?messagePresentation({content:raw,source:{kind:'model-result'}}).text:raw;
  if(p.find){
    // Reject the entire conversion on overflow. Never truncate the original.
    const pieces=[];let offset=0,index,count=0;
    while((index=text.indexOf(p.find,offset))>=0){
      if(++count>512||text.length+count*(p.replacement.length-p.find.length)>100000)return {text:raw,changed:false,limited:true};
      pieces.push(text.slice(offset,index),p.replacement);offset=index+p.find.length;
    }
    if(count)text=pieces.join('')+text.slice(offset);
  }
  return {text,changed:text!==raw};
}

// The page supplies a read-only receipt lookup. Settings and rendered strings never
// enter the authoring command builder or any saved message.
export function createDisplayPanel(document,lookup){
  const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  let scope,history=[],policy=defaultDisplay();
  const memory=new Map(),opened=new Map();
  const storageKey=s=>'character-display/1/'+JSON.stringify([s.familyId,s.branchId,s.targetId]);
  function body(container,original){
    const rendered=displayText(original,policy);
    const text=el('div',rendered.text);text.className='response-text';container.append(text);
    if(rendered.limited)container.append(el('small','替换超出上限，已显示原文。'));
    if(rendered.changed){
      const details=el('details');details.append(el('summary','查看转换前原文'),el('pre',original));container.append(details);
    }
  }
  function receipt(container,by,id){
    const details=el('details'),panel=el('div'),ref={...scope,by,id};
    if(by==='request')details.dataset.requestId=id;
    details.append(el('summary',by==='message'?'核对原始响应与回执':'查看本次响应与回执 · '+id.slice(-8)),panel);
    let loading=false;
    function draw(proof){
      panel.replaceChildren();
      if(!proof.available){panel.append(el('p','这条记录没有可核对的本地回执。'));return;}
      if(by==='request'){panel.append(el('strong',proof.kind==='regenerate'?'精确重生成候选':'角色回复'));body(panel,proof.responseText);}
      panel.append(el('strong','原始响应'),el('pre',proof.rawText),el('strong','回执正文'),el('pre',proof.responseText),
        el('small','请求 '+proof.requestId+'\n上下文哈希 '+proof.contextHash+'\n回执哈希 '+proof.receiptHash));
    }
    details.ontoggle=async()=>{
      if(!details.open||loading)return;
      if(opened.has(details)){draw(opened.get(details));return;}
      loading=true;panel.textContent='正在读取原回执…';
      try{const proof=await lookup(ref);if(details.isConnected){opened.set(details,proof);draw(proof);}}
      catch{panel.textContent='未能核对回执。可收起后再读取。';}
      finally{loading=false;}
    };
    container.append(details);
  }
  function drawHistory(){
    $('history').replaceChildren();
    for(const m of history){
      const li=el('li');li.id='history-'+m.id;li.tabIndex=-1;
      if(m.canRegenerate){body(li,m.content);if(m.speakerId===scope.targetId)receipt(li,'message',m.id);}
      else li.textContent=m.content;
      $('history').append(li);
    }
  }
  function apply(){
    const next={enabled:$('display-enabled').checked,paragraphs:$('display-paragraphs').checked,find:$('display-find').value,replacement:$('display-replacement').value};
    if(!validDisplay(next))return;
    policy=next;
    memory.set(storageKey(scope),policy);
    let persisted=true;
    try{document.defaultView.sessionStorage.setItem(storageKey(scope),JSON.stringify(policy));}catch{persisted=false;}
    for(const [details,proof] of opened){
      if(details.closest('#history')||!details.isConnected)opened.delete(details);
      else if(details.open)details.ontoggle();
    }
    drawHistory();$('display-status').textContent=persisted?'显示设置已应用于此标签页。':'显示设置仅在当前页面生效。';
  }
  $('display-apply').onclick=apply;
  return {
    show(s,messages){
      scope=s;history=messages;opened.clear();
      const key=storageKey(scope);let saved=memory.get(key);
      if(!saved){try{saved=JSON.parse(document.defaultView.sessionStorage.getItem(key));}catch{}}
      policy=validDisplay(saved)?saved:defaultDisplay();
      $('display-enabled').checked=policy.enabled;$('display-paragraphs').checked=policy.paragraphs;
      $('display-find').value=policy.find;$('display-replacement').value=policy.replacement;$('display-status').textContent='';
      drawHistory();
    },
    receipt
  };
}
