import {parse, serialize} from 'parse5';
import {Marked} from 'marked';
import {randomBytes} from 'node:crypto';
import {Worker,isMainThread,parentPort,workerData} from 'node:worker_threads';
import {fail, sha256, canonical, validatePresentation} from './contracts.mjs';
import {validatePlatform} from './platform.mjs';
import {installDisplayPlatform} from './platform-browser.mjs';
import {projectSuggestions} from './input-assistance.mjs';

export const PRESENTATION = Object.freeze({format:'modelmirror.rpg.presentation/1', textBytes:2*1024*1024, frameBytes:12*1024*1024, cacheBytes:48*1024*1024, count:12, ttl:10*60*1000, inputCharacters:120000});
const htmlNamespace = 'http://www.w3.org/1999/xhtml';
const svgNamespace = 'http://www.w3.org/2000/svg';
const blocked = new Set('script iframe frame frameset object embed template noscript base meta link svg math portal fencedframe'.split(' '));
const svgElements = new Set('svg g path rect circle ellipse line polyline polygon text tspan defs linearGradient radialGradient stop title desc clipPath mask use marker symbol'.split(' '));
const svgAttributes = new Set('id class style role viewBox preserveAspectRatio x y x1 y1 x2 y2 dx dy cx cy r rx ry width height d points transform fill fill-rule fill-opacity stroke stroke-width stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity opacity offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod fx fy fr clip-path clip-rule clipPathUnits mask maskUnits maskContentUnits marker-start marker-mid marker-end markerWidth markerHeight markerUnits refX refY orient text-anchor dominant-baseline font-family font-size font-weight letter-spacing textLength lengthAdjust vector-effect'.split(' '));
const fragment = /^#[a-zA-Z_][a-zA-Z0-9_.:-]*$/;
const markdown = new Marked({gfm:true,breaks:true,async:false});
const attributes = new Set('id class style title role lang dir hidden open for type name value checked selected disabled readonly required multiple placeholder minlength maxlength min max step rows cols width height colspan rowspan scope alt controls loop muted preload tabindex'.split(' '));
const imageTypes = new Set(['image/png','image/jpeg','image/webp','image/gif']);
const escape = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const cssText = value => value.replaceAll('<','\\3c ');
const jsonScript = value => JSON.stringify(value).replaceAll('<','\\u003c');
const text = (value, path) => {
  if(typeof value!=='string'||!value.isWellFormed()||Buffer.byteLength(value)>PRESENTATION.textBytes)fail('AUTHORING_PRESENTATION_SIZE',path);
  return value;
};

export function presentationAliases(html, css) {
  text(html,'/html');text(css,'/css');
  return [...new Set((html+'\n'+css).match(/asset:\/\/[a-zA-Z0-9][a-zA-Z0-9._-]*/g)||[])].map(value=>value.slice(8));
}

// Only this fixed adapter runs in the browser. Author JS stays in QuickJS;
// model JS never runs. Host effects still require a separate host UI gesture.
function bootstrap(config, installDisplayPlatform) {
  const send=(type,payload)=>parent.postMessage({protocol:config.protocol,token:config.token,type,payload},config.origin);
  let stopped=false,pending=false,sequence=config.sequence||0,queue=[],composing=false,patching=false,timer=null,scheduled=false,lastStep=performance.now();
  const failure=message=>{stopped=true;queue=[];clearTimeout(timer);platform.stop();send('script-client-error',message);};
  const platform=installDisplayPlatform(item=>pushFeedback([item]),failure);
  function pushFeedback(items){if(stopped)return;queue.push({type:'feedback',target:'n1',key:'',controls:[],feedback:items});flush();}
  function schedule(){clearTimeout(timer);if(!scheduled||stopped||pending||composing||document.hidden||queue.length)return;timer=setTimeout(()=>{queue.push({type:'tick',target:'n1',key:'',controls:[]});flush();},33);}
  const controls=()=>[...document.querySelectorAll('input,textarea,select,option,details')].filter(n=>n.dataset.authorNode).map(node=>{
    const value={key:node.dataset.authorNode};
    if(node.tagName==='DETAILS')value.open=node.open;
    else if(node.tagName==='OPTION')value.selected=node.selected;
    else{value.value=node.value;if(node.tagName==='INPUT')value.checked=node.checked;}
    return value;
  });
  // DOM size is not the event payload size. Send only values changed since
  // the last acknowledged host render, including radio siblings and options.
  const indexControls=values=>new Map(values.map(value=>[value.key,JSON.stringify(value)]));
  const changedControls=(values,baseline)=>values.filter(value=>baseline.get(value.key)!==JSON.stringify(value));
  let controlBaseline=indexControls(config.execution?controls():[]),sentControls=controlBaseline;
  const flush=()=>{
    if(stopped||pending||composing||!queue.length)return;
    const event=queue.shift();
    // A timer patch can replace the clicked text node while this event waits.
    // Keep its captured bubbling path, never retarget to a new unrelated node.
    if(event.ancestors){const target=platform.eventTarget(event.target,event.ancestors);delete event.ancestors;if(!target){flush();schedule();return;}event.target=target;}
    const currentControls=controls();
    event.controls=changedControls(currentControls,controlBaseline);
    if(event.controls.length>128){failure('本次同时修改的表单项超过128个，输入已保留，请刷新后分批修改。');return;}
    sentControls=indexControls(currentControls);
    clearTimeout(timer);const now=performance.now();event.elapsed=Math.min(1000,now-lastStep);lastStep=now;
    event.viewport=platform.viewport();event.layouts=platform.layouts(event.target);
    pending=true;send('author-event',{sequence:++sequence,event});
  };
  function enqueue(event){
    if(!config.execution||stopped||patching||!event.isTrusted)return;
    const target=event.target.closest?.('[data-author-node]')||document.body;if(!target.dataset.authorNode)return;
    const type=event.type==='focusin'?'focus':event.type==='focusout'?'blur':event.type;
    // Opaque sandbox intentionally has no allow-forms. Reproduce only the local
    // submit event in the guest; never enable browser form navigation.
    const submitter=event.target.closest?.('button,input[type="submit"]');
    if(type==='click'&&submitter?.type==='submit'||type==='keydown'&&event.key==='Enter'&&target.tagName==='INPUT')event.preventDefault();
    const value={type,target:target.dataset.authorNode,key:(event.key||'').slice(0,80),controls:[]};
    value.ancestors=[];
    if(['click','pointermove','pointerdown','pointerup','touchmove'].includes(type)){
      for(let node=target;node&&value.ancestors.length<64;node=node.parentElement){
        if(node!==target&&node.dataset.authorNode)value.ancestors.push(node.dataset.authorNode);
        if(['BUTTON','A','INPUT','TEXTAREA','SELECT','FORM'].includes(node.tagName))break;
      }
    }
    if(type.startsWith('pointer')||type==='touchmove'){value.clientX=event.touches?.[0]?.clientX??event.clientX??0;value.clientY=event.touches?.[0]?.clientY??event.clientY??0;}
    const previous=queue.at(-1);
    if(['input','pointermove','touchmove'].includes(type)&&previous?.type===type&&previous.target===value.target)queue[queue.length-1]=value;
    else queue.push(value);
    if(queue.length>32){failure('操作过快，待处理事件超过上限。画面和输入已保留，请刷新预览。');return;}
    flush();
  }
  // Morph only sanitized display nodes, preserving focus, caret and unchanged
  // native animations. Inputs edited while a request is pending take precedence.
  function morph(parent,next){
    const old=[...parent.childNodes],byKey=new Map(old.filter(n=>n.nodeType===1&&n.dataset.authorNode).map(n=>[n.dataset.authorNode,n]));
    let cursor=parent.firstChild;const kept=new Set();
    for(const proposed of [...next.childNodes]){
      const key=proposed.nodeType===1?proposed.dataset.authorNode:null;
      let node=key?byKey.get(key):cursor?.nodeType===proposed.nodeType&&!cursor?.dataset?.authorNode?cursor:null;
      if(!node||node.nodeName!==proposed.nodeName)node=proposed.cloneNode(true);
      if(node!==cursor)parent.insertBefore(node,cursor);
      kept.add(node);
      if(node.nodeType===1){
        for(const attribute of [...node.attributes])if(!proposed.hasAttribute(attribute.name))node.removeAttribute(attribute.name);
        for(const attribute of proposed.attributes)if(node.getAttribute(attribute.name)!==attribute.value)node.setAttribute(attribute.name,attribute.value);
        morph(node,proposed);
        if(['INPUT','TEXTAREA'].includes(node.tagName)&&node.value!==proposed.value)node.value=proposed.value;
        if(node.tagName==='INPUT')node.checked=proposed.checked;
        if(node.tagName==='OPTION')node.selected=proposed.selected;
      }else if(node.nodeValue!==proposed.nodeValue)node.nodeValue=proposed.nodeValue;
      cursor=node.nextSibling;
    }
    for(const node of old)if(!kept.has(node))node.remove();
  }
  window.addEventListener('message',event=>{
    const message=event.data;
    if(event.source!==parent||event.origin!==config.origin||message?.protocol!==config.protocol||message.token!==config.token)return;
    if(message.type==='stop'){stopped=true;pending=false;queue=[];clearTimeout(timer);platform.stop();return;}
    if(message.type==='feedback'&&Array.isArray(message.items)){pushFeedback(message.items);return;}
    if(message.type!=='patch'||!pending||message.sequence!==sequence)return;
    const patch=message.payload;if(!patch||patch.unchanged!==true&&(typeof patch.bodyHtml!=='string'||typeof patch.styleText!=='string'||!Array.isArray(patch.bodyAttributes)))return;
    const preserved=changedControls(controls(),sentControls),focus=document.activeElement,focusKey=focus?.dataset?.authorNode;
    const selection=typeof focus?.selectionStart==='number'?[focus.selectionStart,focus.selectionEnd,focus.selectionDirection]:null;
    patching=true;
    try{
      platform.apply(patch.platform,'before');
      if(!patch.unchanged){
      const template=document.createElement('template');template.innerHTML=patch.bodyHtml;
      morph(document.body,template.content);
      for(const attribute of [...document.body.attributes])document.body.removeAttribute(attribute.name);
      for(const {name,value} of patch.bodyAttributes)document.body.setAttribute(name,value);
      const style=document.head.querySelector('style');if(style.textContent!==patch.styleText)style.textContent=patch.styleText;
      }
      controlBaseline=patch.unchanged?sentControls:indexControls(controls());
      for(const state of preserved){const node=document.querySelector('[data-author-node="'+state.key+'"]');if(node){if('value'in state)node.value=state.value;if('checked'in state)node.checked=state.checked;if('selected'in state)node.selected=state.selected;if('open'in state)node.open=state.open;}}
      if(focusKey){const node=document.querySelector('[data-author-node="'+focusKey+'"]');if(node){node.focus({preventScroll:true});if(selection&&node.setSelectionRange)node.setSelectionRange(...selection);}}
      platform.apply(patch.platform);scheduled=!!patch.platform?.scheduled;
      pending=false;send('patch-applied',sequence);
    }finally{patching=false;}
    flush();schedule();
  });
  document.addEventListener('compositionstart',()=>{composing=true;});
  document.addEventListener('compositionend',()=>{composing=false;flush();schedule();});
  if(config.execution){
    for(const type of ['click','input','change','submit','keydown','keyup','focusin','focusout','toggle','pointermove','pointerdown','pointerup','touchmove'])document.addEventListener(type,enqueue,type==='toggle');
    let resizing=false;window.addEventListener('resize',()=>{if(resizing||stopped)return;resizing=true;setTimeout(()=>{resizing=false;queue.push({type:'resize',target:'n1',key:'',controls:[]});flush();},100);});
    document.addEventListener('visibilitychange',()=>{lastStep=performance.now();if(document.hidden)clearTimeout(timer);else schedule();});
  }
  const content=id=>{
    const element=document.getElementById(id);
    return element?('value' in element?element.value:element.textContent):null;
  };
  if(config.panel){
    let sequence=0,pending=null;
    const copy=event=>{
      const target=event.target.closest?.('[data-panel-copy],[data-panel-copy-from]');
      if(!target||!event.isTrusted||event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
      event.preventDefault();
      const value=target.hasAttribute('data-panel-copy')?target.getAttribute('data-panel-copy'):content(target.getAttribute('data-panel-copy-from'));
      if(typeof value!=='string'||value.length>65536)return;
      pending={id:++sequence,target};send('panel-copy',{id:sequence,text:value});
    };
    document.addEventListener('click',copy);document.addEventListener('keydown',copy);
    window.addEventListener('message',event=>{
      const m=event.data;
      if(event.source!==parent||event.origin!==config.origin||m?.protocol!==config.protocol||m.token!==config.token||m.type!=='panel-copied'||m.payload?.id!==pending?.id)return;
      const target=pending.target;pending=null;
      if(m.payload.ok){target.setAttribute('data-copied','');setTimeout(()=>target.removeAttribute('data-copied'),1600);}
    });
  }
  let last=0;
  document.addEventListener('click',event=>{
    const target=event.target.closest('a,button,[data-rpg-input],[data-rpg-input-from],[data-rpg-copy],[data-rpg-copy-from],[data-rpg-draft]');
    if(!target)return;
    if(target.tagName==='A'&&!target.getAttribute('href')?.startsWith('#'))event.preventDefault();
    if(config.mode!=='opening'||!event.isTrusted)return;
    const now=Date.now();if(now-last<250)return;
    let type,payload;
    if(target.hasAttribute('data-rpg-input')){type='input';payload=target.getAttribute('data-rpg-input');}
    else if(target.hasAttribute('data-rpg-input-from')){type='input';payload=content(target.getAttribute('data-rpg-input-from'));}
    else if(target.hasAttribute('data-rpg-copy')){type='copy';payload=target.getAttribute('data-rpg-copy');}
    else if(target.hasAttribute('data-rpg-copy-from')){type='copy';payload=content(target.getAttribute('data-rpg-copy-from'));}
    else if(target.hasAttribute('data-rpg-draft')){
      const form=document.getElementById(target.getAttribute('data-rpg-draft'));
      if(form?.tagName==='FORM'){
        const values=[...new FormData(form).entries()];
        if(values.length<=128&&values.every(([key,value])=>key.length<=128&&typeof value==='string'&&value.length<=16000)){
          type='draft';payload=JSON.stringify(values,null,2);
        }
      }
    }
    if(type){event.preventDefault();if(typeof payload==='string'&&[...payload].length<=config.inputCharacters){last=now;send(type,payload);}}
  });
  document.addEventListener('submit',event=>event.preventDefault());
  let queued=false;
  const size=()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;send('height',Math.min(6000,Math.max(240,Math.ceil(document.body.getBoundingClientRect().height),document.body.scrollHeight)));});};
  new ResizeObserver(size).observe(document.body);
  window.addEventListener('load',size);
  document.addEventListener('securitypolicyviolation',event=>send('blocked',String(event.effectiveDirective).slice(0,80)));
  send('ready',config.mode);size();
  if(config.execution){platform.apply(config.platform);scheduled=!!config.platform?.scheduled;schedule();}
}

function installSpecialCGZoom(appendedImages) {
  // The host appended exactly this many figures immediately before its own script.
  // Never attach listeners to a lookalike button in author or model markup.
  let figure = document.currentScript.previousElementSibling;
  for (let i = 0; i < appendedImages && figure; i++, figure = figure.previousElementSibling) {
    const trigger = figure.firstElementChild;
    if (figure.tagName !== 'FIGURE' || !trigger?.matches('button.host-cg-magnify')) continue;
    trigger.addEventListener('click', () => {
      const original = trigger.querySelector('img');
      if (!original) return;
      const dialog = document.createElement('dialog');
      dialog.className = 'host-cg-full';
      dialog.setAttribute('aria-label', original.alt || 'CG插图');
      const copy = original.cloneNode(false);
      copy.removeAttribute('style');
      const close = document.createElement('button');
      close.type = 'button'; close.textContent = '关闭';
      close.onclick = () => dialog.close();
      dialog.addEventListener('close', () => { dialog.remove(); trigger.focus(); }, {once:true});
      dialog.append(copy, close); document.body.append(dialog); dialog.showModal();
    });
  }
}
/** The display compiler uses copies. Raw model history is immutable. */
export function compilePresentation({mode,html,css='',authorJs='',resources=[],binding={},origin,token,execution=null,platform=null,suggestions=false,displayProjection=null,cgView=null,panel=false,presentation={scope:'standard',imageMappings:[]}}) {
  if(!['opening','reply'].includes(mode))fail('AUTHORING_PRESENTATION_MODE');
  if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!/^[a-f0-9]{48}$/.test(token))fail('AUTHORING_PRESENTATION_BINDING');
  text(html,'/html');text(css,'/css');text(authorJs,'/authorJs');
  presentation=validatePresentation(presentation);
  if(platform){if(!execution)fail('AUTHORING_PRESENTATION_BINDING');validatePlatform(platform);platform=structuredClone(platform);}
  if(execution&&(mode!=='opening'||!/^[a-f0-9]{64}$/.test(execution.sourceHash)||!/^[a-f0-9]{64}$/.test(execution.stateHash)||!Number.isInteger(execution.sequence)))fail('AUTHORING_PRESENTATION_BINDING');
  if(typeof panel!=='boolean'||panel&&(mode!=='reply'||execution||authorJs))fail('AUTHORING_PRESENTATION_MODE');
  if(cgView&&mode!=='reply')fail('AUTHORING_PRESENTATION_MODE');
  const diagnostics=new Map(),used=new Map(),requested=new Set();
  const imageMappings=new Map(presentation.imageMappings.map(item=>[item.source,item.alias]));
  const mediaMappings=new Map((presentation.mediaMappings||[]).map(item=>[item.source,item.alias]));
  const note=(code,detail='')=>{const key=code+':'+detail;const old=diagnostics.get(key);diagnostics.set(key,{code,detail,count:(old?.count||0)+1});};
  const assets=new Map();
  for(const resource of resources){
    if(!resource||!imageTypes.has(resource.mediaType)&&resource.mediaType!=='audio/mpeg'||!Buffer.isBuffer(resource.bytes)||sha256(resource.bytes)!==resource.sha256||assets.has(resource.alias))fail('AUTHORING_PRESENTATION_RESOURCE');
    assets.set(resource.alias,resource);
  }
  const resolveImage=value=>{
    if(/^data:/i.test(value)){note('RESOURCE_INLINE_IMAGE_BLOCKED');return null;}
    const match=/^asset:\/\/([a-zA-Z0-9][a-zA-Z0-9._-]*)$/.exec(value);
    const alias=match?.[1]||imageMappings.get(value);
    if(!alias){note('RESOURCE_REFERENCE_BLOCKED');return null;}
    requested.add(alias);
    const asset=assets.get(alias);
    if(!asset||!imageTypes.has(asset.mediaType)){note('RESOURCE_MISSING',alias);return null;}
    used.set(asset.alias,{alias:asset.alias,sha256:asset.sha256,mediaType:asset.mediaType});
    return 'data:'+asset.mediaType+';base64,'+asset.bytes.toString('base64');
  };
  const resolveCss=value=>value.replace(/url\(\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s()]*))\s*\)/gi,(_,double,single,bare)=>{
    const reference=double??single??bare;
    if(fragment.test(reference))return 'url("'+reference+'")';
    const resolved=resolveImage(reference);return resolved?'url("'+resolved+'")':'none';
  });
  // Markdown expands only the display copy. parse5 below remains the trust
  // boundary, including raw HTML passed through by Marked.
  const assistance=mode==='reply'&&suggestions===true?projectSuggestions(html,true):null;
  for(const item of assistance?.diagnostics||[])note(item.code,item.detail);
  if(displayProjection&&mode!=='reply')fail('AUTHORING_PRESENTATION_MODE');
  for(const item of displayProjection?.diagnostics||[])note(item.code,item.detail);
  const display=mode==='reply'?markdown.parse(displayProjection?.display??assistance?.display??html):html;
  text(display,'/display');
  const document=parse(display), styles=[];
  let nodes=0;
  function clean(node,depth=0){
    if(++nodes>50000||depth>128)fail('AUTHORING_PRESENTATION_COMPLEX');
    if(node.nodeName==='#comment')return false;
    if(node.tagName){
      if(node.namespaceURI===svgNamespace&&svgElements.has(node.tagName)){
        node.attrs=(node.attrs||[]).flatMap(attribute=>{
          const {name,value}=attribute;
          if(execution&&name==='data-author-node'&&/^n[1-9][0-9]{0,5}$/.test(value))return [attribute];
          if(name==='href'&&(!attribute.namespace||attribute.namespace==='http://www.w3.org/1999/xlink')&&node.tagName==='use'&&fragment.test(value))return [attribute];
          if(attribute.namespace||!svgAttributes.has(name)&&!/^aria-[a-z-]+$/.test(name)){
            note(name.startsWith('on')?'SCRIPT_NOT_EXECUTED':'ATTRIBUTE_REMOVED',name);return [];
          }
          // Presentation attributes can contain only local paint references;
          // SVG cannot load remote documents, images, fonts or executable URLs.
          if(/url\s*\(/i.test(value)&&name!=='style'&&!/^url\(\s*(['"]?)#[a-zA-Z_][a-zA-Z0-9_.:-]*\1\s*\)$/.test(value)){
            note('RESOURCE_REFERENCE_BLOCKED');return [];
          }
          return [{...attribute,value:name==='style'?resolveCss(value):value}];
        });
        node.childNodes=(node.childNodes||[]).filter(child=>clean(child,depth+1));return true;
      }
      if(node.namespaceURI!==htmlNamespace||blocked.has(node.tagName)){
        note(node.tagName==='script'?'SCRIPT_NOT_EXECUTED':'ELEMENT_REMOVED',node.tagName);return false;
      }
      if(node.tagName==='style'){
        styles.push(cssText(resolveCss((node.childNodes||[]).map(n=>n.value||'').join(''))));return false;
      }
      const old=node.attrs||[], kept=[];
      for(const attribute of old){
        const {name,value}=attribute;
        if(name.startsWith('data-author-')&&(!execution||name!=='data-author-node'||!/^n[1-9][0-9]{0,5}$/.test(value)))continue;
        if(attribute.namespace||name.startsWith('on')||['nonce','is','autofocus','srcdoc','formaction','action','target','ping','download','srcset','poster'].includes(name)){
          note(name.startsWith('on')?'SCRIPT_NOT_EXECUTED':'ATTRIBUTE_REMOVED',name);continue;
        }
        if(name==='src'){
          if(node.tagName==='img'){
            const resolved=resolveImage(value);if(resolved)kept.push({name,value:resolved});
            else kept.push({name:'data-resource-missing',value:'true'});
          }else note('RESOURCE_REFERENCE_BLOCKED');
          continue;
        }
        if(name==='href'){
          if(node.tagName==='a'&&value.startsWith('#'))kept.push(attribute);else note('LINK_BLOCKED');
          continue;
        }
        if(name==='type'&&node.tagName==='input'&&!/^(text|number|checkbox|radio|range|color|date|datetime-local|month|week|time|email|tel|url|hidden|button|submit|reset)$/i.test(value)){
          kept.push({name:'type',value:'text'});note('INPUT_TYPE_REPLACED');continue;
        }
        if(name.startsWith('data-rpg-')&&mode!=='opening')continue;
        if(attributes.has(name)||/^aria-[a-z-]+$/.test(name)||/^data-[a-z0-9_-]+$/.test(name))kept.push({name,value:name==='style'?resolveCss(value):value});
      }
      node.attrs=kept;
      if(mode==='reply'&&presentation.scope==='afengy'&&node.tagName==='img'){
        for(const name of ['width','height'])if(!kept.some(a=>a.name===name))kept.push({name,value:'250'});
      }
      if(node.tagName==='img'&&kept.some(a=>a.name==='data-resource-missing')){
        node.tagName='span';node.nodeName='span';
        const alt=old.find(a=>a.name==='alt')?.value||'图片';
        node.childNodes=[{nodeName:'#text',value:'[缺少本地资源：'+alt+']',parentNode:node}];
        node.attrs=node.attrs.filter(a=>!['alt','width','height'].includes(a.name));
      }
    }
    node.childNodes=(node.childNodes||[]).filter(child=>clean(child,depth+1));return true;
  }
  clean(document);
  const root=document.childNodes.find(n=>n.tagName==='html'),body=root?.childNodes.find(n=>n.tagName==='body');
  const bodyAttributes=body?.attrs||[];
  if(authorJs)note('SCRIPT_NOT_EXECUTED','startupJs');
  const base='html{color-scheme:light}body{margin:0;padding:12px;box-sizing:border-box;overflow-wrap:anywhere;font-family:system-ui,sans-serif}img{max-width:100%;height:auto}input,textarea,select,button{max-width:100%;box-sizing:border-box}pre{white-space:pre-wrap}table{max-width:100%}[data-resource-missing]{display:inline-block;border:1px dashed #9a6b00;padding:8px;color:#795300;background:#fff8e6}';
  if(platform)for(const command of platform.effects){
    if(command[0]==='reflow')command[3]=resolveCss(command[3]);
    if(command[0]==='media'&&command[2]==='play'){
      const alias=/^asset:\/\/([a-zA-Z0-9][a-zA-Z0-9._-]*)$/.exec(command[3])?.[1]||mediaMappings.get(command[3]);
      if(alias)requested.add(alias);const asset=assets.get(alias);
      if(!asset||asset.mediaType!=='audio/mpeg'){note('AUDIO_RESOURCE_MISSING',alias||'');command[3]='';}
      else{used.set(alias,{alias,sha256:asset.sha256,mediaType:asset.mediaType});command[3]='data:audio/mpeg;base64,'+asset.bytes.toString('base64');}
    }
  }
  let script='('+bootstrap.toString()+')('+jsonScript({protocol:PRESENTATION.format,token,origin,mode,panel,inputCharacters:PRESENTATION.inputCharacters,execution:!!execution,sequence:execution?.sequence||0,platform})+',('+installDisplayPlatform.toString()+'));';
  const nonce=randomBytes(18).toString('base64');
  const markdownBase='.markdown-body{line-height:1.6}.markdown-body p{margin:0 0 1em}.markdown-body pre{padding:12px;overflow:auto;background:#f6f8fa;border-radius:6px}.markdown-body code{font-family:ui-monospace,monospace}.markdown-body table{border-collapse:collapse}.markdown-body td,.markdown-body th{padding:6px 10px;border:1px solid #d0d7de}.markdown-body blockquote{margin-left:0;padding-left:1em;border-left:3px solid #d0d7de}.markdown-body svg{vertical-align:middle}';
  const scopeBase=presentation.scope==='afengy'?'#installedBuiltInCss{overflow:hidden}#installedBuiltInCss *,#installedBuiltInCss *::before,#installedBuiltInCss *::after{box-sizing:border-box}#customized-answer-content{padding:12px 16px;font-size:14px;box-sizing:border-box;width:100%;position:relative;display:inline-block}#customized-answer-content .markdown-body{line-height:1.5}#customized-answer-content .markdown-body img{display:block;height:auto;vertical-align:middle;border:0;border-radius:10px;margin:8px 0;box-shadow:0 2px 4px -2px #1018280f,0 4px 8px -2px #1018281a}':'';
  let styleText=base+'\n'+(mode==='reply'?markdownBase+scopeBase:'')+'\n'+cssText(resolveCss(css))+'\n'+styles.join('\n');
  let bodyHtml=body?serialize(body):'';
  if(mode==='reply'){
    // Platform compatibility roots live only inside the opaque display frame.
    // No card ID, card-specific class, prompt or image list enters this adapter.
    bodyHtml='<div class="markdown-body"'+(presentation.scope==='afengy'?' id="customized-answer-content-markdown"':'')+'>'+bodyHtml+'</div>';
    if(presentation.scope==='afengy')bodyHtml='<div id="installedBuiltInCss"><div id="ai-chat-answer"><div id="customized-answer"><div id="customized-answer-container" class="chat-answer-container"><div id="customized-answer-content-container"><div id="customized-answer-content" class="relative inline-block bg-gray-100/90">'+bodyHtml+'</div></div></div></div></div></div>';
  }
  if(cgView){
    const imageUrl=image=>{
      const resource=assets.get(image.alias);
      if(resource&&(resource.sha256!==image.sha256||resource.mediaType!==image.mediaType))fail('AUTHORING_CG_REPLAY_RESOURCE');
      return resolveImage('asset://'+image.alias);
    };
    const background=cgView.background?imageUrl(cgView.background):null;
    if(background)styleText+='\nbody{background-image:url("'+background+'")!important;background-size:cover!important;background-position:center!important;background-attachment:fixed!important}';
    // Platform-owned illustration appendix. It never re-enters Markdown,
    // display replacements, keyword matching, or the stored raw reply.
    bodyHtml+=cgView.plan.chat.map(image=>{
      const url=imageUrl(image);
      const picture=url?'<img src="'+url+'" alt="'+escape(image.label||'CG插图')+'" style="display:block!important;position:static!important;width:auto!important;max-width:100%!important;height:auto!important;max-height:70vh!important;object-fit:contain!important;margin:auto!important">':'<span data-resource-missing>缺少 CG 本地资源</span>';
      const content=url&&image.special?'<button type="button" class="host-cg-magnify" aria-label="放大CG插图">'+picture+'<span aria-hidden="true"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="10" cy="10" r="7"/><path d="M10 7v6m-3-3h6m2.5 5.5L22 22"/></svg></span></button>':picture;
      return '<figure data-cg-entry="'+escape(image.entryId)+'" style="display:block!important;position:relative!important;margin:16px 0!important;text-align:center!important">'+content+'</figure>';
    }).join('');
  }
  if(cgView?.plan.chat.some(image=>image.special)){
    script+='('+installSpecialCGZoom.toString()+')('+cgView.plan.chat.length+');';
    styleText+='\nfigure[data-cg-entry] > .host-cg-magnify{border:0;padding:0;position:relative;max-width:100%;background:transparent;cursor:zoom-in}figure[data-cg-entry] > .host-cg-magnify > span{position:absolute;inset:0;display:grid;place-items:center;color:white;background:#18182660;font:36px sans-serif;opacity:0}figure[data-cg-entry] > .host-cg-magnify:is(:hover,:focus-visible) > span{opacity:1}.host-cg-full{box-sizing:border-box;max-width:96vw;max-height:96vh;border:0;border-radius:10px;padding:9px;background:#20202a}.host-cg-full::backdrop{background:#13121bd9}.host-cg-full img{display:block!important;width:auto!important;height:auto!important;max-width:88vw!important;max-height:81vh!important;object-fit:contain!important}.host-cg-full button{display:block;margin:6px auto 0;color:white;background:#424151;border:0;border-radius:5px;padding:5px 16px}';
  }
  const frame='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>作品隔离预览</title><style>'+styleText+'</style></head><body'+bodyAttributes.map(a=>' '+a.name+'="'+escape(a.value)+'"').join('')+'>'+bodyHtml+'<script nonce="'+nonce+'">'+script+'</script></body></html>';
  if(Buffer.byteLength(frame)>PRESENTATION.frameBytes)fail('AUTHORING_PRESENTATION_SIZE','/frame');
  const manifest={format:PRESENTATION.format,mode,binding,sourceHash:execution?.sourceHash||sha256(canonical({html,css,authorJs})),presentationHash:sha256(canonical(presentation)),renderHash:sha256(canonical({bodyHtml,bodyAttributes,styleText})),requestedAliases:[...requested],resources:[...used.values()],diagnostics:[...diagnostics.values()],authorScriptsExecuted:!!execution,...(execution?{scriptStateHash:execution.stateHash,sequence:execution.sequence}:{})};
  if(cgView)manifest.cg=cgView;
  if(displayProjection)manifest.displayRules=displayProjection.report;
  if(assistance){manifest.suggestions=assistance.choices;manifest.assistanceHash=sha256(canonical({sourceHash:manifest.sourceHash,enabled:true,choices:assistance.choices}));}
  const csp="default-src 'none'; sandbox allow-scripts; script-src 'nonce-"+nonce+"'; script-src-attr 'none'; style-src 'unsafe-inline'; img-src data:; media-src "+(execution?'data:':"'none'")+"; connect-src 'none'; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors "+origin;
  return {frame,csp,manifest,patch:{bodyHtml,bodyAttributes,styleText,...(platform?{platform}:{})}};
}

/** Expiring, bounded, nonpersistent snapshots; never silently evict a live frame. */
export function createPresentationCache({now=Date.now}={}){
  const records=new Map();let bytes=0;
  const prune=()=>{for(const [id,item] of records)if(item.expiresAt<=now()){records.delete(id);bytes-=item.bytes;}};
  return {
    put(compiled,token){prune();const size=Buffer.byteLength(compiled.frame);if(records.has(token)||records.size>=PRESENTATION.count||bytes+size>PRESENTATION.cacheBytes)fail('AUTHORING_PRESENTATION_CAPACITY');const expiresAt=now()+PRESENTATION.ttl;records.set(token,{...compiled,bytes:size,expiresAt});bytes+=size;return {token,expiresAt,manifest:compiled.manifest};},
    get(token){prune();const record=records.get(token);if(!record)fail('AUTHORING_PRESENTATION_NOT_FOUND');return record;},
    replace(token,compiled){prune();const record=records.get(token);if(!record)fail('AUTHORING_PRESENTATION_NOT_FOUND');const size=Buffer.byteLength(compiled.frame);if(bytes-record.bytes+size>PRESENTATION.cacheBytes)fail('AUTHORING_PRESENTATION_CAPACITY');records.set(token,{...compiled,bytes:size,expiresAt:record.expiresAt});bytes+=size-record.bytes;},
    release(token){const record=records.get(token);if(record){records.delete(token);bytes-=record.bytes;}return !!record;},
  };
}

// HTML parsing is bounded off the request thread. No submitted JavaScript is run
// here, and a pathological reply cannot monopolize the authoring HTTP service.
export async function compilePresentationBounded(input,{timeoutMs=2500}={}){
  const compiler=createPresentationCompiler();try{return await compiler.compile(input,{timeoutMs});}finally{compiler.close();}
}
// Retain the bounded parser Worker for a live opening, rather than starting a
// new parser process for every timer frame. Each call still has its own timeout.
export function createPresentationCompiler(){
  const worker=new Worker(new URL(import.meta.url),{workerData:{job:'authoring-presentation-session'},resourceLimits:{maxOldGenerationSizeMb:96,stackSizeMb:4}});
  let closed=false,pending=null;
  const finish=(error,result)=>{if(!pending)return;const current=pending;pending=null;clearTimeout(current.timer);error?current.reject(error):current.resolve(result);};
  const close=()=>{if(closed)return;closed=true;finish({code:'AUTHORING_PRESENTATION_CLOSED'});worker.terminate().catch(()=>{});};
  worker.on('message',message=>finish(message.error,message.result));
  worker.on('error',()=>{finish({code:'AUTHORING_PRESENTATION_RESOURCE_LIMIT'});close();});
  worker.on('exit',()=>{finish({code:'AUTHORING_PRESENTATION_RESOURCE_LIMIT'});closed=true;});
  return {close,async compile(input,{timeoutMs=2500}={}){
  if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>10000)fail('AUTHORING_PRESENTATION_OPTIONS');
  text(input.html,'/html');text(input.css||'','/css');text(input.authorJs||'','/authorJs');
  if((input.resources||[]).reduce((sum,item)=>sum+item.bytes.length,0)>PRESENTATION.frameBytes)fail('AUTHORING_PRESENTATION_SIZE','/resources');
  if(closed)fail('AUTHORING_PRESENTATION_CLOSED');if(pending)fail('AUTHORING_PRESENTATION_BUSY');
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{finish({code:'AUTHORING_PRESENTATION_TIMEOUT'});close();},timeoutMs);
    pending={resolve,reject,timer};worker.postMessage({input});
  }).catch(error=>fail(error.code||'AUTHORING_PRESENTATION_RESOURCE_LIMIT',error.path||''));
  }};
}
if(!isMainThread&&workerData?.job==='authoring-presentation-session')parentPort.on('message',({input})=>{
  try{
    input.resources=(input.resources||[]).map(item=>({...item,bytes:Buffer.from(item.bytes)}));
    parentPort.postMessage({result:compilePresentation(input)});
  }catch(error){parentPort.postMessage({error:{code:error.code||'AUTHORING_PRESENTATION_INVALID',path:error.path||''}});}
});
