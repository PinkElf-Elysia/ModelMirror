// Presentation copies only. Never use this result as saved history or a model message.
import {parseFragment, serialize} from 'parse5';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import Markdown from 'react-markdown';

export const presentationVersion = 'rpg-card-presentation/2';
export const commentarySource = 'https://catai.wiki/31a557a3-828e-44e7-bcc7-972771e4bb00/w=3000';
export const commentaryAsset = './assets/god-commentary-v1.webp';
export const commentaryAssets = Object.freeze({
  "https://catai.wiki/5a7a3217-dc6e-4b63-36d8-1c4b11a28d00/w=3000": "./assets/god-commentary-alt-v1.jpg",
  "https://catai.wiki/287eba7e-830e-4ba3-032a-f55393131900/w=3000": "./assets/god-commentary-287eba7e-830e-4ba3-032a-f55393131900.jpg",
  "https://catai.wiki/1fc2661a-a9c2-4330-8a34-51959bda5c00/w=3000": "./assets/god-commentary-1fc2661a-a9c2-4330-8a34-51959bda5c00.jpg",
  "https://catai.wiki/31a557a3-828e-44e7-bcc7-972771e4bb00/w=3000": "./assets/god-commentary-v1.webp",
  "https://catai.wiki/61e70fb9-9a81-4274-56dc-9753b573e000/w=3000": "./assets/god-commentary-61e70fb9-9a81-4274-56dc-9753b573e000.jpg",
  "https://catai.wiki/2f3c5ff4-ee3e-4ea8-03b9-27ff5cb00d00/w=3000": "./assets/god-commentary-2f3c5ff4-ee3e-4ea8-03b9-27ff5cb00d00.jpg",
  "https://catai.wiki/5824e5aa-ea5d-4396-32d9-8e3db9095700/w=3000": "./assets/god-commentary-5824e5aa-ea5d-4396-32d9-8e3db9095700.jpg",
  "https://catai.wiki/6e822f82-cf5c-448b-b976-046800a7b100/w=3000": "./assets/god-commentary-6e822f82-cf5c-448b-b976-046800a7b100.jpg",
  "https://catai.wiki/98cbbf81-7e73-484d-a713-1b04c0917b00/w=3000": "./assets/god-commentary-98cbbf81-7e73-484d-a713-1b04c0917b00.jpg",
  "https://catai.wiki/9c431025-e8ca-471e-8edf-d82eb7a83400/w=3000": "./assets/god-commentary-9c431025-e8ca-471e-8edf-d82eb7a83400.jpg",
  "https://catai.wiki/c2157402-2346-4fc8-0808-6ab5a1809b00/w=3000": "./assets/god-commentary-c2157402-2346-4fc8-0808-6ab5a1809b00.jpg",
  "https://catai.wiki/ab5abbc7-a2ec-45f3-1efe-486b2efe1300/w=3000": "./assets/god-commentary-ab5abbc7-a2ec-45f3-1efe-486b2efe1300.jpg",
  "https://catai.wiki/c9a9a34f-9965-4314-146c-7a48fb5c5100/w=3000": "./assets/god-commentary-c9a9a34f-9965-4314-146c-7a48fb5c5100.jpg",
  "https://catai.wiki/4256c515-785f-420f-d777-683cf95bb900/w=3000": "./assets/god-commentary-4256c515-785f-420f-d777-683cf95bb900.jpg",
  "https://catai.wiki/5939d0f0-163b-4c27-2a7a-b05419797900/w=3000": "./assets/god-commentary-5939d0f0-163b-4c27-2a7a-b05419797900.jpg",
  "https://catai.wiki/280a79e9-0e26-46ad-3f71-028cfa9b5c00/w=3000": "./assets/god-commentary-280a79e9-0e26-46ad-3f71-028cfa9b5c00.jpg",
  "https://catai.wiki/8f21b0cc-67e2-42ab-95bc-e78dd37fdf00/w=3000": "./assets/god-commentary-8f21b0cc-67e2-42ab-95bc-e78dd37fdf00.jpg"
});
const htmlNamespace = 'http://www.w3.org/1999/xhtml';
const tags = new Set('p br hr div span strong b em i u s del blockquote pre code ul ol li details summary table thead tbody tfoot tr th td h1 h2 h3 h4 h5 h6 ruby rt rp'.split(' '));
const drop = new Set('script style iframe object embed svg math template form textarea select link meta base audio video source picture canvas noscript thinking think'.split(' '));
const classes = {
  god: new Set('page page-top status-box status-summary status-details status-item summary-icon summary-name a b label value page-main scene-wrapper flip-button-wrapper flip-toggle text-front text-back flip-card flip-card-inner flip-card-front flip-card-back page-bottom live-container status-info status-row topic separator danmu-area danmu-track aa bb cc dd title interview-wrap interview-card interview-header interview-avatar interview-info interview-name interview-status interview-qa qa-1 qa-2 qa-3 qa-question qa-answer speech'.split(' ')),
  earth: new Set('situation-panel info-row label-icon info-text main-text char-card char-header char-name char-tags tag char-section section-title info-grid grid-item key val appearance-text personality-tags motivation relation-list relation-item relation-type finance-grid finance-item finance-label finance-val status-bars stat-bar stat-label bar fill health hunger fatigue stat-num condition-text inventory-grid inv-category skill-list certificate-list vehicle-info npc-container npc-card npc-header npc-name npc-tags npc-body npc-row icon favor npc-collapsible collapsible-trigger collapsible-content npc-divider env-panel env-sense sense-icon sense-content env-crowd env-interact social-panel social-item social-label social-value memory-core memory-section section-header note anchor-list anchor-item aid atype divider ltm-scroll stm-scroll stm-card sid stime sscene sfact state-info entity-scroll entity-card entity-name entity-status open-scroll open-card oid otime ocontent ostatus close-scroll close-card cid ctime ccontent action-panel action-item'.split(' ')),
};
const escape = s => s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const text = value => ({nodeName:'#text',value});
const attr = (n,k) => n.attrs?.find(a=>a.name===k)?.value;
const has = (n,c) => (attr(n,'class')||'').split(/\s+/).includes(c);
const element = (tag, attrs=[], childNodes=[]) => ({nodeName:tag,tagName:tag,namespaceURI:htmlNamespace,attrs,childNodes});
const a = (name,value) => ({name,value});
// Additional selectors verified in the author's visible stylesheet during R2.
for(const c of 'occult-section occult-grid occult-item occult-key occult-val spell-list artifact-list spell-item artifact-item spell-name artifact-name spell-level artifact-quality spell-effect artifact-effect spell-cost karma-info memory-note anchor-section ltm-section stm-section state-section entity-section open-section close-section ltm-list stm-list entity-list open-list close-list ltm-item stm-item entity-item open-item close-item anchor-id ltm-id stm-id open-id close-id anchor-type ltm-scene stm-scene open-time close-time anchor-fact ltm-fact stm-fact open-content close-content open-status ltm-time stm-time memory-divider'.split(' '))classes.earth.add(c);
const inlineTags = new Set('span strong b em i u s del br code ruby rt rp img'.split(' '));

// Protect fenced examples before HTML parsing; their markup remains literal text.
// This covers CommonMark backtick/tilde fences (including an unfinished last fence).
function protectFences(raw) {
  // Existing HTML code/raw-text blocks are already literal (or will be removed).
  // Do not reinterpret backticks inside them as a second Markdown document.
  const protectedRanges=[];
  const queue=[parseFragment(raw,{sourceCodeLocationInfo:true})];
  while(queue.length) {
    const n=queue.pop(), loc=n.sourceCodeLocation;
    if(loc && ['pre','code','script','style','textarea'].includes(n.tagName))protectedRanges.push([loc.startOffset,loc.endOffset]);
    else queue.push(...(n.childNodes||[]));
  }
  let offset=0;
  const offsets=[],lines=raw.split('\n').map(line=>{offsets.push(offset);offset+=line.length+1;return line.replace(/\r$/,'');});
  const out=[];
  for(let i=0;i<lines.length;i++) {
    if(protectedRanges.some(([start,end])=>offsets[i]>=start&&offsets[i]<end)) {out.push(lines[i]);continue;}
    const m=lines[i].match(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/);
    if(!m || (m[1][0]==='`' && m[2].includes('`'))) {out.push(lines[i]);continue;}
    const body=[]; const close=new RegExp('^ {0,3}'+m[1][0]+'{'+m[1].length+',}[ \\t]*$');
    while(++i<lines.length && !close.test(lines[i])) body.push(lines[i]);
    const language=m[2].trim().split(/\s+/)[0];
    const cls=/^[a-zA-Z0-9_-]{1,32}$/.test(language)?' class="language-'+language+'"':'';
    out.push('<pre><code'+cls+'>'+escape(body.join('\n')+(body.length?'\n':''))+'</code></pre>');
  }
  return out.join('\n');
}

function prose(value) {
  return parseFragment(renderToStaticMarkup(createElement(Markdown, {
    components:{img:({alt})=>createElement('span',null,alt||'图片未加载'),a:({children})=>createElement('span',null,children)},
    urlTransform:()=>'', children:value,
  }))).childNodes;
}

// This author protocol is data inside an otherwise inert HTML comment. Never
// expose arbitrary comments, thinking blocks, or interpret suggestion text as HTML.
function suggestionBlock(value) {
  const match=value.match(/^\s*<suggested_questions>\s*\n([\s\S]*?)\n\s*<\/suggested_questions>\s*$/);
  if(!match||value.length>12000)return null;
  const lines=match[1].split(/\r?\n/).filter(s=>s.trim());
  if(!lines.length||lines.length>6||lines.some(s=>!/^\s*-\s+\S/.test(s)))return null;
  return element('details',[],[element('summary',[],[text('剧情辅助')]),element('ul',[],lines.map(s=>element('li',[],[text(s.replace(/^\s*-\s+/,''))])))]);
}

function preserveSoftBreaks(parent,inParagraph=false) {
  if(['pre','code'].includes(parent.tagName))return;
  const prose=inParagraph||parent.tagName==='p';
  parent.childNodes=(parent.childNodes||[]).flatMap(n=>{
    if(prose&&n.nodeName==='#text')return n.value.split('\n').flatMap((part,i)=>i?[element('br'),text(part)]:[text(part)]);
    preserveSoftBreaks(n,prose);return [n];
  });
  for(const n of parent.childNodes)n.parentNode=parent;
}

function safeStyle(value, n) {
  const kept=[];
  for(const declaration of value.split(';')) {
    const i=declaration.indexOf(':'); if(i<0)continue;
    const key=declaration.slice(0,i).trim().toLowerCase(), val=declaration.slice(i+1).trim();
    if(['color','background-color','border-color'].includes(key) && /^(?:#[0-9a-f]{3,8}|[a-z]{1,24})$/i.test(val))kept.push(key+':'+val);
    if(key==='width' && has(n,'fill') && /^(?:100|\d{1,2})(?:\.\d{1,2})?%$/.test(val) && parseFloat(val)<=100)kept.push(key+':'+val);
  }
  return kept.join(';');
}

/** Host chooses the profile. Raw output cannot select another card or asset URL. */
export function cardPresentation(raw, profile) {
  if(!Object.hasOwn(classes,profile))throw new TypeError('UNKNOWN_PRESENTATION_PROFILE');
  if(typeof raw!=='string')throw new TypeError('INVALID_PRESENTATION_SOURCE');
  const root=parseFragment(protectFences(raw));
  const report={removedElements:0,removedAttributes:0,unavailableImages:0};
  let marker='RPGINLINEPLACEHOLDER';
  while(raw.includes(marker))marker+='X';
  // Parse each contiguous prose run once, so inline HTML does not split a paragraph.
  function markdownRuns(parent, depth, force) {
    const result=[];let run=[];
    const flush=()=>{
      if(!run.length)return;
      const literals=run.filter(n=>n.nodeName==='#text').map(n=>n.value).join('');
      if(!literals.trim()||(!force&&!/\n\s*\n|(?:^|\n)\s*(?:---+|\*\*\*+|#{1,6} |[-*] |\d+\. )/.test(literals))) {result.push(...run);run=[];return;}
      const slots=[];
      const source=run.map(n=>n.nodeName==='#text'?n.value:marker+(slots.push(n)-1)+'END').join('');
      const fragment={childNodes:prose(source)};
      if(profile==='god')preserveSoftBreaks(fragment);
      clean(fragment,[],depth+1,false);
      const restore=p=>{
        p.childNodes=(p.childNodes||[]).flatMap(n=>{
          if(n.nodeName!=='#text'){restore(n);return [n];}
          const parts=[];let start=0;
          for(const m of n.value.matchAll(new RegExp(marker+'(\\d+)END','g'))) {
            parts.push(text(n.value.slice(start,m.index)),slots[Number(m[1])]);start=m.index+m[0].length;
          }
          parts.push(text(n.value.slice(start)));return parts.filter(Boolean);
        });
        for(const n of p.childNodes)n.parentNode=p;
      };
      restore(fragment);result.push(...fragment.childNodes);run=[];
    };
    for(const n of parent.childNodes) {
      if(n.nodeName==='#text'||inlineTags.has(n.tagName))run.push(n);
      else {flush();result.push(n);}
    }
    flush();parent.childNodes=result;
  }
  function clean(parent, ancestors=[], depth=0, markdown=true) {
    // Reject excessive nesting explicitly; never silently truncate source text.
    if(depth>160)throw new RangeError('PRESENTATION_DEPTH_LIMIT');
    const result=[];
    for(const n of parent.childNodes||[]) {
      if(n.nodeName==='#comment') {
        const suggestions=profile==='god'&&markdown?suggestionBlock(n.data):null;
        if(suggestions)result.push(suggestions);
        continue;
      }
      if(n.nodeName==='#text') {
        result.push(n);
        continue;
      }
      if(n.namespaceURI!==htmlNamespace||drop.has(n.tagName)) {report.removedElements++;continue;}
      const originalClass=attr(n,'class')||'';
      if(n.tagName==='img') {
        if(profile==='god' && Object.hasOwn(commentaryAssets,attr(n,'src')) && ancestors.some(p=>has(p,'aa'))) {
          result.push(element('img',[a('src',commentaryAssets[attr(n,'src')]),a('alt','吐槽角色，点击展开或收起吐槽'),a('loading','lazy'),a('decoding','async'),a('referrerpolicy','no-referrer')]));
        } else {report.unavailableImages++;result.push(element('span',[],[text(attr(n,'alt')||'图片未加载')]));}
        continue;
      }
      if(n.tagName==='input') {
        const flip=has(n,'flip-toggle') && parent.tagName==='label' && has(parent,'flip-button-wrapper');
        const aside=has(n,'bb') && parent.tagName==='label' && ancestors.some(p=>has(p,'aa'));
        if(profile==='god' && attr(n,'type')==='checkbox' && (flip||aside)) {
          result.push(element('input',[a('type','checkbox'),a('class',flip?'flip-toggle':'bb'),a('aria-label',flip?'切换宏观与微观视角':'展开或收起吐槽'),...(attr(n,'checked')!==undefined?[a('checked','')]:[])]));
        } else report.removedElements++;
        continue;
      }
      const isProse=profile==='god' && ['macroscopic','microscopic'].includes(n.tagName);
      clean(n,[...ancestors,parent],depth+1,markdown&&!['pre','code'].includes(n.tagName));
      if(profile==='earth' && n.tagName==='say') {
        const role=attr(n,'role');n.tagName=n.nodeName='span';
        n.attrs=['hero','male','female'].includes(role)?[a('class','rpg-say-'+role)]:[];
        result.push(n);continue;
      }
      if(isProse) {n.tagName=n.nodeName='div';n.attrs=[a('class','rpg-narrative')];result.push(n);continue;}
      if(!tags.has(n.tagName) && !(profile==='god' && ['main','label'].includes(n.tagName))) {result.push(...n.childNodes);continue;}
      const attributes=[];
      for(const item of n.attrs||[]) {
        if(item.name==='class') {
          const tokens=originalClass.split(/\s+/).filter(c=>classes[profile].has(c)||(n.tagName==='code'&&/^language-[a-zA-Z0-9_-]{1,32}$/.test(c)));
          if(tokens.length)attributes.push(a('class',[...new Set(tokens)].join(' ')));
        } else if(item.name==='style') {const val=safeStyle(item.value,n);if(val)attributes.push(a('style',val));}
        else if(item.name==='open'&&n.tagName==='details')attributes.push(a('open',''));
        else if(['colspan','rowspan'].includes(item.name)&&['td','th'].includes(n.tagName)&&/^[1-9]\d?$/.test(item.value))attributes.push(item);
        else report.removedAttributes++;
      }
      n.attrs=attributes; result.push(n);
    }
    parent.childNodes=result;
    if(markdown && [undefined,'div','details','macroscopic','microscopic'].includes(parent.tagName)) {
      markdownRuns(parent,depth,parent.tagName===undefined||['macroscopic','microscopic'].includes(parent.tagName)||has(parent,'main-text'));
    }
    for(const n of parent.childNodes)n.parentNode=parent;
  }
  clean(root);
  return {html:serialize(root),profile,version:presentationVersion,report};
}

/** A host-owned scope: no model-provided class/attribute can manufacture it. */
export function cardDisplay(raw,profile) {
  if(!Object.hasOwn(classes,profile))throw new TypeError('UNKNOWN_PRESENTATION_PROFILE');
  try {return '<section class="card-output" data-profile="'+profile+'">'+cardPresentation(raw,profile).html+'</section>';}
  catch {return '<p class="presentation-error">此回复暂时无法展示，请使用下方“原文”查看保存的内容。</p>';}
}
