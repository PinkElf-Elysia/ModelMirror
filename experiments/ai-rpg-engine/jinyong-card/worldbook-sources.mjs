import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {parseExpressionAt} from 'acorn';

const read=file=>readFileSync(new URL(file,import.meta.url),'utf8');
export const digest=text=>createHash('sha256').update(text).digest('hex');
export const source=read('resources/system.txt'), html=read('resources/original.html');
const manifest=JSON.parse(read('resources/MANIFEST.json'));
if(digest(source)!==manifest.system.sha256 || digest(html)!==manifest.sourceHtml.sha256)throw Error('JINYONG_SOURCE_CHANGED');
const lines=source.split('\n');
export const FUSION='金庸群侠传',TL='天龙八部',SD='射雕英雄传',SC='神雕侠侣',COMMON='通用';
export const entries=[],works=[],stages={};
function entry(work,category,name,keys,parts,extra={}){
  entries.push({id:'J'+(entries.length+1),work,category,name,keys:[...new Set([name,...keys])],parts,...extra});
}
function exact(n,text=lines[n-1],column=lines[n-1].indexOf(text)){
  if(column<0||lines[n-1].slice(column,column+text.length)!==text)throw Error('Wrong source position at '+n);
  return {source:'resources/system.txt',line:n,column,text};
}
function span(a,b){return Array.from({length:b-a+1},(_,i)=>exact(a+i));}
// Parse only literal table fields. No code from the author page is evaluated.
function expression(marker,from=0){
  const at=html.indexOf(marker,from);if(at<0)throw Error('Missing source '+marker);
  return parseExpressionAt(html,at+marker.length,{ecmaVersion:2022,locations:true});
}
function prop(obj,key){const p=obj.properties.find(p=>(p.key.name??p.key.value)===key);if(!p)throw Error('Missing table field '+key);return p.value;}
function literal(node){
  const text=node.value;if(typeof text!=='string'||html.slice(node.start+1,node.end-1)!==text)throw Error('Unexpected authored literal');
  return {source:'resources/original.html',line:node.loc.start.line,column:node.loc.start.column+1,text};
}
const people=new Set('乔峰 段誉 虚竹 郭靖 黄蓉 杨过 小龙女 独孤 张无忌 令狐冲 狄云 戚芳 袁承志 金蛇郎君 石破天 陈家洛 乾隆 霍青桐 胡斐 苗人凤 程灵素 药王 韦小宝 康熙 李文秀 阿青 越女'.split(' '));
const places=new Set('大理 桃花岛 古墓 襄阳 光明顶 华山 雪谷 侠客岛 回部 边地 雪山 绝壁 西域 官道 吴越'.split(' '));
const sects=new Set('少林 丐帮 明教 六大派 日月神教 五岳剑派 血刀门 长乐帮 红花会 天地会 神龙教 清廷 镖局'.split(' '));
const arts=new Set(['九阴真经','独孤九剑']);
for(const group of expression('const worksDatabase = ').properties){
  for(const item of prop(group.value,'works').elements){
    const work=prop(item,'name').value;
    works.push(work);stages[work]=[];
    entry(work,'作品',work,[],[literal(prop(item,'name')),literal(prop(item,'desc'))],{rule:'work'});
    for(const kw of prop(item,'keywords').elements){
      const k=kw.value,category=people.has(k)?'人物':places.has(k)?'地点':sects.has(k)?'门派与势力':arts.has(k)?'武学':'剧情线索';
      const name=({独孤:'独孤求败',越女:'阿青'})[k]??k;
      entry(work,category,name,[k],[literal(kw)]);
    }
    for(const stage of prop(item,'stages').elements){
      const n=prop(stage,'name'),period=prop(stage,'year');
      stages[work].push(n.value);
      entry(work,'剧情阶段',n.value,[period.value,prop(stage,'id').value],[literal(n),literal(period)],{rule:'stage',stage:n.value});
    }
    for(const f of prop(item,'factions').elements)if(f.value!=='自定义...')entry(work,'门派与势力',f.value,[],[literal(f)]);
    for(const a of prop(item,'abilities').elements){const n=prop(a,'name');entry(work,'武学',n.value,[],[literal(n),literal(prop(a,'desc'))]);}
  }
}
if(works.length!==15)throw Error('Incomplete authored works');
for(const reg of expression('const fusionRegions = ').elements){
  const name=prop(reg,'name').value;
  entry(FUSION,'地点',name,[],['name','short','detail'].map(k=>literal(prop(reg,k))),{region:name});
  for(const [k,c] of [['tags','门派与势力'],['story','剧情线索']])for(const n of prop(reg,k).elements)
    entry(FUSION,n.value==='无量山'?'地点':c,n.value,[],[literal(n)],{region:name});
}
const factionsStart=html.indexOf('function getFusionFactionGroups');
for(const block of ['globalFactions','civilFactions'])for(const n of expression('const '+block+' = ',factionsStart).elements)
  if(n.value!=='自定义...')entry(FUSION,'门派与势力',n.value,[],[literal(n)]);
for(const region of expression('const localMap = ',factionsStart).properties)for(const n of region.value.elements)
  entry(FUSION,'门派与势力',n.value,[],[literal(n)],{region:region.key.value});
for(const region of expression('const map = ',html.indexOf('function getFusionRegionAbilities')).properties)for(const a of region.value.elements)
  entry(FUSION,'武学',prop(a,'name').value,[],['name','desc'].map(k=>literal(prop(a,k))),{region:region.key.value});

entry(TL,'作品',TL,[],span(773,781),{rule:'work'});
const groupNames=new Set(['函谷八友','梅兰竹菊四剑','三十六洞洞主七十二岛岛主','朱碧双姝']);
for(const m of lines[782].matchAll(/(?:[^，（。（）]|（[^）]*）)+/gu)){
  const raw=m[0],base=raw.split('（')[0],note=raw.match(/（(.+)）/)?.[1];
  const name=({'徐长老':'徐冲霄','王夫人':'李青萝','吴长老':'吴长风','陈长老':'陈孤雁','神农帮帮主司空玄':'司空玄','西夏银川公主李清露':'李清露','芙蓉仙子崔绿华':'崔绿华'})[base]||base;
  const keys=[base];if(note && !/所属|丫鬟|方丈|首座|原主|首領|玉像原型|梵僧/.test(note))keys.push(...note.split('、').map(s=>s.replace(/等$/,'')));
  if(base.includes('、'))keys.push(...base.split('、'), '左子穆');
  entry(TL,'人物',base==='无量剑宗左子穆、辛双清'?'左子穆':name,keys,[exact(783,raw,m.index)]);
  if(groupNames.has(base)||base.includes('、')){
    for(const member of (groupNames.has(base)?keys.slice(1):['辛双清']))entry(TL,'人物',member,[],[exact(783,member,m.index+raw.indexOf(member))]);
  }
}
entry(TL,'武学','武功体系',['装备与武功体系'],span(785,787));
for(const [name,word] of Object.entries({'逍遥派':'逍遥派','少林':'少林','大理段氏':'大理段氏','丐帮':'丐帮','星宿派':'星宿派','吐蕃密宗':'吐蕃密宗','慕容氏':'慕容氏'}))entry(TL,'门派与势力',name,[],[exact(787,word)]);
for(let n=789;n<=817;n++)if(lines[n-1].trim()){
  const raw=lines[n-1].slice(2).trim(),name=raw.split('（')[0],alias=raw.match(/（(.+)）/)?.[1];
  entry(TL,'武学',name,alias?[alias]:[],[exact(n)]);
}
for(let n=821;n<=837;n+=4){
  const name=lines[n-1].split('·')[1].split('（')[0];
  entry(TL,'剧情篇章',name,[lines[n-1],lines[n-1].split('·')[0]],span(n,n+2),{rule:'chapter',minStage:[0,1,1,2,2][(n-821)/4]});
}
// Names found only inside narrative retain their source position; a name lookup never activates its chapter.
for(const [category,names] of [['地点','大理 无锡城 西夏皇宫 缥缈峰 灵鹫峰 雁门关外 无量山 琅嬛福地 天龙寺 燕子坞 参合庄 松鹤楼 杏子林 雁门关 聚贤庄 少林寺 灵鹫宫 少室山 姑苏 藏经阁'],['门派与势力','神农帮 无量剑派 少林寺 西夏一品堂 灵鹫宫'],['人物','萧远山 段誉 乔峰 萧峰 虚竹 李清露']]){
 for(const name of names.split(' ')){const n=[823,827,831,835,839].find(n=>lines[n-1].includes(name));if(n)entry(TL,category,name==='萧峰'?'乔峰':name,[name],[exact(n,name)]);}
}
entry(TL,'剧情线索','衔接提示',['前作','后作'],span(841,845));
entry(SC,'人物','神雕',['巨雕','独孤求败','杨过','玄铁剑法'],[exact(847)],{minStage:2});
entry(SC,'武学','玄铁剑法',[],[exact(847,'玄铁剑法')],{minStage:2});
const rankWorks={
 '江南七怪':[SD],'黄河四鬼':[SD],'大小武':[SC],'郭芙':[SC],'青城四秀':['笑傲江湖'],'扫地僧':[TL],'张三丰':['倚天屠龙记'],'王重阳':[SD],'风清扬':['笑傲江湖'],
 '东邪西毒南帝北丐':[SD],'郭靖':[SD,SC],'杨过':[SC],'张无忌':['倚天屠龙记'],'任我行':['笑傲江湖'],'全真七子':[SD,SC],'武当七侠':['倚天屠龙记'],'日月神教长老':['笑傲江湖'],'霍都':[SC],'沙通天':[SD],'彭连虎':[SD],'灵智上人':[SD],'海沙帮帮主':['倚天屠龙记'],'龙门镖局总镖头':['倚天屠龙记']
};
for(const [n,rank] of [[851,'三流高手'],[854,'四流高手'],[857,'不入流'],[864,'宗师高手'],[867,'绝顶高手'],[870,'一流高手'],[873,'二流高手']]){
 for(const m of lines[n-1].matchAll(/(?<=：|、)[^、。]+/g)){
  const name=m[0].split('（')[0];
  for(const work of rankWorks[name]||[COMMON])entry(work,'人物',name,[],[exact(n,m[0],m.index)],{rank,...(name==='郭靖'&&work===SD||name==='杨过'?{minStage:2}:{})});
 }
}
for(const [i,m] of [...lines[859].matchAll(/[^，。]+/g)].entries()){
 if(i===4)continue;
 const keys=i===0?['五绝','丘处机']:i===1?['丘处机','沙通天']:i===2?['沙通天','江南七怪']:['霍都','大小武'];
 entry(i===3?SC:SD,'人物',keys[0],keys,[exact(860,m[0],m.index)]);
}
entry(SD,'人物','丘处机',[],[exact(869,'丘处机为一流顶标杆。')]);
entry(FUSION,'作品',FUSION,['融合宇宙'],span(875,907),{rule:'work'});
for(let n=909;n<=913;n++){const name=lines[n-1].slice(2).split('：')[0];entry(FUSION,'地点',name,[],[exact(n)],{region:name});}
entry(FUSION,'作品',FUSION,[],span(915,946),{rule:'work'});
for(const [name,a,b,keys] of [['神功秘宝',948,968,['神功','秘笈','秘宝','绝学']],['江湖生计',970,981,['生计','百姓','集市','渡口','镖局']],['货币',983,989,['钱','银','文']],['日常物价',991,997,['物价','客栈','马匹']],['兵器物价',999,1004,['兵器','刀剑']],['药品物价',1006,1011,['药品','买药']],['人力工钱',1013,1018,['人力','工钱']],['情报价格',1020,1025,['情报']],['大宗交易',1027,1031,['交易','商队']]])entry(FUSION,'江湖设定',name,keys,span(a,b));
for(let n=1035;n<=1039;n++){const name=lines[n-1].slice(2).split('：')[0];entry(FUSION,'地点',name,[],[exact(n)],{region:name});}
export const fixedRows=new Map();
for(let n=25;n<=772;n++)fixedRows.set(n,lines[n-1]);
for(const n of [849,850,852,853,855,856,858,862,863,865,866,868,869,871,872,874])fixedRows.set(n,n===869?lines[n-1].split('，丘处机')[0]:lines[n-1]);
export const fixed=[...fixedRows.values()].join('\n');
