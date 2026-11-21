'use strict';
const archive = JSON.parse(document.getElementById('archive-data').textContent);
const panel = document.getElementById('panel');
const nav = document.querySelector('nav');
const tabs = ['首页','世界观','魔术师故事线','英灵切入点','NPC档案','天赋抽取','魔术师登录','英灵登录','更新日志'];
const modes = {magus: {fields:{}, group:null, entry:null, custom:'', pool:'talents', limit:2, drawn:[], picks:new Set()}, servant: {fields:{}, group:null, entry:null, custom:'', pool:'skills', limit:3, drawn:[], picks:new Set()}};
let current = 0;
let messageTimer;
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function button(text, action, className) {
  const el = node('button', text, className);
  el.type = 'button';
  el.addEventListener('click', action);
  return el;
}
function heading(text) { panel.append(node('h2',text)); }
function notify(text) {
  document.getElementById('feedback').textContent=text;
  clearTimeout(messageTimer);
  messageTimer=setTimeout(()=>document.getElementById('feedback').textContent='',3000);
}
function show(tab, scroll=true) {
  current=tab;
  [...nav.children].forEach((el,i)=>{if(i===tab)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});
  panel.replaceChildren();
  panel.classList.toggle('home-panel',tab===0);
  if (tab===0) home();
  if (tab===1) worldview();
  if (tab===2 || tab===3) timeline(tab===2?'magus':'servant');
  if (tab===4) characters();
  if (tab===5) {heading('天赋抽取（10选2）');roll('magus');heading('自定义天赋');field(panel,'magus','自定义天赋','paragraph');}
  if (tab===6 || tab===7) profile(tab===6?'magus':'servant');
  if (tab===8) {heading('更新日志');const box=node('div',undefined,'log inset');archive.log.filter(t=>t!=='更新日志').forEach(t=>box.append(node('p',t)));panel.append(box);}
  if(scroll) window.scrollTo({top:0,behavior:'instant'});
}
tabs.forEach((name,i)=>nav.append(button(name,()=>show(i))));
function list(items, host=panel) {
  const ul=node('ul');
  items.forEach(text=>ul.append(node('li',text.replace(/^◆\s*/,''))));
  host.append(ul);
}
function home(){
  const items=archive.home;
  const section=()=>{const el=node('section',undefined,'home-section');panel.append(el);return el;};
  const intro=section(), title=node('h1');
  const parts=items[0].match(/^(.*?)\s+(Ver\s+[\d.]+)$/i);
  title.append(document.createTextNode(parts?parts[1]+' ':items[0]));
  if(parts)title.append(node('span',parts[2],'home-version'));
  intro.append(title,node('p',items[1],'home-welcome'));
  const pending=items.indexOf('动画/漫画'), games=items.indexOf('游戏');
  const worlds=section();
  worlds.append(node('h2','已加载的世界内容'));list(items.slice(2,pending),worlds);
  const plans=section();
  plans.append(node('h2','待载入项目'),node('h3',items[pending]));list(items.slice(pending+1,games),plans);
  plans.append(node('h3',items[games]));list(items.slice(games+1,games+2),plans);
  const works=section();works.append(node('h2','作者其他作品'));list(items.slice(games+2),works);
}
function worldview(){
  const values=archive.worldview;
  const begin=values.findIndex(t=>!t.startsWith('◆'));
  heading('基本结构');list(values.slice(0,begin));heading('核心概念详解');
  for(let i=begin;i<values.length;i+=2){
    if(values[i].startsWith('T0:'))heading('实力划分（参考）');
    const el=node('details');el.append(node('summary',values[i]),node('p',values[i+1]));panel.append(el);
  }
}
const placeholders={'姓名':'如：远坂、间桐、苍崎','性别':'男 / 女 / 未指定','年龄':'通常在16-30岁之间','外貌':'发色、瞳色、气质等','体型':'高挑、健壮、纤细等','起源':'如：剑、虚无、重复','性格':'如：冷静、傲娇、利己','魔术师准则':'如：追求根源、守护家族','梦想':'如：成为魔法使、复兴家业','出身':'如：时钟塔贵族、在野家系','补充说明':'可在此填写更多背景故事、人际关系等……'};
function field(host,mode,key,type='text',options,initial='') {
  const model=modes[mode].fields;
  if (!(key in model)) model[key]=initial || options?.[0] || '';
  const label=node('label',key,type==='paragraph'?'wide':undefined);
  const input=node(options?'select':type==='paragraph'?'textarea':'input');
  if(options)options.forEach(v=>{const op=node('option',v);op.value=v;input.append(op);});
  if(type==='paragraph')input.rows=3;
  if(mode==='magus'&&placeholders[key])input.placeholder=placeholders[key];
  input.value=model[key];input.addEventListener('input',()=>{model[key]=input.value;});
  label.append(input);host.append(label);return input;
}
function timeline(mode) {
  const state=modes[mode],groups=archive[mode];
  heading(mode==='magus'?'魔术师故事线（选择将写入档案）':'英灵切入点（选择将写入档案）');
  const selected=node('p',state.entry?'当前切入点 · '+state.entry.name:'尚未选择切入点','secondary');panel.append(selected);
  if(state.group===null){
    panel.append(node('p','先选择作品系列，再选择切入点。'));
    let grid;
    groups.forEach((group,i)=>{
      if(i===0 || i===5){panel.append(node('h3',i===0?'Fate/Grand Order':'其他作品系列'));grid=node('div',undefined,'grid');panel.append(grid);}
      const choose=button('',()=>{state.group=i;show(current);},'choice');
      choose.append(node('b',group.group),node('small',group.subtitle),node('small',group.items.length+' 个','group-count'));grid.append(choose);
    });
    return;
  }
  const group=groups[state.group];
  panel.append(button('‹ 返回分类',()=>{state.group=null;show(current);}),node('h3',group.group),node('p',group.items.length+' 个切入点','secondary'));
  group.items.forEach(item=>{
    const el=node('article',undefined,'entry'),label=node('label'),radio=node('input');
    radio.type='radio';radio.name=mode+'-timeline';radio.checked=state.entry===item;
    radio.addEventListener('change',()=>{state.entry=item;selected.textContent='当前切入点 · '+item.name;});
    label.append(radio,document.createTextNode(mode==='magus'?' 选择该世界':' 以此身份降临'));el.append(node('b',item.name),node('p',item.description),label);
    if(item.fields?.length){const text=node('textarea');text.setAttribute('aria-label','自定义切入点');text.placeholder=item.fields[0].label;text.rows=4;text.value=state.custom;text.addEventListener('input',()=>{state.custom=text.value;state.entry=item;radio.checked=true;selected.textContent='当前切入点 · '+item.name;});el.append(text);}
    panel.append(el);
  });
}
const dialog=document.getElementById('npc');
dialog.querySelector('button').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target===dialog && (event.offsetX<0||event.offsetY<0||event.offsetX>dialog.clientWidth||event.offsetY>dialog.clientHeight))dialog.close();});
function characters(){
  heading('角色档案登录');
  const grid=node('div',undefined,'portraits');
  archive.npcs.forEach(npc=>{
    const item=button('',()=>{dialog.querySelector('h2').textContent='档案：'+npc.name;dialog.querySelector('input').value=npc.name;dialog.querySelector('textarea').value=npc.description;dialog.showModal();},'portrait');
    const img=node('img');img.src='portraits/'+npc.image;img.alt='';img.loading='lazy';item.append(img,node('span',npc.name));grid.append(item);
  });panel.append(grid);
}
function draw(mode){
  const state=modes[mode],pool=[...archive[state.pool]];
  for(let i=0;i<10;i++){const j=i+Math.floor(Math.random()*(pool.length-i));[pool[i],pool[j]]=[pool[j],pool[i]];}
  state.drawn=pool.slice(0,10);state.picks.clear();
}
function roll(mode){
  const state=modes[mode];if(!state.drawn.length)draw(mode);
  const toolbar=node('div',undefined,'toolbar'),grid=node('div',undefined,'grid'),count=node('strong');
  const refresh=()=>{count.textContent='已选：'+state.picks.size+' / '+state.limit;grid.replaceChildren();state.drawn.forEach(item=>{
    const choose=button('',()=>{
      if(state.picks.has(item))state.picks.delete(item);
      else if(state.picks.size<state.limit)state.picks.add(item);
      else{notify('最多选择 '+state.limit+' 项');return;}
      refresh();grid.querySelectorAll('button')[state.drawn.indexOf(item)].focus();
    },'choice');
    choose.setAttribute('aria-pressed',String(state.picks.has(item)));choose.append(node('b',item.name),node('small',item.description));grid.append(choose);
  });};
  toolbar.append(button(mode==='magus'?'生成 10 项':'抽取 10 项技能',()=>{draw(mode);refresh();}),count,button('清空选择',()=>{state.picks.clear();refresh();}));
  panel.append(toolbar,grid);refresh();
}
const ranks=['A','B','C','D','E','EX'];
const mageKeys=['姓名','性别','年龄','外貌','体型','起源'];
const mageValues=['性格','魔术师准则','梦想','出身','补充说明'];
const spiritKeys=['御主 (MASTER)','真名 (TRUE NAME)','性别','身高/体重','属性','样貌','性格','武器类型','出身/传说'];
const params=['筋力 (STRENGTH)','耐久 (ENDURANCE)','敏捷 (AGILITY)','魔力 (MANA)','幸運 (LUCK)','宝具 (NP)'];
function plane(){const grid=node('div',undefined,'fields');panel.append(grid);return grid;}
function build(mode) {
  const state=modes[mode], values=state.fields;
  const value=(key,fallback='')=>(values[key]||'').trim() || fallback;
  const entry=state.entry;
  const cut=entry?'【'+entry.name+'】\n'+(entry.fields?.length?state.custom.trim()||entry.description:entry.description):'（未选择）';
  if(mode==='magus'){
    const divider='-'.repeat(49);
    const defaults=['随机','未指定','19','普通，无明显特征','中等','随机'];
    const lines=['================= MAGUS PROFILE =================',
      ...mageKeys.map((key,i)=>key+': '+value(key,defaults[i])),
      '魔术回路·量: '+value('魔术回路·量'),'魔术回路·质: '+value('魔术回路·质'),
      ...mageValues.slice(0,4).map((key,i)=>key+': '+value(key,['冷静沉着','明哲保身','平静地生活下去','三流魔术家系末裔'][i])),
      divider,'世界切入点：',entry?cut:'未选择',divider,'天赋/礼装/刻印：'];
    const picks=[...state.picks].map(item=>'- 【'+item.name+'】\n  '+item.description);
    if(value('自定义天赋'))picks.push('- 【自定义】\n  '+value('自定义天赋'));
    lines.push(picks.join('\n\n')||'（未选择）');
    if(value('补充说明'))lines.push(divider,'补充说明：',value('补充说明'));
    return [...lines,'='.repeat(49)].join('\n');
  }
  const divider='-'.repeat(48);
  const picks=[...state.picks].map(item=>'- '+item.name+'\n  '+item.description);
  if(value('技能名称'))picks.push('- '+value('技能名称')+' [自定义]\n  '+value('技能描述','（无描述）'));
  const weapons=[];
  for(const n of [1,2]){
    const part=(key,fallback='')=>value('宝具 '+n+' - '+key+(n===2?' (可选)':''),fallback);
    if(part('名称'))weapons.push('◆ '+part('名称')+'\n  等级: '+part('等级','?')+'\n  种类: '+part('种类','?')+'\n  '+part('描述','（无描述）'));
  }
  return ['================ SERVANT STATUS ================',
    '职阶: '+value('职阶 (CLASS)','Saber'),'御主: '+value(spiritKeys[0],'（待定）'),
    '真名: '+value(spiritKeys[1],'阿尔托莉雅·潘德拉贡'),'性别: '+value('性别','女'),
    '身高/体重: '+value('身高/体重','154cm / 42kg'),'属性: '+value('属性','秩序·善'),
    '样貌: '+value('样貌','（未填写）'),'性格: '+value('性格','（未填写）'),'武器: '+value('武器类型','（未填写）'),
    divider,'【时间切入点】',cut,divider,'【出身/传说】',value('出身/传说','【让AI自由发挥】'),
    divider,'【能力值】',params.map(key=>key.split(' ')[0]+': '+value(key,'C')).join(' | '),
    divider,'【职阶/保有技能】',picks.join('\n\n')||'（未选择或添加技能）',
    divider,'【宝具】',weapons.join('\n\n')||'（未登录宝具）','='.repeat(49)].join('\n');
}
function profile(mode){
  const mage=mode==='magus';
  heading(mage?'魔术师信息登录':'英灵基础信息');let grid=plane();
  if(mage){
    mageKeys.forEach(k=>field(grid,mode,k));
    field(grid,mode,'魔术回路·量','text',['D级 (1-9条)','C级 (10-29条)','B级 (30-49条)','A级 (50-99条)','EX级 (100条以上)'],'C级 (10-29条)');
    field(grid,mode,'魔术回路·质','text',['E~D级 (劣等)','C级 (凡庸/平均)','B级 (优秀)','A级 (最高等级/异质 - Abnormal)','EX级 (规格外/特殊 - Special)'],'C级 (凡庸/平均)');
    mageValues.forEach(k=>field(grid,mode,k,k==='补充说明'?'paragraph':'text'));
  }else{
    field(grid,mode,'职阶 (CLASS)','text',['Saber','Archer','Lancer','Rider','Caster','Assassin','Berserker','Ruler','Avenger','Moon Cancer','Foreigner','Pretender']);
    spiritKeys.forEach(k=>field(grid,mode,k,k==='出身/传说'?'paragraph':'text'));
    heading('能力值 (Parameters)');grid=plane();params.forEach(k=>field(grid,mode,k,'text',ranks,'C'));
    heading('职阶技能 & 保有技能 (10选3)');roll(mode);heading('自定义技能');grid=plane();
    field(grid,mode,'技能名称');field(grid,mode,'技能描述','paragraph');
    heading('宝具 (Noble Phantasm)');grid=plane();
    for(const n of [1,2])for(const suffix of ['名称','等级','种类','描述'])field(grid,mode,'宝具 '+n+' - '+suffix+(n===2?' (可选)':''),suffix==='描述'?'paragraph':'text');
  }
  heading('合并预览');
  const controls=node('div',undefined,'toolbar'),preview=node('pre',undefined,'preview inset');preview.hidden=true;
  function generate(){const text=build(mode);preview.textContent=text;preview.hidden=false;return text;}
  controls.append(button(mage?'生成档案文本':'生成英灵档案',generate),button('复制到剪贴板',async()=>{const text=generate();try{await navigator.clipboard.writeText(text);notify('档案已复制');}catch{const range=document.createRange();range.selectNodeContents(preview);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);notify('档案已选中，按 Ctrl/Cmd+C 复制');}}));
  if(window.parent!==window&&new URLSearchParams(location.search).has('chat')){
    controls.append(button('带入聊天',()=>window.parent.postMessage({type:'type-moon-opening',text:generate()},new URL(location.href).origin)));
  }
  panel.append(controls,preview);
}
function initStars(){
  const canvas=document.getElementById('stars');
  const ctx=canvas.getContext('2d');if(!ctx)return;
  function paint(){const w=canvas.width=window.innerWidth,h=canvas.height=window.innerHeight;
    let seed=42;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
    for(let n=0;n<100;n++){ctx.fillStyle='rgba(190,219,244,'+(.12+rand()*.42)+')';ctx.beginPath();ctx.arc(rand()*w,rand()*h,.4+rand()*1.2,0,2*Math.PI);ctx.fill();}
  }paint();window.addEventListener('resize',paint);
}
function initMusic(){
  const control=document.getElementById('music');
  const audio=document.createElement('audio');
  audio.preload='none';audio.loop=true;audio.volume=.35;audio.src='fate.mp3';
  audio.addEventListener('playing',()=>control.setAttribute('aria-pressed','true'));
  audio.addEventListener('pause',()=>control.setAttribute('aria-pressed','false'));
  control.addEventListener('click',async()=>{
    if(!audio.paused){audio.pause();return;}
    try{await audio.play();}catch(error){if(error.name!=='AbortError')notify('无法播放背景音');}
  });
}
function initLoading(){
  if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
  const overlay=document.getElementById('loading'), progress=overlay.querySelector('progress');overlay.hidden=false;
  const done=()=>{overlay.hidden=true;clearInterval(timer);};
  const timer=setInterval(()=>{progress.value+=20;if(progress.value===60)document.getElementById('step').textContent='同步阿赖耶识…';if(progress.value>=100)done();},160);
  overlay.querySelector('button').addEventListener('click',done,{once:true});
}
show(0,false);initStars();initMusic();initLoading();
