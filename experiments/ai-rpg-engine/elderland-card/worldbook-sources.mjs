import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {parseExpressionAt} from 'acorn';
export const sha=s=>createHash('sha256').update(s).digest('hex');
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const manifest=JSON.parse(read('./resources/MANIFEST.json'));
export const source=JSON.parse(read('./resources/prompts.json')).system;
const html=read('./resources/original.html');
if(sha(source)!==manifest.system.sha256||sha(html)!==manifest.frontend.sha256)throw Error('ELDERLAND_SOURCE_DRIFT');
export const lines=source.split('\n');
export const regions={
 north:['霜寒领','北境','北境·霜寒领','北境·永冬荒原','Frostmarch'],
 west:['风暴群岛','西境','西境·风暴群岛','Stormreach Isles'],
 central:['诸王之地','中央','中央·诸王之地','Central Kingdoms'],
 east:['奥术帝国','东境','东境·奥术帝国','Arcane Empire'],
 south:['黄金海岸','南境','南境·黄金海岸','Golden Coast'],
 elfwood:['艾瑞温','精灵密林','精灵密林·艾瑞温','艾瑞温精灵王国'],
 dwarf:['卡拉克山','矮人山脉','矮人山脉·卡拉克山'],underdark:['幽暗地域','地下世界','深渊之下']
};
export const entries=[], positions=new Map(),locations=[];
const R='区域',P='地点',F='势力',C='人物',T='任务';
const distinct=a=>[...new Set(a.filter(Boolean))];
function add(region,category,name,keys=[],mode='named',parent=''){
  if(!name)throw Error('Empty entry name');
  const original=name;
  name=name.replace(/^([南北东西]部)·/,'$1').replace(/^隐藏区域·/,'');
  name=name.replace(/[（(].*$/,'').replace('与海精灵/特里同交涉','海精灵与特里同交涉');
  if(category===C)name=name.replace(/^(?:公会会长|冒险者公会长|次女|长子|老贤者|大公(?=埃里克)|德鲁伊大长老(?=瑟兰迪尔)|馆长(?=埃尔隆德)|战锤军团总指挥|大熔炉总管|酒馆老板|塑能院长|咒法院长|人类国王(?=罗德里克))/,'')||original;
  const row={id:'e'+(entries.length+1),region,category,name,keys:distinct([name,original,...keys]),mode,parent,spans:[]};
  entries.push(row);return row;
}
const short=t=>t.trim().replace(/^[\s·•\d.\-*“"【]+/,'').split(/[：:（(—｜，；。】”"\n]/)[0].replace(/\*+/g,'').trim();
function span(row,n,end=n){
  for(let i=n;i<=end;i++)positions.set(i,row);
}
const anchors={};
const overview={};
for(const [key,n] of Object.entries({north:1014,west:1125,south:1239,central:1566,elfwood:840,east:2727,dwarf:2778,underdark:2807})){
  anchors[key]=n;overview[key]=add(key,R,regions[key][0],regions[key],'region');
}
const sections=[
 [829,839,'central',C,'雅克'],[840,954,'elfwood'],[1014,1124,'north'],[1125,1238,'west'],[1239,1344,'south'],
 [1345,1437,'north',P,'永冬荒原'],[1438,1472,'north',C,'瑟兰',['龙语者','Theron']],
 [1473,1565,'west',P,'沉没的亚特兰蒂斯',['亚特兰蒂斯']],[1566,1714,'central'],[1715,1806,'central',P,'艾尔希林'],
 [1807,1824,'central',P,'风语城精灵区',['北区·精灵区']],[1825,1845,'central',C,'让娜·德·多米雷米',['让娜','神选圣女']],
 [1846,1856,'central',C,'雅克'],[1857,1971,'elfwood'],[1972,2017,'elfwood',P,'世界树'],[2018,2045,'elfwood',P,'星歌森林'],
 [2046,2088,'elfwood',P,'密林工匠区'],[2089,2104,'elfwood',P,'德鲁伊圣环'],[2105,2154,'elfwood',P,'游侠哨站'],
 [2155,2200,'elfwood',P,'星歌林地'],[2201,2264,'elfwood',P,'月井城'],[2265,2312,'elfwood',P,'银叶王庭'],
 [2313,2370,'elfwood',C,'凯兰崔尔·星辰之冠',['凯兰崔尔','精灵至高王']],[2371,2421,'elfwood',C,'加拉德瑞尔·月守',['加拉德瑞尔','月井守护者']],
 [2422,2460,'elfwood',C,'埃尔隆德·典藏者',['精灵学者','Elrond the Lorekeeper']],[2461,2498,'elfwood',C,'林迪尔',['精灵游吟诗人']],
 [2499,2547,'elfwood',C,'莱戈拉斯·风行者',['莱戈拉斯','游侠统领']],[2548,2583,'elfwood',C,'瑟兰迪尔·绿父',['瑟兰迪尔','德鲁伊大长老']],
 [2584,2652,'elfwood',C,'埃尔隆德·晨星',['精灵王子']],[2653,2726,'elfwood',C,'阿尔温·暮星',['阿尔温','精灵公主']],
 [2727,2777,'east'],[2778,2806,'dwarf'],[2807,2843,'underdark']
];
const parents=new Map();
for(const [start,end,r,category,name,aliases=[]] of sections){
  const item=name?add(r,category,name,aliases):overview[r];
  span(item,start,end);for(let i=start;i<=end;i++)parents.set(i,item);
}
for(const [i,r] of ['north','west','central','east','south','elfwood','dwarf','underdark'].entries())span(overview[r],812+i);
for(let n=760;n<=764;n++)span(add('common',F,short(lines[n-1])),n);
function child(n,end,category,name,aliases=[],mode='named',parent){
  const base=parents.get(n);
  const e=add(base.region,category,name,aliases,mode,parent??(base.category===R?'':base.name));
  span(e,n,end);return e;
}
function list(a,b,group,mode='named'){
  for(let n=a;n<=b;n++){
    const s=lines[n-1];if(!s.trim()||/^[-\s]*$/.test(s))continue;
    const name=short(s);
    if(name&&name!=='初始态度')child(n,n,group,name,[],mode);
  }
}
for(const [a,b,g,mode] of [
 [1073,1078,P],[1083,1089,F],[1105,1110,T,'board'],[1185,1191,P],[1196,1201,F],[1217,1225,T,'board'],
 [1254,1258,P],[1266,1273,F],[1326,1334,T,'board'],[1404,1410,T,'board'],[1469,1471,T],
 [1537,1547,T],[1651,1660,P],[1663,1673,F],[1686,1698,T,'board'],[1764,1769,P],[1772,1784,C],
 [866,874,C],[1883,1891,C],[2364,2368,T],[2414,2419,T],[2453,2458,T],[2491,2496,T],
 [2539,2544,T],[2576,2580,T],[2646,2650,T],[2720,2724,T],[2740,2743,F],[2746,2748,P],[2769,2773,T,'board'],[2820,2825,F]
])list(a,b,g,mode);
for(const [a,b] of [[1039,1068],[1145,1180],[1605,1648]]){
  const starts=[];
  for(let n=a;n<=b;n++)if(/^\*\*\d+\./.test(lines[n-1]))starts.push(n);
  starts.forEach((start,i)=>child(start,(starts[i+1]||b+1)-1,P,short(lines[start-1])));
}
for(const start of [878,1895])for(let n=start;n<=start+24;n+=3)child(n,n+2,P,short(lines[n-1]));
for(let n=1360;n<=1375;n+=3)child(n,n+2,P,short(lines[n-1]));
for(const [a,b,g,name,aliases] of [
 [1752,1754,C,'米瑞尔·月咏',['精灵女王']],[1755,1757,C,'罗德里克三世',['人类国王']],[1758,1761,P,'风语城'],[1793,1796,T,'风之歌节'],
 [2788,2788,P,'铁砧堡',['熔火城','深矿城']],[2789,2789,F,'锻造大师公会',['矿工行会','战锤军团','奥瑞利安神殿']],
 [2797,2797,P,'瓦拉希尔龙巢',['瓦拉希尔']],[2804,2804,T,'清矿洞怪物',['护送商队','寻稀有矿石']],
 [2827,2827,P,'蛛网城',['梅兹贝兰赞','心灵要塞','灰铁堡','幻影城','眼魔巢穴']],[2829,2836,T,'厄奎塔斯的平衡之灾']
])child(a,b,g,name,aliases||[],a===2804?'board':'named');
for(const [a,b] of [[937,946],[1394,1401],[1548,1564],[1797,1805],[1954,1963]]){
  const base=parents.get(a);child(a,b,T,base.name+'秘闻',['秘密','暗线','传言'],'secret',base.name);
}
for(const [n,cat,names] of [[837,T,['父亲的沉默','橡树下的秘密']],[1791,T,lines[1790].split('、')],[1843,T,lines[1842].split('：').at(-1).split('、')],[1854,T,['父亲的沉默','橡树下的秘密']],[2843,T,lines[2842].split('：').at(-1).split('，')]]){
  const normalized=names.map(s=>s.trim().replace(/[。；，]$/,''));
  const row=child(n,n,cat,normalized[0],normalized.slice(1),n===2843?'board':'named');row.names=normalized;
}
for(const row of entries){
  if(row.name.includes('冒险者公会'))row.keys=distinct([...row.keys,'冒险者公会','冒险者协会']);
}
// Coalesce adjacent source lines without merging duplicate passages in distinct positions.
for(let n=1;n<=lines.length;n++){
  const row=positions.get(n);if(!row)continue;
  const last=row.spans.at(-1);
  if(last?.end===n-1)last.end=n;else row.spans.push({start:n,end:n});
}
function literal(node){
  if(node.type==='Literal')return node.value;
  if(node.type==='ArrayExpression')return node.elements.map(literal);
  if(node.type==='ObjectExpression'){
    const obj=Object.fromEntries(node.properties.map(p=>[p.key.name??p.key.value,literal(p.value)]));
    Object.defineProperty(obj,'line',{value:node.loc.start.line});return obj;
  }
  throw Error('Nonliteral region data');
}
const start=html.indexOf('{',html.indexOf('const regionData ='));
export const regionData=literal(parseExpressionAt(html,start,{ecmaVersion:2022,locations:true}));
function supplement(r,g,name,keys,text,obj,field,parent='',mode='named'){
  const row=add(r,g,name,keys,mode,parent);
  row.insert=anchors[r];row.body=text;row.origin={html:obj.line,field,id:obj.id};return row;
}
for(const [r,data] of Object.entries(regionData))for(const country of data.countries){
  supplement(r,P,country.name,[country.id],country.desc,country,'desc');
  if(country.detail)supplement(r,P,country.name,[country.id],country.detail,country,'detail');
  for(const loc of country.locations){
    locations.push({region:r,name:loc.name,country:country.name,id:loc.id});
    supplement(r,P,loc.name,[loc.id],loc.desc,loc,'desc',country.name,'city');
    if(!loc.detail)continue;
    // The authored list items supply tighter lookup keys than the entire city page.
    let heading='',count=0;
    for(const match of loc.detail.matchAll(/<h[345][^>]*>([^<]+)<\/h[345]>|<(li|p)>([\s\S]*?)<\/\2>/g)){
      if(match[1]){heading=match[1];continue;}
      const text=match[3],g=/NPC|人物/.test(heading)?C:/任务|委托/.test(heading)?T:/势力|组织/.test(heading)?F:P;
      const named=match[2]==='li'?short(text.replace(/<[^>]*>/g,'').split(/\s+[—–-]\s+/)[0]):'';
      const name=named&&!/守护此地|^可在|^这里|^可以/.test(named)&&named.length<=23&&/^[\p{Script=Han}·]+$/u.test(named)?named:loc.name;
      const keys=name==='冒险者公会'?['冒险者协会']:[];const person=text.match(/会长([\p{Script=Han}]+·[\p{Script=Han}]+)/u);
      if(person)keys.push(person[1]);
      const row=supplement(r,name==='冒险者公会'?F:g,name,keys,text,loc,'detail',loc.name,g===T?'board':'named');
      row.origin.offset=match.index+match[0].indexOf(text);count++;
    }
    if(!count)supplement(r,P,loc.name,[loc.id],loc.detail,loc,'detail',loc.name);
  }
}
export const omitted=[[1,2]];
export const sourceHash=manifest.system.sha256;
