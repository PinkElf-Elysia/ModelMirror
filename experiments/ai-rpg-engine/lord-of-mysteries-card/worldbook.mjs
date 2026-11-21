import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';

const read = name => readFileSync(new URL(name, import.meta.url));
const hash = value => createHash('sha256').update(value).digest('hex');
export const source = JSON.parse(read('./resources/prompts.json')).system;
const sourceHash = JSON.parse(read('./resources/MANIFEST.json')).system.sha256;
if (hash(source) !== sourceHash) throw Error('MYSTERIES_PROMPT_MISMATCH');
export const versionHash = () => hash(Buffer.concat([read('./worldbook.mjs'), read('./resources/prompts.json'),Buffer.from(read('./public/index.html').toString('utf8').match(tablePattern)[0].replaceAll('\r\n','\n'))]));
const lines = source.split('\n');
const html = read('./public/index.html').toString('utf8');
const tablePattern=/const artifactsDB = \{([\s\S]*?)\r?\n\};/;
const artifactTable = html.match(tablePattern);
if (!artifactTable) throw Error('MYSTERIES_ARTIFACT_TABLE_MISSING');
const entries = [], layout = [], names = new Map();
function register(category, name) {
  if (!names.has(category)) names.set(category, new Set());
  if (/\p{Script=Han}/u.test(name)) names.get(category).add(name);
}
let entry;
function begin(category, name, aliases = []) {
  register(category, name);
  entry = {id:'L' + line + '.' + entries.length, category, name, keys:[...new Set([name,...aliases].filter(Boolean))], parts:[]};
  entries.push(entry);
}
let line, col;
function piece(kind, text, field = '', keys = []) {
  if (!text) return;
  const part = {line, column:col, text};
  col += text.length;
  if (kind === 'fixed') layout.push({...part,kind});
  else {
    const index = entry.parts.length;
    entry.parts.push({...part, mode:kind, field, ...(keys.length ? {keys} : {})});
    layout.push({kind:'entry', entry:entry.id, index});
  }
}
const pub = text => piece('public',text);
const secret = (text,field,keys) => piece('secret',text,field,keys);
const split = (s, end, field) => { pub(s.slice(0,end)); secret(s.slice(end),field); };
const prefix = (s, pattern, field) => split(s, s.search(pattern) < 0 ? s.length : s.search(pattern), field);
const brackets = s => s.replace(/^[【]|[】]$/g,'');
const plainName = s => s.split('（')[0];
const fieldOf = s => s.replace(/^[-\s]+/,'').match(/^([^：:]{1,18})[：:]/)?.[1] || '档案正文';
const region = new Map([[914,'苏尼亚海'],[940,'狂暴海'],[957,'迷雾海'],[975,'北海'],[994,'极地海'],[1009,'灵界'],[1028,'星界'],[1036,'冥界'],[1047,'深渊']]);
const geoPublic = new Set([916,917,942,943,959,960,977,978,996,997,1011,1030]);
const landmarkLines=new Set([931,932,933,934,935,936,950,951,952,968,969,970,971,985,986,987,988,1000,1002]);
const publicLandmarks=new Set([931,932,933,934,935,1000,1002]);
const churchPublic = new Set([1756,1767,1778,1789,1800,1811,1822]);
const societyPublic = new Set([1834,1845,1856,1867,1877,1888,1900,1911,1922,1933,1945,1957,1968]);
let pathway, pathAliases, rank;

function ladder(s) {
  const members = [...s.matchAll(/([\p{Script=Han}·]+)\s*[(（]([0-9])[)）]/gu)];
  for (const m of members) register('序列',m[1]);
  // Store each original ladder under its own path. Individual rank names remain
  // valid selectors without exposing the path's other ranks or its source.
  if (members.length) {
    const first = members.find(m=>m[2]==='9')?.[1];
    if (first) register('途径', first+'途径');
    let start=0;
    for (const m of members) {
      piece('structure',s.slice(start,m.index));
      const keys=[m[1],...(m[2]==='9'?[m[1]+'途径']:[])];
      secret(m[0],'序列位阶',keys);
      start=m.index+m[0].length;
    }
    piece('structure',s.slice(start));
  } else secret(s,fieldOf(s));
}
function classify(s) {
  if (line<163 || line>2245 || (line>=1614 && line<=1618)) return;
  if (line===832 || line===851 || line===899) begin('世界法则',({832:'本源与修行规则',851:'高序列层级',899:'九大源质总览'})[line]);
  if ((line>=832&&line<=836)||(line>=851&&line<=854)||(line>=899&&line<=900)) return secret(s,fieldOf(s));
  if (line===880) {begin('地区','北大陆');pub(s);return;}
  if (line<=906) return piece('fixed',s);
  if (line===907) {
    begin('源质','混沌海');
    for (const part of s.split(/(?= - )/)) ladder(part);
    return;
  }
  if (line>=910 && line<=912) {
    const name = plainName(s);
    begin('地区',name,line===911?['神弃之地']:[]);
    for (const [i,part] of s.split(/(?= - )/).entries()) {
      if (!i) pub(part);
      else if (i===1) prefix(part,/[，；。]/,'地理位置与边界');
      else {
        const label=fieldOf(part);
        const city=['东拜朗','西拜朗','高地王国','帕斯王国','哈加提王国','罗思德群岛','白银城','月城','巨人王庭'].includes(label);
        const sect=['灵教团','玫瑰学派','天师道','嵩里冥道','佛门传承'].includes(label);
        if(city||sect) {
          const parent=entry;begin(city?'地区':'教会与组织',label);
          split(part,part.indexOf('：'),'档案正文');entry=parent;
        }else secret(part,label);
      }
    }
    return;
  }
  if (!s.trim() || /^(-{3,}|[\x60]{3}(markdown)?)$/.test(s.trim())) return piece('structure',s);
  if (line<1058) {
    if (region.has(line)) {begin('地区',region.get(line));return pub(s);}
    if(landmarkLines.has(line) && s.includes('：')) {
      const parent=entry, name=s.slice(2,s.indexOf('：'));
      begin('地区',name, line===931?['拜亚姆','香料群岛']:[]);
      if(publicLandmarks.has(line))prefix(s,/[，；。]/,'地点档案');
      else {pub(s.slice(0,s.indexOf('：')));secret(s.slice(s.indexOf('：')),'地点档案');}
      entry=parent;return;
    }
    if (geoPublic.has(line)) return prefix(s,/[，；。]/,fieldOf(s));
    return secret(s,fieldOf(s));
  }
  if (line<1311) {
    if (/^[\p{Script=Han}]+$/u.test(s)) {begin('源质',s);return pub(s);}
    return ladder(s);
  }
  if (line<1614) {
    if (s.includes(' · ') && s.endsWith('途径')) {
      const variants = s.split(' · ')[1].replace(/途径$/,'').split(/[（）]/).filter(Boolean);
      pathway = lines[line].match(/^序列9\s+([^·]+)/)[1]+'途径';
      pathAliases = variants.map(v=>v+'途径');
      begin('途径',pathway,pathAliases);
      return secret(s,'途径归属');
    }
    const header = s.match(/^序列(\d)\s+([^·]+)/);
    if (header) {
      rank = Number(header[1]);
      begin('序列',header[2],rank===9?[pathway,...pathAliases]:[]);
      if (rank===9) return split(s, header[0].length,'途径归属');
      return secret(s,'途径归属');
    }
    if (rank===9 && s.startsWith('核心能力：')) return piece('character',s,'核心能力');
    return secret(s,fieldOf(s));
  }
  if (line<1697) {
    if (s.startsWith('【')) {begin('种族与存在',brackets(s));return pub(s);}
    return secret(s,fieldOf(s));
  }
  if (line<1712) {
    if (s.startsWith('【')) {
      const [,code,name] = s.match(/【(\d-\d+) (.+)】/);
      begin('封印物',name,[code,code.replace('-','－'),code.replace('-','‑')]); return pub(s);
    }
    if (s.startsWith('基础信息：')) {
      const parts=s.split(/(?= \| )/);
      pub(parts[0]);secret(parts[1],'特性来源');secret(parts[2],'持有记录');return;
    }
    if (s.startsWith('外观形态：')) return prefix(s,/，/,'外观细节');
    return secret(s,fieldOf(s));
  }
  if (line<1752) {
    if(s.startsWith('【')) {begin('历史',brackets(s).replace(/.* · /,''));return pub(s);}
    return secret(s,fieldOf(s));
  }
  if (line<1980) {
    if (/^[\p{Script=Han}（）【】]+$/u.test(s)) {
      const raw=brackets(s);begin('教会与组织',plainName(raw),[raw]);return pub(s);
    }
    if (churchPublic.has(line)||societyPublic.has(line)) return prefix(s,/[，；。]/,fieldOf(s));
    return secret(s,fieldOf(s));
  }
  if (line<2075) {
    if (line===1980) return piece('structure',s);
    if (/^[\p{Script=Han}·（）]+$/u.test(s)) {
      const raw=plainName(s), name=['黑夜女神·阿曼妮西斯','死神·萨林格尔'].includes(raw)?raw.split('·')[0]:raw;
      begin('人物',name,[s,...(name!==raw?[raw.split('·')[1]]:[])]);
      return split(s,name.length,'身份');
    }
    return secret(s,fieldOf(s));
  }
  if (/^[\p{Script=Han}]+篇$/u.test(s)) {begin('篇章',s);return pub(s);}
  if (s.startsWith('时间锚点：')) return prefix(s,/[，。；]/,'时间锚点详情');
  return secret(s,fieldOf(s));
}

for (line=1;line<=lines.length;line++) {
  col=0;classify(lines[line-1]);
  if (line>=163 && line<=2245 && !(line>=1614&&line<=1618) && col!==lines[line-1].length)
    throw Error('Unmapped source line '+line);
}
// The opening form has a separate reference shelf that was never part of the
// system prompt. Its opt-in excerpts share an anchor after the two original files.
const shelf = [];
for (const m of artifactTable[0].matchAll(/\{name:'([^']+)', desc:'([^']+)', src:'([^']+)'\}/g)) {
  const [,label,description,attribution]=m, code=label.match(/^\d+-\d+/)?.[0];
  const title=label.replace(/^\d+-\d+\s+/,'');
  const name=plainName(title), aliases=title.match(/（([^）]+)）/)?.[1];
  register('封印物',name);
  const keys=[...new Set([name,label,code,code?.replace('-','－'),code?.replace('-','‑'),aliases,...(name==='特伦索斯特黄铜书'?['黄铜书']:[])].filter(Boolean))];
  const parts=[...description.matchAll(/[^。；]+[。；]?/g)].map((m,i)=>({text:m[0],field:'简档第'+'一二三四五六七八九十'[i]+'段'}));
  const sourceLine=html.slice(0,artifactTable.index+m.index).split('\n').length;
  shelf.push({category:'封印物',name,keys,label,description,attribution,sourceLine,parts,anchorAfter:1711});
}
if (shelf.length!==61) throw Error('MYSTERIES_ARTIFACT_TABLE_DRIFT');
const byId = new Map(entries.map(e=>[e.id,e]));
const order=['途径','序列','地区','教会与组织','封印物','源质','世界法则','种族与存在','历史','人物','篇章'];
export const catalog = [
  '【世界书检索】按当前场景使用下列名称。名字只用于定位资料，人物可知范围依信息迷雾规则判断。',
  '封印物首次只见公开资料。仅在剧情确已取得相应权限时，在可见正文独立一行记录“档案授权｜封印物名｜字段”；其他机密由当事人主动透露时记录“当事人告知｜名称｜字段”。可查字段如外观细节、特性来源、持有记录、核心能力、负面效应、封印规范、背景备注或简档第一段。续查时写出名称和字段。',
  ...order.map(c=>c+'　'+[...names.get(c)].sort(c==='篇章'?()=>0:(a,b)=>a.localeCompare(b,'zh-CN')).join('、'))
].join('\n');
function fixedLine(n){return (n>=163&&n<=906) && n!==880 && !(n>=832&&n<=836) && !(n>=851&&n<=854) && !(n>=899&&n<=900);}
export const fixed=lines.filter((_,i)=>fixedLine(i+1)).join('\n');
export const worldbook = {sourceHash, sourceFile:'resources/prompts.json → system', rules:{public:'当前输入、开局档案或上一轮可见叙事命中规范名或别名；篇章仅使用一个原名。',character:'仅已选定该序列的角色档案可随角色装配能力。',secret:'本轮含名称和字段，且先前主持人的可见叙事有同一名称和字段的当事人告知记录。封印物也接受档案授权记录。',structure:'保留原稿复原位置；单独命中不下发。',references:'其他档案中复述封印物的段落另外要求该封印物的关联记录授权。',supplementary:'表单简档每段单独授权。没有中文名的原编号可检索。'}, coordinate:'system 的 LF 行号从 1 起，UTF-16 列从 0 起；同一行按列拼接',catalogBeforeLine:832, supplementary:{source:'public/index.html / artifactsDB',anchorAfter:1711,records:shelf}, excluded:[[1,162],[1614,1618],[2246,lines.length]].map(([from,to])=>({from,to,sha256:hash(lines.slice(from-1,to).join('\n'))})),entries};
function visibleText(text) {
  return text.replace(/<(details|think|thinking|checking|pre|blockquote|script|style)\b[^>]*>[\s\S]*?(?:<\/\1>|$)/gi,'').replace(/[\x60]{3}[\s\S]*?(?:[\x60]{3}|$)/g,'');
}
function receipts(history) {
  const grants=new Set();
  let precedingUser='';
  for (const m of history) {
    if (m.role==='user') precedingUser=m.content;
    if (m.role!=='assistant'||typeof m.content!=='string') continue;
    const visible=visibleText(m.content);
    for (const row of visible.split(/\r?\n/)) if (!precedingUser.includes(row) && /^(档案授权|当事人告知)｜[^｜<>]+｜[^｜<>]+$/.test(row)) grants.add(row);
  }
  return grants;
}
function mentionsKey(text,key) {
  if (/^\d+[-－‑]\d+$/.test(key)) return new RegExp('(?<![\\d-])'+key+'(?!\\d)').test(text);
  return text.includes(key);
}
function selectedChapter(text) {
  return [...names.get('篇章')].find(name=>text.includes(name));
}
export function assembleWorldbook({opening='',history=[],input=''}) {
  const grants=receipts(history), first=history.find(m=>m.role==='user')?.content||'';
  const chapter=selectedChapter(input)||selectedChapter([...history].reverse().map(m=>m.role==='assistant'?visibleText(m.content):m.content).find(t=>selectedChapter(t))||'')||selectedChapter(opening);
  const previous=history.filter(m=>m.role==='assistant').at(-1)?.content||'';
  const mentions=[input,opening,first,visibleText(previous)].join('\n');
  const dossier=opening||first||input;
  const ownsRank=e=>[...dossier.matchAll(/【(?:主修途径|外神恩赐)】\s*[^\n]*序列9「([^」]+)」/g)].some(m=>m[1]===e.name);
  const active=new Set(),hits=[];
  for (const e of entries) {
    const named=e.category==='篇章'?e.name===chapter:e.keys.some(k=>mentionsKey(mentions,k));
    for(const [index,p] of e.parts.entries()) {
      if (p.mode==='structure') continue;
      let allow=(p.mode==='public' && named)||(p.mode==='character' && named && ownsRank(e));
      if ((p.mode==='secret'||p.mode==='character') && input.includes(p.field)) {
        const owners=(p.keys||e.keys).filter(k=>mentionsKey(input,k));
        allow ||= owners.some(k=>grants.has('当事人告知｜'+k+'｜'+p.field) ||
          (e.category==='封印物' && grants.has('档案授权｜'+e.name+'｜'+p.field)));
      }
      if (allow && e.category!=='封印物' && p.mode==='secret') {
        // A chapter, faction or character may repeat a sealed artefact's secret.
        const related=shelf.filter(a=>a.keys.some(k=>p.text.includes(k)));
        allow=related.every(a=>grants.has('档案授权｜'+a.name+'｜关联记录')||grants.has('当事人告知｜'+a.name+'｜关联记录'));
      }
      if (allow) {active.add(e.id+':'+index);hits.push({line:p.line,column:p.column,category:e.category,name:e.name,field:p.field||'公开'});}
    }
  }
  const out=new Map();
  for (let n=163;n<=906;n++) if (fixedLine(n)) out.set(n,lines[n-1]);
  for (const ref of layout) if (ref.kind==='entry'&&active.has(ref.entry+':'+ref.index)) {
    const p=byId.get(ref.entry).parts[ref.index];out.set(p.line,(out.get(p.line)||'')+p.text);
  }
  const extra=[];
  for(const a of shelf) {
    const named=a.keys.some(k=>mentionsKey(mentions,k));
    if (!named) continue;
    if (!['许愿神灯','阿勒苏霍德之笔'].includes(a.name)) {
      extra.push(a.label);hits.push({source:'public/index.html',line:a.sourceLine,name:a.name,category:'封印物',field:'公开',anchorAfter:1711});
    }
    for(const p of a.parts) {
      if (a.keys.some(k=>mentionsKey(input,k)) && input.includes(p.field) &&
          ['档案授权','当事人告知'].some(method=>grants.has(method+'｜'+a.name+'｜'+p.field))) {
        extra.push(a.name+' '+p.text);hits.push({source:'public/index.html',line:a.sourceLine,name:a.name,category:'封印物',field:p.field,anchorAfter:1711});
      }
    }
  }
  if(extra.length)out.set(1711,(out.get(1711)?out.get(1711)+'\n':'')+extra.join('\n'));
  out.set(831,out.get(831)+'\n'+catalog);
  return {system:[...out].sort(([a],[b])=>a-b).map(([,s])=>s).join('\n'),hits,chapter};
}
