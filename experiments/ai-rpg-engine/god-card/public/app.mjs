import {fields,features,featureOrder,eras,powers,persons,lengths,newSetup,blankCharacter,defaultFeatures,featureValue,cycleFeature,setupBlocks,serializeSetup,restoreSetup} from '../lib/setup.mjs';
const $=id=>document.getElementById(id);
const key='modelmirror:god-card:setup:v1';
let state=newSetup(), writable=true;
try{const raw=localStorage.getItem(key);if(raw)state=restoreSetup(raw);}catch{writable=false;$('draftStatus').textContent='现有草稿无法读取，已保留原数据；当前修改暂不保存';}
const el=(tag,className,text)=>{const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e;};
const button=(text,fn,className='quiet')=>{const b=el('button',className,text);b.type='button';b.addEventListener('click',fn);return b;};
let toastTimer;
function toast(text){clearTimeout(toastTimer);$('toast').textContent=text;$('toast').classList.add('visible');toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3500);}
async function copy(text){try{await navigator.clipboard.writeText(text);toast('📜 天书已复制，可以直接粘贴使用');}catch{toast('复制未成功，请选中天书文字手动复制');}}
function persist(){if(!writable)return;try{localStorage.setItem(key,JSON.stringify(state));$('draftStatus').textContent='草稿已保存在此浏览器';}catch{$('draftStatus').textContent='草稿保存失败，请复制天书保留内容';}}
function changed(){renderBible();refreshButtons();persist();}
const sections=new Map();
function section(id,ordinal,title,subtitle){
 const root=el('section','sec');root.id=id;
 const head=button('',()=>setOpen(id,!root.classList.contains('open')),'sec-head');
 head.setAttribute('aria-expanded','false');head.setAttribute('aria-controls',id+'-body');
 head.append(el('span','ordinal',ordinal));
 const copy=el('span','heading-copy');copy.append(el('b','',title),el('small','',subtitle));head.append(copy,el('span','arrow','▾'));
 const body=el('div','sec-body');body.id=id+'-body';body.inert=true;body.setAttribute('aria-hidden','true');
 const clip=el('div','sec-clip'),content=el('div','sec-content');clip.append(content);body.append(clip);root.append(head,body);$('sections').append(root);
 sections.set(id,{root,head,body,content});return content;
}
function setOpen(id,open){const s=sections.get(id);s.root.classList.toggle('open',open);s.head.setAttribute('aria-expanded',String(open));s.body.inert=!open;s.body.setAttribute('aria-hidden',String(!open));}
function tile([icon,name,desc],fn){const b=button('',fn,'tile');b.append(el('span','icon',icon),el('b','',name));if(desc)b.append(el('small','',desc));return b;}
powers.forEach(p=>$('powers').append(tile(p,()=>toast(p[0]+' 你动了一个念头……全世界的媒体都在报道'))));
'天地玄黄宇宙洪荒日月盈昃辰宿列张'.split('').forEach((r,i)=>{const s=el('span','',r);s.style.setProperty('--angle',i*22.5+'deg');$('runes').append(s);});
$('eye').addEventListener('click',()=>toast('👁️ 某直播间弹幕突然刷屏："主播身后那个人是谁？"'));
$('music').addEventListener('click',()=>toast('暂缺原站音频素材，音乐尚未接入'));
const news=['全球多地观测到不明金色光柱，官方暂无回应','某地出现神秘青年，所到之处皆有异象'];
let newsIndex=0;
setInterval(()=>{if(document.hidden)return;$('newsText').textContent=news[++newsIndex%news.length];},14000);

const manual=section('manual','I','神谕手册','小功能 · 降低消耗 · 自定义配置');
const tabs=el('div','tile-grid');manual.append(tabs);
const panels=[];
function manualPanel(name,icon,sub){
 const panel=el('div','manual-content');panel.hidden=true;panel.id='manual-'+panels.length;
 const b=tile([icon,name,sub],()=>{const open=panel.hidden;panels.forEach(([p,t])=>{p.hidden=true;t.setAttribute('aria-expanded','false');});panel.hidden=!open;b.setAttribute('aria-expanded',String(open));});
 b.setAttribute('aria-expanded','false');b.setAttribute('aria-controls',panel.id);panels.push([panel,b]);tabs.append(b);manual.append(panel);return panel;
}
const functions=manualPanel('小功能','⚙️','十一道神通');
functions.append(el('p','','💡 在对话里直接发送下面的指令就能切换功能，点指令可以复制。'));
for(const k of featureOrder){
 functions.append(el('h4','',k),el('p','',features.find(f=>f[0]===k)?.[2]??(k==='人称视角'?'选择故事的叙述人称。':'选择每次回复的长度。')));
 const commands=k==='人称视角'?persons.map(p=>'改成'+p):k==='回复长度'?lengths.map(l=>'回复长度'+l):['开启','关闭'].map(v=>v+(k==='画面特写'?'特写':k));
 commands.forEach(c=>functions.append(button(c,()=>copy(c),'command')));
}
const costs=manualPanel('降低消耗','💎','节约神力');
const list=el('ol');
['右上角调低记忆范围，例如调到4或6','更换低系数模型，回复质量请自行测试','关闭用不到的功能，例如「美化」','经常把长期总结区里的内容挪到自定义配置的提示词里','减少使用的MOD'].forEach(t=>list.append(el('li','',t)));
costs.append(list,el('p','manual-note','以上为作者原站使用说明；本页整理天书，模型调用由外层会话负责。'));
const config=manualPanel('自定义配置','📐','创世法典');
config.append(el('p','','📍 位置在右上角，可以填入你自定义的设定和故事进程。'),el('p','','⚠️ 如果创建新对话，一定要检查配置里有没有之前遗留的设定！'));
for(const [t,v] of [
 ['【前置词 / 后置词】','你填在这里的内容，每次都会附加在你发给 AI 的消息的前面 / 后面。'],
 ['【主提示词】','主要用来填写设定，大部分设定只需要填在这里，例如你的个人信息、故事的发展进程、重要事件。'],
 ['【世界书】','只有对话中出现了你设置的关键词，AI 才会读取你写在里面的设定。这个功能非常灵活，可以用来节省积分。本作品的所有功能都是通过世界书触发的。']
]){config.append(el('h4','',t),el('p','',v));if(t==='【前置词 / 后置词】')config.append(el('p','','例如输出格式总是出错，可以在后置词里写：'),button('[注意遵守输出格式]',()=>copy('[注意遵守输出格式]'),'command'));}
config.append(el('p','manual-note','作者原站说明。此处不额外装配任何自定义提示词或世界书规则。'));
function characterForm(c,scope){
 const grid=el('div','form-grid');
 for(const [i,k] of fields.entries()){
  const field=el('div','field'+(i>=4?' wide':''));
  if(k==='性别'){
   const label=el('span','',k);label.id=scope+'-gender-label';field.append(label);
   const seg=el('div','segments');seg.setAttribute('role','group');seg.setAttribute('aria-labelledby',label.id);
   ['男','女'].forEach((sex,j)=>{const b=button((j?'♀ ':'♂ ')+sex,()=>{c[k]=c[k]===sex?'':sex;seg.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b&&c[k]===sex)));changed();},'');b.setAttribute('aria-pressed',String(c[k]===sex));seg.append(b);});field.append(seg);
  }else{
   const input=el(i>=4?'textarea':'input');if(i<4)input.type='text';input.id=scope+'-'+i;input.value=c[k];input.placeholder=(i>=4?'描述':'输入')+k;
   const label=el('label','',k);label.htmlFor=input.id;field.append(label,input);
   input.addEventListener('input',()=>{c[k]=input.value;changed();});
  }
  grid.append(field);
 }
 return grid;
}
section('profile','II','凡身档案','你降临人间时的凡人身份（用户角色）').append(characterForm(state.user,'user'));
const othersContent=section('people','III','众生名录','与你相遇的凡人（其他角色）');
const others=el('div');others.id='others';othersContent.append(others);
let lastRemoved=null,formSequence=0;
const restoreButton=button('恢复刚移除的角色',()=>{if(lastRemoved){state.others.splice(lastRemoved.index,0,lastRemoved.character);lastRemoved=null;renderOthers();changed();}});
restoreButton.hidden=true;
function renderOthers(){
 others.replaceChildren();
 state.others.forEach((c,index)=>{
  const card=el('div','other-card'),head=el('div','other-head');
  head.append(el('span','','✦ 角色 '+(index+1)),button('✕ 移除',()=>{lastRemoved={index,character:c};state.others.splice(index,1);renderOthers();changed();$('addChar').focus();},'remove'));
  card.append(head,characterForm(c,'other-'+formSequence++));others.append(card);
 });
 restoreButton.hidden=!lastRemoved;
 sections.get('people').head.querySelector('small').textContent='与你相遇的凡人（其他角色） '+state.others.length;
}
const add=button('＋ 添加一位角色',()=>{state.others.push(blankCharacter());renderOthers();changed();others.lastElementChild.querySelector('input').focus();},'add');add.id='addChar';othersContent.append(add,restoreButton);renderOthers();
const eraSection=section('era','IV','纪元之选','选择你降临的时代背景');
const eraGrid=el('div','tile-grid era-grid');eraSection.append(eraGrid);
eras.forEach(e=>{const b=tile(e,()=>{state.era=state.era===e[1]?'':e[1];changed();});b.dataset.era=e[1];eraGrid.append(b);});
function textareaField(parent,id,label,placeholder,value,onInput){
 const box=el('div','field wide'),l=el('label','',label),t=el('textarea');t.id=id;t.placeholder=placeholder;t.value=value;l.htmlFor=id;box.append(l,t);parent.append(box);t.addEventListener('input',()=>{onInput(t.value);changed();});return t;
}
textareaField(eraSection,'eraExtra','自定义新世界（选填）','对你降临世界的自定义补充',state.eraExtra,v=>state.eraExtra=v);
const feat=section('rules','V','神权法则','功能区域 · 开启或关闭小功能');
features.forEach(([k,icon,desc])=>{
 const b=button('',()=>{cycleFeature(state,k);changed();},'feature');b.dataset.feature=k;
 const copy=el('span','copy');copy.append(el('b','',k),el('small','',desc));b.append(el('span','icon',icon),copy,el('span','switch'));feat.append(b);
});
function segment(parent,heading,values,sub,key){
 parent.append(el('h5','',heading));const group=el('div','segments');group.setAttribute('role','group');group.setAttribute('aria-label',heading);
 values.forEach((v,i)=>{const b=button('',()=>{state[key]=v;changed();},'');b.dataset[key]=v;b.append(el('span','',v),el('small','',sub[i]));group.append(b);});parent.append(group);
}
segment(feat,'👁️ 人称视角',persons,['"我"','"你"','"他/她"'],'person');
segment(feat,'📏 回复长度',lengths,['500字以下','700-1200字','1500字以上'],'length');
const resets=el('div','actions');resets.append(button('恢复默认',()=>{state.features=defaultFeatures();state.person='第二人称';state.length='中等';changed();}),button('全部关闭',()=>{for(const [k] of features)state.features[k]=false;changed();}));feat.append(resets);
const opening=section('scene','VI','创世第一幕','填写开场白');
const openingInput=textareaField(opening,'opening','开场白','故事从这里开始……',state.opening,v=>state.opening=v);openingInput.className='opening';
const counter=el('div','counter');opening.append(counter);
const bibleSection=section('scripture','VII','天书 · 实时显示','你写下的一切都会在这里出现，可以一键复制');
const progressBox=el('div','progress'),progress=el('progress'),percent=el('span');progress.max=100;progress.setAttribute('aria-label','神格凝聚');progressBox.append(el('span','','神格凝聚'),progress,percent);
const bible=el('div');bible.id='bible';bible.tabIndex=0;bible.setAttribute('aria-label','实时天书');
bibleSection.append(progressBox,bible,el('p','hint','✦ 直接点天书里的功能行，就能切换开启 / 关闭'));
const copyActions=el('div','actions');const copyButton=button('📋 一键复制',()=>copy(serializeSetup(state)),'primary');copyButton.id='copyBtn';
const omitLabel=el('label','omit'),omit=el('input');omit.type='checkbox';omit.id='omitEmpty';omit.checked=state.omitEmpty;omit.addEventListener('change',()=>{state.omitEmpty=omit.checked;changed();});omitLabel.append(omit,document.createTextNode('省略未填写项'));copyActions.append(copyButton,omitLabel);bibleSection.append(copyActions);
function refreshButtons(){
 document.querySelectorAll('[data-feature]').forEach(b=>b.setAttribute('aria-pressed',String(state.features[b.dataset.feature])));
 for(const k of ['person','length','era'])document.querySelectorAll('[data-'+k+']').forEach(b=>b.setAttribute('aria-pressed',String(state[k]===b.dataset[k])));
}
function renderBible(){
 const scroll=bible.scrollTop;const focused=document.activeElement?.dataset?.bibleFeature;
 bible.replaceChildren();
 for(const block of setupBlocks(state,true)){
  bible.append(el('h4','','【'+block.name+'】'));
  for(const row of block.rows){
   const text=(row.name?row.name+'：':'')+row.value;
   if(row.feature){const b=button(text,()=>{cycleFeature(state,row.name);changed();},'bible-feature');b.dataset.bibleFeature=row.name;bible.append(b);}
   else bible.append(el('div','bible-row',text));
  }
 }
 bible.scrollTop=scroll;
 if(focused)bible.querySelectorAll('[data-bible-feature]').forEach(b=>{if(b.dataset.bibleFeature===focused)b.focus({preventScroll:true});});
 counter.textContent=state.opening.length+' 字';
 // Local completeness indicator: 8 profile fields, other character, era, opening.
 const filled=fields.filter(k=>state.user[k].trim()).length+Number(state.others.some(c=>fields.some(k=>c[k].trim())))+Number(Boolean(state.era||state.eraExtra))+Number(Boolean(state.opening));
 progress.value=Math.round(filled/11*100);percent.textContent=progress.value+'%';
}
$('fab').addEventListener('click',()=>{setOpen('scripture',true);sections.get('scripture').root.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});sections.get('scripture').head.focus({preventScroll:true});});
window.addEventListener('storage',e=>{if(e.key===key){writable=false;$('draftStatus').textContent='其他标签页修改了草稿；本页暂停保存，请先复制内容再刷新';}});
refreshButtons();renderBible();
if(location.pathname.startsWith('/rpg-app/god/setup/')&&window.parent!==window){
 const create=button('使用天书创建离线会话',()=>{if(!writable){toast('请先核对草稿存储状态');return;}window.parent.postMessage({type:'god-setup-create',setup:state},location.origin);},'primary');
 create.id='createSession';bibleSection.append(create);
}
