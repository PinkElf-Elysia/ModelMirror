import {lines,entries,chapters,eligible,clip,whole,phrase} from './worldbook-sources.mjs';
export {entries as worldbook,chapters};
const common={common:true},from=rank=>({rank}),stage=chapter=>({chapter});
const person='人物',org='组织',place='地点',power='言灵',plot='剧情';
const base=(n,name,group,keys=[],gate=common)=>whole(n,group,name,keys,gate);
const part=(n,t,name,group,keys=[],gate=common)=>phrase(n,t,group,name,keys,gate);
const portion=(n,start,end,name,group,keys=[],gate=common)=>clip(n,start,end,group,name,keys,gate);
function fragments(n,delimiter,settings){
  const segments=lines[n-1].split(delimiter);
  if(settings.length!==segments.length)throw Error('Wrong segment count '+n);
  let start=0;
  segments.forEach((t,i)=>{
    const [name,group,keys,gate]=settings[i];
    clip(n,start,start+t.length+(i===segments.length-1?0:delimiter.length),group,name,keys,gate);
    start+=t.length+delimiter.length;
  });
}
part(682,'（如113号"莱茵"释放即灰飞烟灭）','莱茵',power,['言灵·莱茵','113号']);
part(689,'G级为芬格尔特设，不代表血统。','芬格尔',person,['芬格尔·冯·弗林斯']);
part(691,'芬格尔因多次留级被降至G级，实际战力仍达A级。','芬格尔',person);
base(696,'卡塞尔学院',org,['卡塞尔','秘党','校董会','执行部','装备部','风纪委员会','亚伯拉罕血统契']);
part(702,'（如「戒律」压制所有低血统者言灵、「神谕」唯一克制「皇帝」）','戒律',power,['神谕','皇帝','言灵·戒律']);
part(703,'（如针对「镰鼬」的听觉特性以高频音波干扰）','镰鼬',power,['言灵·镰鼬']);
part(704,'（如「时间零」近身完克大范围杀伤言灵）','时间零',power);
part(706,'（如七宗罪、村雨）','村雨', '物品',['七宗罪'],from(2));
base(712,'秘党',org,['卡塞尔学院']);
base(714,'卡塞尔学院本部',place,['卡塞尔学院','美国芝加哥','芝加哥','冰窖','诺顿馆','安珀馆','靶场','CC1000车站','七宗罪拍卖会场']);
base(715,'直辖分部',org,['纽约','伦敦','巴黎','拉斯维加斯']);
base(717,'日本分部',org,['蛇岐八家'],from(7));
base(719,'蛇岐八家',org,['日本分部','东京','高天原','热海黑石官邸','大阪极乐馆','爱媛梅津寺町','夜之食原'],from(7));
const cn=(t,name,keys=[],gate=common)=>part(723,t,name,place,keys,gate);
cn('- 中国：无正式执行分部，仅设安全港，核心地标：','中国安全港',['仕兰市','中国']);
cn('仕兰市（故事起点','仕兰市',['仕兰中学']);
cn('、0号高架尼伯龙根入口','零号高架路',['0号高架','楚天骄'],{rank:5,fromPrequel:'rain-fall'});
cn('）、','仕兰市',['仕兰中学']);
cn('长江三峡夔门（青铜城尼伯龙根）、','长江三峡夔门',['夔门','青铜城'],from(1));
cn('北京（地铁尼伯龙根）、','北京地铁',['北京','地铁尼伯龙根'],from(5));
cn('上海/香港联络点、','上海与香港联络点',['上海','香港']);
cn('中俄蒙边境线','中俄蒙边境线',[],from(10));
base(724,'欧洲据点',place,['加图索家族','罗马','卡塞尔庄园','格陵兰海','阿瓦隆']);
base(725,'黑天鹅港',place,['莫斯科','西伯利亚大铁路'],{rank:10,fromPrequel:'black-swan'});
base(729,'海外任务站',place,['加勒比海','中东','非洲','南美']);
const geo=[
 ['青铜城',from(1)],['北京地铁迷宫',from(5)],['阿瓦隆',{rank:9,fromPrequel:'greenland-ice-sea'}],['高天原',from(7)],['夜之食原',from(7)],['零号高架路',{rank:5,fromPrequel:'rain-fall'}],['黑天鹅港',{rank:10,fromPrequel:'black-swan'}]
];
fragments(733,'、',geo.map(([n,g])=>[n,place,['尼伯龙根',...(n==='零号高架路'?['0号高架']:[])],g]));
part(229,'图兰朵/','图兰朵',plot,[],from(5));
part(229,'/蛇岐八家','蛇岐八家',org,[],from(7));
part(466,'/"图兰朵事件后X日"','图兰朵',plot,[],from(5));
const sharedKeys=['死人之国','尼伯龙根'];
for(const n of [737,740,741,742])base(n,'尼伯龙根',place,sharedKeys);
const example=lines[738],begin=example.indexOf('青铜城'),end=example.indexOf('均为具体个例');
portion(739,0,begin,'尼伯龙根',place,sharedKeys);
let cursor=begin;
for(const [i,t] of example.slice(begin,end).split('、').entries()){
  const [n,g]=geo[[0,1,3,2,4,5][i]];
  portion(739,cursor,cursor+t.length+(i<5?1:0),n,place,[...sharedKeys,t],g);cursor+=t.length+1;
}
portion(739,end,example.length,'尼伯龙根',place,sharedKeys);
part(749,'（仅学院风云篇、东京黑月篇标注时间备注，不影响触发关键词）','学院风云篇',plot,['东京黑月篇'],from(6));
portion(750,lines[749].indexOf('（'),lines[749].length,'东京黑月篇',plot,['东京黑月篇'],from(7));
base(758,'前传',plot,[],{anyOf:chapters.filter(c=>c.work==='前传').map(c=>c.key)});
function chapterList(n,delimiter){
  let cursor=0;
  for(const [i,t] of lines[n-1].split(delimiter).entries()){
    const c=n===760?chapters.filter(c=>c.work==='前传')[i]:chapters.find(c=>c.rank===i);
    const row=portion(n,cursor,cursor+t.length,c.name,plot,[t,c.id],stage(c.key));
    row.auto=true;cursor+=t.length+delimiter.length;
  }
}
chapterList(760,' → ');chapterList(764,' → ');
for(const n of [766,768,769,770]){
  const candidates=chapters.filter(c=>c.work==='异闻录');
  if(n===768||n===769){
    const prefix=lines[n-1].indexOf('：')+1;
    const chunks=lines[n-1].slice(prefix).split('、');
    let i=prefix;
    for(const t of chunks){
      const c=candidates.find(c=>t.includes(c.name));
      const row=portion(n,i,i+t.length,c.name,plot,[c.id],stage(c.key));
      row.auto=true;i+=t.length+1;
    }
  }else base(n,'学院风云篇',plot,['异闻'],{anyOf:chapters.filter(c=>c.phase||c.work==='异闻录').map(c=>c.key)});
}
const chu=['楚子航','狮心会会长','面瘫师兄'];
for(let n=772;n<=795;n++){
  if(!lines[n-1])continue;
  if(n===792){
    const cut=lines[n-1].indexOf('，后期');
    portion(n,0,cut,'君焰',power,chu,from(0));
    portion(n,cut,lines[n-1].length,'君焰',power,[...chu,'抗拒火环','风王之瞳'],from(5));
  }else base(n,'楚子航',person,chu,[775,776,783,785,788,789,793,794].includes(n)?{rank:5,fromPrequel:'rain-fall'}:from(0));
}
for(const [start,end,name,keys,gate] of [[798,819,'尼德霍格',['黑王','黑皇帝','至高祖龙'],from(0)],[822,843,'伊邪那美',['白王','白皇帝','圣骸'],from(7)]]){
  for(let n=start;n<=end;n++)if(lines[n-1]){
    const later=[801,804,814].includes(n)?from(10):gate;
    base(n,n===817?'皇帝':n===841?'神谕':name,n===817||n===841?power:person,[name,...keys],later);
  }
}
part(847,'，代表人物：希尔伯特·让·昂热','昂热',person,['昂热校长']);
part(847,'，代表人物：上杉越、源稚生','上杉越',person,['源稚生'],from(7));
part(849,'，代表人物：恺撒·加图索、楚子航（常态）','恺撒',person,['楚子航','凯撒']);
part(857,'（如王将制造的"鬼"）','王将',person,['赫尔佐格'],from(7));
part(859,'G级为芬格尔特设（因多次留级被降级），不代表血统，实际战力仍达A级水准。','芬格尔',person);
const titleAliases=[['火之晨曦','Ⅰ','I','1','一'],['悼亡者之瞳','Ⅱ','II','2','二'],['黑月之潮','Ⅲ','III','3','三'],['奥丁之渊','Ⅳ','IV','4','四'],['悼亡者的归来','Ⅴ','V','5','五']];
function identify(value){
  if(!value)return null;
  const c=chapters.find(c=>value===c.key||value===c.id||value===c.name||value.startsWith(c.name+'（')||value.endsWith('·'+c.name));
  if(c?.phase&&/第二沙盒期|2011年(?:下半年|[6-9]月)|东京黑月篇后/.test(value))return chapters.find(x=>x.rank===8);
  if(c)return c;
  const name=value.trim().replace(/^《|》$/g,'');
  const title=titleAliases.find(([t,...numbers])=>[t,...numbers.map(v=>'龙族'+v+'：'+t),...numbers.map(v=>'龙族'+v)].includes(name.replace(':','：')));
  return title?chapters.find(c=>c.work===title[0]):null;
}
function openingStage(text){
  const native=text.match(/^■ 时间线篇章[ \t]*\r?\n[ \t]*([^\r\n]+)/m);
  const field=text.match(/^(?:作品(?:名)?|当前篇章|时间线篇章)[：:]\s*([^\r\n]+)/m);
  const value=(native?.[1]||field?.[1]||text.trim()).trim();
  if(value.startsWith('学院风云篇')&&/^\s*入场节点[：:].*(?:第二沙盒期|东京黑月篇后)/m.test(text))return chapters.find(c=>c.rank===8);
  return identify(value);
}
function selectedSpirit(text,e){
  const field=text.match(/■ 掌握言灵[^\n]*\n([\s\S]*?)(?=\n■|\n═|$)/)?.[1]||'';
  return field.includes('. '+e.name+'\n     '+e.text);
}
function matches(e,text){
  return e.keys.some(k=>k.length>1?text.includes(k):text.split('\n').some(line=>line.trim()===k)||['言灵','言灵·','询问','查阅','拜访','扮演原著：','《','「'].some(prefix=>text.includes(prefix+k)));
}
const modelNames={696:['卡塞尔学院','秘党','校董会','执行部','装备部','风纪委员会'],714:['卡塞尔学院本部','芝加哥','冰窖','诺顿馆','安珀馆','学院车站','靶场'],715:['直辖分部'],719:['蛇岐八家','日本分部'],724:['罗马','卡塞尔庄园','格陵兰海'],725:['黑天鹅港','莫斯科','西伯利亚大铁路']};
for(const e of entries)if(modelNames[e.ref.line]){
  e.modelNames=modelNames[e.ref.line];e.keys=[...new Set([...e.keys,...e.modelNames])];
}
const categoryOrder=[person,org,place,power,plot,'物品'];
function shelf(pool,work){
  const out=['【世界书名称库】使用当前篇章原名。遇到人、地点或能力时，在剧情中自然写出相应规范名；只使用当前时间线已经揭晓的情报。'];
  for(const g of categoryOrder){
    const rows=pool.filter(r=>r.group===g);
    const names=new Set(rows.flatMap(r=>(r.modelNames||[r.name]).map(name=>name+(r.phase?'（'+r.work+'）':rows.some(x=>x.name===name&&(x.variant||'')!==(r.variant||''))?'（'+(r.variant||work)+'）':''))));
    if(names.size)out.push(g+'：'+[...names].join('、'));
  }
  return out.join('\n');
}
export function selectWorldbook({opening='',history=[],progress=[],input=''}={}){
  const archive=opening||history.find(m=>m.role==='user')?.content||input;
  let s=openingStage(archive);
  const advance=message=>{
    const target=message.trim().match(/^(?:推进篇章|进入篇章|切换篇章)[：:]\s*(.+)$/);
    const direct=chapters.some(c=>message.trim()===c.name);
    if(!target&&!direct)return;
    const next=identify(target?target[1]:message.trim());
    if(next&&(!s||s.work===next.work||s.rank!==null&&next.rank===s.rank+1))s=next;
  };
  for(const message of history){
    if(message.role==='user')advance(message.content);
    if(message.role==='assistant'){
      const name=message.content.match(/^[│ ├└─]*当前时间线篇章[：:]\s*([^\r\n]+)/m)?.[1];
      const next=name&&identify(name.trim());
      if(next&&s&&next.work===s.work&&s.rank!==null&&next.rank===s.rank+1)s=next;
    }
  }
  if(!history.length)for(const message of progress)advance(message);
  advance(input);
  const text=[archive,...history.slice(-2).map(m=>m.content),input].join('\n');
  const pool=entries.filter(e=>eligible(e,s)||e.selection&&selectedSpirit(archive,e));
  const active=new Set(pool.filter(e=>e.auto||e.chapterEntry||matches(e,text)));
  const visibleChapters=entries.filter(e=>e.chapterEntry&&e.work===s?.work);
  const catalog=shelf([...new Set([...pool,...visibleChapters])],s?.work||'通用');
  return {work:s?.work||'未定',chapter:s?.name||'未定',stage:s?.key||null,catalog,active};
}
