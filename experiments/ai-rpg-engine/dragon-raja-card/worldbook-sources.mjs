import {parseExpressionAt} from 'acorn';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read=name=>readFileSync(new URL('./resources/'+name,import.meta.url),'utf8');
export const sha=s=>createHash('sha256').update(s).digest('hex');
const manifest=JSON.parse(read('MANIFEST.json'));
export const source=JSON.parse(read('prompts.json')).system;
const html=read('original.html');
if(sha(html)!==manifest.frontend.sha256||sha(source)!==manifest.system.sha256)throw Error('DRAGON_SOURCE_CHANGED');
export const lines=source.split('\n');
export const entries=[];
export const chapters=[];
export const works=['前传','火之晨曦','悼亡者之瞳','黑月之潮','奥丁之渊','悼亡者的归来','异闻录','同人设定'];
// Only literal database fields are read. The author's page script is never executed by the host.
function database(name){
  const mark='const '+name, pos=html.indexOf(mark);
  if(pos<0)throw Error(mark);
  const node=parseExpressionAt(html,html.indexOf('=',pos)+1,{ecmaVersion:2022,locations:true});
  function unpack(n){
    if(n.type==='Literal')return n.value;
    if(n.type==='ArrayExpression')return n.elements.map(unpack);
    if(n.type==='ObjectExpression'){
      const obj=Object.fromEntries(n.properties.map(p=>[p.key.name??p.key.value,unpack(p.value)]));
      Object.defineProperty(obj,'line',{value:n.loc.start.line});
      return obj;
    }
    throw Error('Unexpected data expression in '+name);
  }
  return unpack(node);
}
const chapterDB=database('chaptersDatabase');
export const first='卡塞尔入学篇',beijing='大地与山之王篇',shilan='仕兰归乡篇',tokyo='东京黑月篇';
const campaign=['卡塞尔入学篇','夔门初探篇','冰窖之乱篇','青铜计划篇',shilan,beijing,'学院风云篇',tokyo,'学院风云篇','昆古尼尔篇','全球追杀篇'];
const volume=[1,1,1,1,2,2,2,3,3,4,5];
const unique=a=>[...new Set(a.filter(Boolean))];
function add(group,name,keys,gate,slot,text,ref,more={}){
  const row={id:'r'+(entries.length+1),group,name,keys:unique([name,...keys]),gate,slot,text,ref,...more};
  entries.push(row);return row;
}
const at=rank=>({rank}),only=chapter=>({chapter});
const any={common:true};
for(const [family,list] of Object.entries(chapterDB))for(const c of list){
  const ranks=family==='main'?campaign.flatMap((s,i)=>s===c.name?[i]:[]):[null];
  for(const rank of ranks){
    const work=works[family==='main'?volume[rank]:family==='side'?6:0];
    const phase=c.name==='学院风云篇'?(rank===6?'第一沙盒期':'第二沙盒期'):'';
    const key=c.id+(phase?'-'+rank:'');
    const stage={key,id:c.id,name:c.name,year:c.year,phase,rank,work,tags:c.tags,desc:c.desc};
    chapters.push(stage);
    // The shared campus entry describes two periods. Include only the active period.
    const parts=c.desc.split('<br>');
    const text=phase?[parts[0],parts[rank===6?1:2],...parts.slice(3)].join('<br>'):c.desc;
    add('剧情',c.name,[c.id,...c.tags],only(key),756,text,{html:c.line,field:'chaptersDatabase.desc'},{title:c.name,phase,work,chapterEntry:true});
  }
}
export function eligible(e,s){
  const g=e.gate;
  if(g.common)return true;
  if(!s)return false;
  if(g.chapter)return g.chapter===s.key;
  if(g.anyOf)return g.anyOf.includes(s.key);
  if(g.fan)return false; // Original optional fan abilities require explicit player selection.
  if(g.fromPrequel&&s.key===g.fromPrequel)return true;
  const rank=s.work==='异闻录'?(s.key==='son-in-law'?8:6):s.rank;
  return Number.isInteger(rank)&&Number.isInteger(g.rank)&&rank>=g.rank;
}
const pre=['summer-mourning','black-swan','greenland-ice-sea','rain-fall'];
const side=chapterDB.side.map(c=>c.id);
function characterGate(i){
  if(i>=102)return only(side[([102,108,109].includes(i)?0:[103,104,110].includes(i)?1:i>=105&&i<=107?2:i<=112?3:i<=115?4:5)]);
  if(i>=61&&i<=70||i===91)return only(pre[0]);
  if(i>=71&&i<=74||i===92)return only(pre[1]);
  if(i>=75&&i<=82||[10,11,18,19,39].includes(i))return at(10);
  if(i>=40&&i<=57||[16,17,89,98,100].includes(i))return at(7);
  if([6,32,37,96,101].includes(i))return at(5);
  if([30,97].includes(i))return {rank:5,fromPrequel:pre[3]};
  if([31,38,88].includes(i))return at(4);
  if(i===90)return at(9);
  if([15,28,29].includes(i))return at(1);
  if([93,94,12,59,60].includes(i))return at(2);
  if([58,95].includes(i))return at(3);
  return at(0);
}
const names={0:'路明非',1:'芬格尔',2:'恺撒',20:'昂热',23:'曼施坦因',24:'施耐德',25:'曼斯',33:'阿卡杜拉',63:'鬼',71:'赫尔佐格'};
const extras={0:['李嘉图·M·路'],1:['肯德基先生'],2:['凯撒','凯撒·加图索'],3:['面瘫师兄'],9:['兰斯洛特'],21:['弗拉梅尔'],83:['泽太子']};
for(const [i,c] of database('canonChars').entries()){
  const variants=c.name.split('/'),name=names[i]||variants.find(v=>/^[\p{Script=Han}·]+$/u.test(v));
  if(!name)throw Error(c.name);
  const keys=[c.name,...variants,...extras[i]||[]],gate=characterGate(i);
  const ref={html:c.line,field:'canonChars['+i+'].desc'};
  if(i===0){
    for(const [j,part] of c.desc.split('、').entries())add('人物',name,keys,at([0,9,3,3][j]),772,part,ref,{title:name});
  }else add('人物',name,keys,gate,772,c.desc,ref,{title:name});
}
export const spiritPolicies=[];
const spiritDB=database('wordSpiritDB');
const spiritRanks={1875:4,1876:7,1879:4,1882:7,1883:5,1889:7,1890:7,1891:7,1892:7,1893:10,1895:2,1901:5,1903:7,1904:7,1905:7,1907:7,1908:5,1909:7,1911:7,1914:3,1915:5,1916:7,1918:3,1919:10};
for(const [family,list] of Object.entries(spiritDB))for(const item of list){
  const sideStory=chapterDB.side.find(c=>item.user?.includes(c.name));
  const gate=family.startsWith('fan')?{fan:true}:sideStory?only(sideStory.id):at(spiritRanks[item.line]||0);
  const keys=['言灵·'+item.name,'言灵'+item.name,...(item.seq&&item.seq!=='—'?['序列号'+item.seq,'Seq.'+item.seq]:[])];
  const aliases={雷池:'惊蛰',无尘之地:'离垢净土'};
  if(aliases[item.name])keys.push(aliases[item.name]);
  const split=item.line===1902?item.desc.indexOf('，与风王之瞳'): -1;
  const description=split<0?item.desc:item.desc.slice(0,split);
  add('言灵',item.name,keys,gate,682,description,{html:item.line,field:'wordSpiritDB.'+family+'.desc'},{title:item.name,variant:family.startsWith('fan')?'同人设定':sideStory?'异闻录':'',selection:family.startsWith('fan')});
  if(split>=0)add('言灵',item.name,keys,at(5),682,item.desc.slice(split),{html:item.line,field:'wordSpiritDB.high.desc',from:split},{title:item.name});
  const representativeRank={1873:7,1875:5,1886:5,1902:0,1916:7}[item.line];
  const userGate=representativeRank===undefined?gate:at(representativeRank);
  if(item.user)add('言灵',item.name,keys,userGate,682,item.user,{html:item.line,field:'wordSpiritDB.'+family+'.user'},{title:item.name+' · 代表',variant:sideStory?'异闻录':''});
  const byChapter={};
  for(const c of chapters){
    const allowed=gate.fan||eligible({gate},c);
    const desc=allowed?(split>=0&&!eligible({gate:at(5)},c)?description:item.desc):'';
    byChapter[c.key]=[desc===item.desc?true:desc,!!item.user&&eligible({gate:userGate},c)];
  }
  spiritPolicies.push({name:item.name,desc:item.desc,user:item.user,byChapter});
}
// Source slices keep DOCX order, including mixed-era passages within the same sentence.
const charter=[['狮心会','组织',3,'狮心会会长',0],['学生会','组织',2,'学生会主席',0],['加图索家族','组织',34,'加图索家主',0],['蛇岐八家','组织',40,'蛇岐八家大家长',7],['猛鬼众','组织',41,'猛鬼众龙王',7],['新生联谊会','组织',8,'新生联谊会会长',0]];
const roster=database('canonChars');
for(const [name,group,i,phrase,rank] of charter){
  if(!roster[i].desc.includes(phrase))throw Error(name);
  add(group,name,[name],at(rank),696,phrase,{html:roster[i].line,field:'canonChars['+i+'].desc'},{title:roster[i].name.split('/')[0]});
}
export const spans=new Map();
export function clip(n,a,b,group,name,keys,gate=any){
  const line=lines[n-1],from=typeof a==='string'?line.indexOf(a):a;
  const end=typeof b==='string'?line.indexOf(b,from)+b.length:b;
  if(from<0||end<=from||end>line.length)throw Error('Bad source span at '+n);
  const row=add(group,name,keys,gate,n,line.slice(from,end),{line:n,from,to:end});
  const same=spans.get(n)||[];
  if(same.some(x=>from<x.ref.to&&end>x.ref.from))throw Error('Overlapping source spans at '+n);
  same.push(row);spans.set(n,same);return row;
}
export const whole=(n,...args)=>clip(n,0,lines[n-1].length,...args);
export const phrase=(n,t,...args)=>clip(n,t,t,...args);
