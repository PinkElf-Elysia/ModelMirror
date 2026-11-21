import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';

const read = file => readFileSync(new URL('./resources/' + file, import.meta.url), 'utf8');
const book = JSON.parse(read('worldbook.json'));
const sourceMap = JSON.parse(read('source-map.json'));
const catalog = new Set([...sourceMap.works, ...sourceMap.builderOnly]);
const anchors = {
  人物: '## 关系系统', 社会: '八、势力体系', 战力: '三、突破规则', 单作战力: '二、突破规则',
  起源: '美国恐怖宇宙·世界总览', 事件: '📖 单作品宇宙作品库及命名规则'
};
const index = ['【世界书触发表】',
  '作品沿用原作总列表及序号。当前触发作品、可能/即将经历作品各填一个正式全名或「无」。仅有库名的作品没有独立事件正文。',
  '人物、势力和设定按下列规范名称点名。同名人物可用「作品／人物」区分。独立世界限本作，当前和预热事件仍须遵守信息迷雾。'];
index.push('建卡面板补充片名｜' + sourceMap.builderOnly.join('、'));
const sections = Map.groupBy(book.filter(e => !['作品','起源'].includes(e.kind)), e => e.group ?? e.kind);
for (const [group, rows] of sections) {
  const shared = book.filter(e => e.kind === '人物' && e.group !== group && e.works?.includes(group));
  index.push(group + '｜' + [...rows, ...shared].map(e => e.keys.join('／')).join('、'));
}
index.push('有事件正文｜' + book.filter(e => e.kind === '作品').map(e=>e.title).join('、'));
index.push('有前传背景｜' + book.filter(e=>e.kind==='起源').flatMap(e=>e.works).join('、'));
export function systemPrompt() { return read('system.txt') + '\n\n' + index.join('\n'); }

const groupKeys = new Set(['沃伦夫妇','温彻斯特兄弟']);
const byKey = new Map();
for (const e of book.filter(e=>!['作品','起源'].includes(e.kind))) {
  for (const key of e.keys) {
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(e);
  }
}
const escape = s => s.replace(/[.*+?^$(){}|[\]\\]/g,'\\$&');
const entityRx = new RegExp([...byKey.keys()].sort((a,b)=>b.length-a.length).map(escape).join('|'),'gu');
const family = s => /^(招魂|安娜贝尔|修女)\d+$/.test(s) ? '招魂宇宙' : s?.replace(/\d+$/,'');
const factionWorks = {
  '沃伦灵异调查事务所':['招魂宇宙'], '格林猎人家族':['格林'], '梅菲尔女巫家族':['梅菲尔女巫'],
  '米尔顿律所':['魔鬼代言人'], '勒马尔尚密教':['养鬼吃人'], '三角密教':['虚空异界'],
  '记录者协会':['邪恶力量'], '联邦异常事务调查局':['X档案','危机边缘'],
  '新月吸血鬼社群':['夜访吸血鬼'], '公路猎人驿站':['邪恶力量'],
  '梵蒂冈圣职驱魔团':['教皇的驱魔人','驱魔人'], '灵质科技联盟':['升级','屈从','隐形人','梅根']
};

function visible(raw, panels) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    if (['script','style','template','summary'].includes(node.tagName) || (!panels && node.tagName==='details')) return '\n';
    if (node.attrs?.some(a => a.name==='hidden'||(a.name==='aria-hidden'&&a.value==='true'))) return '\n';
    const value=(node.childNodes || []).map(visit).join('');
    return /^(p|div|br|li|pre|details|section|article)$/.test(node.tagName)?'\n'+value+'\n':value;
  }
  return visit(parseFragment(raw));
}
function work(value) {
  const raw=value.trim();
  if(catalog.has(raw)) return raw;
  const title=raw.match(/^《([^》]+)》/)?.[1] ?? raw.replace(/（(?:新英格兰|中大西洋|中西部|西南部|西海岸|南部|通用|海外)）$/,'');
  return catalog.has(title)?title:null;
}
function readState(history,input,opening) {
  const state={mode:null,current:null,next:null};
  function apply(raw,user) {
    const text=user?raw:visible(raw,true);
    const mode=text.match(/【世界模式】\s*(?:[🌌🎬]\s*)?(单作品宇宙|融合宇宙)/u)?.[1]
      ?? (user?text.match(/(?:切换到|开启|切换至|进入)(单作品宇宙|融合宇宙)/)?.[1]:null);
    if(mode) state.mode=mode;
    if(/当前触发作品[：:]/.test(text))state.next=null;
    for(const m of text.matchAll(/(当前触发作品|可能\/即将经历作品|即将经历作品)[：:]\s*([^\r\n]*)/g)) {
      const field=m[1]==='当前触发作品'?'current':'next';
      if(user && field==='next' && text.includes('【世界模式】'))state.current=null;
      state[field]=work(m[2].replace(/[*\x60]/g,'').trim());
    }
    if(user && !text.includes('【世界模式】')) {
      const choice=text.match(/(?:切换(?:作品)?(?:到|至|为)?|进入|选择作品|开局选|换到)\s*《([^》]+)》/);
      const chosen=choice?work(choice[1]):/^《[^》]+》$/.test(text.trim())?work(text):null;
      if(chosen){state.current=chosen;state.next=null;}
    }
  }
  if(opening)apply(opening,true);
  for(const m of history)if(['assistant','user'].includes(m.role))apply(m.content,m.role==='user');
  apply(input,true);
  return state;
}

export function selectEntries(history,input,opening='') {
  const state=readState(history,input,opening);
  const single=state.mode==='单作品宇宙';
  const active=state.current??state.next;
  const works=new Set([active,state.next].filter(w=>w&&(!single||family(w)===family(active))));
  const chosen=new Set();
  for(const w of works)for(const e of book)if(['作品','起源'].includes(e.kind)&&e.works.includes(w))chosen.add(e);
  const previous=history.findLast(m=>m.role==='assistant');
  const narrative=previous?visible(previous.content,false).replace(/\x60{3}[^]*?(?:\x60{3}|$)/g,' '):'';
  const sources=[input, narrative];
  if(!history.length && opening && opening!==input)sources.push(opening);
  for(const source of sources) {
    const text=source.replace(/(?:当前触发作品|可能\/即将经历作品|即将经历作品)[：:][^\n]*/g,' ').replace(/《[^》]*》/g,' ');
    for(const match of text.matchAll(entityRx)){
      const candidates=byKey.get(match[0]);
      const qualified=candidates.filter(e=>e.group&&text.slice(0,match.index).endsWith(e.group+'／'));
      const pool=qualified.length?qualified:candidates;
      for(const e of pool) {
        if(e.title==='融合宇宙实力阶位'&&single || e.title==='单作品实力阶位'&&!single)continue;
        if(single && e.kind==='势力' && !factionWorks[e.title]?.includes(family(active)))continue;
        if(single && ['民间个体与家族','地区派系','顶层组织'].includes(e.title))continue;
        if(e.group && match[0].length===1 && !qualified.length && !e.works?.includes(active))continue;
        if(e.group && (single || (!qualified.length && candidates.length>1 && !groupKeys.has(match[0]))) && !e.works?.includes(active))continue;
        chosen.add(e);
      }
    }
  }
  let remaining=12000;
  return [...chosen].filter(e=>{
    const cost=e.body.length+2;
    if(cost>remaining)return false;
    remaining-=cost;return true;
  });
}

export function assembleTurn(system, history, input, opening='') {
  const grouped=Map.groupBy(selectEntries(history,input,opening),e=>anchors[e.slot]);
  const content=system.split('\n').flatMap(line=>{
    const rows=grouped.get(line);
    if(!rows)return [line];
    grouped.delete(line);
    return [...rows.flatMap(e=>[e.body,'']),line];
  }).join('\n');
  if(grouped.size)throw Error('美恐世界书插入位置缺失');
  return {current:input,messages:[{role:'system',content},...history,{role:'user',content:input}]};
}
