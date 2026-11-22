import {readFileSync} from 'node:fs';
import {alternatives,replyText,section,slots} from './assembly.mjs';

const resource = name => readFileSync(new URL('./resources/'+name,import.meta.url),'utf8');
const catalog = JSON.parse(resource('reviewed-worldbook.json'));
const chapters = JSON.parse(resource('chapters.json'));
const fixed = resource('system.txt');
const byId = new Map(catalog.map(e=>[e.id,e]));
const chapterNames = alternatives(chapters.map(c=>c.title));
const chapterMention = new RegExp(chapterNames,'gu');
const field = new RegExp('^(?:【时间线】|当前时间线篇章[：:]|当前篇章[：:])\\s*[*]*('+chapterNames+')','u');
const choice = new RegExp('^(?:切换(?:时间线)?(?:到|至|为)?|进入|选择|开局选|开团选)\\s*[「“【]?('+chapterNames+')','u');
const routes = new Map();
const byKind = new Map();
for(const record of catalog){
  const e=record.profile?byId.get(record.profile):record;
  if(!e || !slots.has(e.slot))throw Error('无效条目 '+record.id);
  if(!record.profile){
    if(!byKind.has(e.kind))byKind.set(e.kind,new Set());
    byKind.get(e.kind).add(e.title);
  }
  for(const key of record.keywords){
    if(!routes.has(key))routes.set(key,new Set());
    routes.get(key).add(e);
  }
}
const words=new RegExp(alternatives(routes.keys()),'gu');
export const triggerIndex=['【世界书触发表】','按当前篇章取用资料。需要某项设定时在可见叙事中自然写出规范名；名称只供检索，角色所知仍按入场时点。',...Array.from(byKind,([kind,names])=>kind+'｜'+[...names].join('、'))].join('\n');
export const reviewedPrompt=()=>fixed+'\n\n'+triggerIndex;

function stated(text,player){
  for(const line of text.split(/\r?\n/).reverse()){
    const value=line.trim().replace(/^[│├└─\s•-]+/, '').replaceAll('**','');
    const name=field.exec(value)?.[1] || (player && (choice.exec(value)?.[1] || chapters.find(c=>c.title===value)?.title));
    if(name)return chapters.find(c=>c.title===name);
  }
}
function phaseAt(history,input,opening){
  let chapter=stated(input,true);
  if(chapter)return chapter;
  for(const message of history.toReversed()){
    if(message.role==='user')chapter=stated(message.content,true);
    if(message.role==='assistant')chapter=stated(replyText(message.content,true),false);
    if(chapter)return chapter;
  }
  return stated(opening,true);
}
function entryPoint(history,input,opening,chapter){
  for(const text of [input,...history.toReversed().map(m=>m.role==='assistant'?replyText(m.content,true):m.content),opening]){
    const chosen=stated(text,true);
    if(chosen && chosen.title!==chapter.title)return '';
    const line=text.split(/\r?\n/).find(l=>l.trimStart().startsWith('【入场时机】'));
    if(line)return line.split('】')[1].trim();
  }
  return '';
}

// Keep the author's whole chapter in the source. At a selected early entry, expose only the opening scene.
const openings=[
  '世界始于"全なる一"','星王龙死后','格兰贝尔在西方与露米娜丝数度交战','约七十年前，莱昂召唤东京大空袭中濒死的静江',
  '转生为朱拉大森林封魔洞窟中的蓝色史莱姆','前往武装国家多瓦贡寻求技术支持','利姆鲁遇见了濒死的日本召唤者井泽静江','朱拉大森林陷入危机',
  '最古魔王蜜莉姆·纳瓦突然降临特恩佩斯特','前往英格拉西亚王国自由学园担任S班教师','调查沙漠小国柯利乌斯的王位继承纠纷','利姆鲁归国途中遭西方圣教会圣骑士团长日向·坂口伏击',
  '召集魔王盛宴（Walpurgis）企图制裁利姆鲁','来到特恩佩斯特求援','利姆鲁与日向再度交锋','邀请各国来宾参加',
  '前往度假岛休养','提出开发"兽魔术"','利姆鲁正式申请加盟西方诸国评议会','举办音乐交流会',
  '侵攻特恩佩斯特','利姆鲁将收集的约一百万份灵魂分配给十二名核心干部','米迦勒与费尔德维以天星宫为据点发动天使军势','率幻兽族三王大军入侵基轴世界'
];
const chronology=new Map([
  [2,'三名妖死族各自的生前身份按定稿处理，嘉加里生前是高等精灵。'],
  [18,'阿姆利塔战后蜜莉姆取得龙蛋并为伙伴命名；此刻仍在蛋中。'],
  [19,'日向原有的勇者之卵留在克萝耶体内；这次苏生后，原有的卵仍在克萝耶体内。'],
  [20,'阿皮托驻79层，泽奇恩驻80层。依第12、13卷名册，帝国战时的守卫应采用当时形态。'],
  [23,'优树和拉普拉斯受困处是次元夹缝。被送到时空尽头并从那里归来的是利姆鲁。']
]);
// Originals remain in chapters.json for audit and legacy saves. Superseded clauses cannot
// enter a new-game request alongside their own correction.
const correctedClauses=new Map([
  [2,[['将三名战死亲卫复活为妖死族','造出三名妖死族']]],
  [18,[['混沌龙盖亚被净化重生为小龙回到蜜莉姆身边','龙魂得以保存，蜜莉姆接过龙蛋，给伙伴取名盖亚']]],
  [19,[['日向复活进化为神人，成为真之勇者。','日向获救。']]],
  [20,[['等觉醒魔王级守护者','等守护者'],['趁维尔德拉在迷宫第百层时','趁机']]],
  [23,[['优树和拉普拉斯从时空尽头归还','优树和拉普拉斯脱离次元夹缝']]]
]);
function chapterBody(chapter,phase,restricted,point){
  if(!restricted){
    const note=chronology.get(phase);
    let body=chapter.body;
    for(const [original,approved] of correctedClauses.get(phase)??[]){
      if(!body.includes(original))throw Error('篇章补注底本不符 '+chapter.title);
      body=body.replace(original,approved);
    }
    return section({...chapter,body})+(note?'\n【本篇补注】'+note:'');
  }
  const marker=openings[phase],index=chapter.body.indexOf(marker);
  if(index<0)throw Error('篇章入场原文缺失 '+chapter.title);
  const end=point==='篇章中期'?chapter.body.indexOf('。')+1:index+marker.length;
  const excerpt=chapter.body.slice(0,end);
  return '【当前篇章】'+chapter.title+'\n'+chapter.year+'\n'+excerpt+(excerpt.endsWith('。')?'':'。');
}
export function selectReviewed(history,input,opening=''){
  const chapter=phaseAt(history,input,opening);
  const phase=chapters.indexOf(chapter);
  const point=chapter && entryPoint(history,input,opening,chapter);
  const restricted=!!point && point!=='篇章后期' && point!=='—' && point!=='未定';
  const result=chapter?[{id:'chapter-'+phase,title:chapter.title,slot:chapter.slot,body:chapterBody(chapter,phase,restricted,point)}]:[];
  const last=history.findLast(m=>m.role==='assistant');
  const story=last?replyText(last.content,false).replace(/```[^]*?(?:```|$)/g,'\n'):'';
  const selected=new Set();
  const sources=[input,story];
  if(!history.length)sources.push(opening);
  for(const source of sources){
    const text=source.replace(/^.*[：:]\s*(?:—|未定)\s*$/gm,'').replace(chapterMention,' ');
    for(const match of text.matchAll(words)){
      if(match[0]==='枫' && /^(?:叶|树|林|木|糖)/u.test(text.slice(match.index+1)))continue;
      for(const e of routes.get(match[0]))selected.add(e);
    }
  }
  let remaining=12000-result.reduce((n,e)=>n+e.body.length+2,0);
  for(const e of selected){
    if(e.only!==undefined && e.only!==phase)continue;
    const parts=e.segments.filter(s=>s.from<=phase && !(phase>=4 && s.mainFrom>phase)
      && (!restricted || s.from<phase || s.atOpening));
    if(!parts.length)continue;
    const lines=[];
    let priorHeading='';
    for(const part of parts){
      if(parts.some(other=>other!==part && other.body.length>part.body.length && other.body.includes(part.body)))continue;
      const [first,...rest]=part.body.split('\n');
      if(!rest.length){lines.push(first);priorHeading='';continue;}
      if(first!==priorHeading)lines.push(first);
      lines.push(...rest);
      priorHeading=first;
    }
    const body='【'+e.title+'】\n'+lines.join('\n');
    if(body.length+2>remaining)continue;
    remaining-=body.length+2;
    result.push({id:e.id,title:e.title,slot:e.slot,body});
  }
  return result;
}
export function reviewedTurn(system,history,input,opening=''){
  const grouped=new Map();
  for(const e of selectReviewed(history,input,opening)){
    const anchor=slots.get(e.slot);
    if(!grouped.has(anchor))grouped.set(anchor,[]);
    grouped.get(anchor).push(e.body);
  }
  const content=system.split('\n').flatMap(line=>{
    const bodies=grouped.get(line);
    if(!bodies)return [line];
    grouped.delete(line);
    return [...bodies,'',line];
  }).join('\n');
  if(grouped.size)throw Error('定稿世界书锚点缺失');
  return {current:input,messages:[{role:'system',content},...history,{role:'user',content:input}]};
}
