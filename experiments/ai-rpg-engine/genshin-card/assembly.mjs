import {readFileSync} from 'node:fs';
import {parseFragment} from 'parse5';

const read = file => readFileSync(new URL('./resources/' + file, import.meta.url), 'utf8');
const prefix = read('prefix.txt');
const suffix = read('suffix.txt');
const spans = JSON.parse(read('worldbook.json'));
const byStart = new Map(spans.map(span => [span.start, span]));
const groups = new Map();
for (const {group, keys} of spans) {
  if (!keys.length) continue;
  if (!groups.has(group)) groups.set(group, new Set());
  groups.get(group).add(keys.join('／'));
}
export const triggerIndex = ['## 提瓦特世界书 · 规范名称与触发表',
  '每组以首项为规范触发词，其余为别名。人物、地点和设定按当前场景查阅，遵守原文的视角与迷雾规则。',
  '蒙德：砂糖作食材、琴作乐器、风神像均不调入对应角色。杜林、尼可、布伦妮仅点名时取蒙德档案，明确提及魔女会时才追加对应魔女会条目。',
  '璃月：三项以“璃月·”开头的名称也可省略间隔号。“七星”须有璃月语境，星象不调入组织；药材、木料、成语、岩神像等非人物用法不调入相应人物。',
  '稻妻：日常点名雷电将军取人物档案，明确指向她的周本、Boss或挑战才追加周本条目。雷神像和普通词义不启用角色，幕府只在稻妻语境启用，稻妻·潜伏者和稻妻·其他可省略间隔号。',
  '须弥：日常点名流浪者或散兵取人物档案，普通身份及“散兵游勇”不调用。正机之神调入设定，明确指向它的周本、Boss或挑战再追加周本条目。草神像不启用人物，须弥·沙漠和须弥·其他可省略间隔号。',
  '枫丹：壁炉之家主人、执行官仆人指阿蕾奇诺，明确谈壁炉之家组织才取势力。水龙王作称号取那维莱特，泛称取龙王补充。审判庭、公爵限枫丹语境；水神像不启用人物，两处枫丹·名称可省略间隔号。',
  '纳塔：纳塔六大部族调齐六族，点名单族取该条。归火圣夜巡礼（圣夜巡礼）日常取纳塔机制，各国节庆或跨国比较才追加各国节日。火神像、地名旧称、燃素说、异世界的火之国、伊法尔均排除对应条目。',
  '至冬：冰神像、瞳色灰眸、季节范围、影视雪国列车和普通射击游戏排除对应条目。灰眸专名取两处。达达利亚和执行官公子日常只取人物；点明其周本、Boss或挑战才追加公子周本，另谈愚人众或执行官群体才调组织。旅行者切换冰元素同冰元素旅行者。',
  '挪德卡莱：英文名兼容大小写、空格及连字符。霜月神殿只取地点，银月之庭保留两处；远征军另谈母组织才取西风骑士团。魔女会当地条目限本地场景。黑森林食品和德国地名、巫师3的狂猎、三月女神节与摄影域名排除。影域全称与简称均只取影域条目。',
  '旅行者：以完整规范名点查时只取指定条目。派蒙日常取基础档案，身世或天理关系加核心。七天神像取地图，共鸣或切换元素加形态。降临者泛称取天理与降临者，旅行者的降临者身份加核心。世界树排除其他作品。',
  '元素与地脉：普通反应名可点查，日常词义逐处排除；月绽放、月感电取月曜，明确比较普通反应时追加全表。星超导、星扩散只取星烁体系详解。',
  '深渊教团与坎瑞亚：深渊作专名需提瓦特语境，排除地形。狼王、特瓦林、阿佩普、雷内、水形幻人日常只取设定，指向该目标的周本、Boss或挑战再取战斗档案。独立点查深渊教团分类、神之心机制只取正文，神之心仍取两处。',
  '愚人众执行官：裸席号不指人。完整头衔只调人物，公鸡、富人的头衔调席位总表。另谈组织或席位再追加。统括官限愚人众语境，丑角排除戏曲。女士周本按目标追加，邪眼单点取补充词条，谈执行官邪眼或力量来源加全席补充。点查全席补充只取该条。',
  '魔女会：魔女会加 A/R/M/B 取成员表，N 取尼可双档，裸字母不调。黄金限魔女会或炼金术士称号，艾莉丝日常取组织、问代号加成员。魔女会逸闻只取逸闻。同名人物用“点查魔女会篇·规范名”单取专档。',
  '天理四影：四位执政的职称分别取人物；天理四影、名单或权能分工取总表，点名成员追加人物，明确谈与天理的关系再取天理档案。天理成语、纳贝长名及苏格兰天空岛排除。',
  '世界Boss与秘境：若陀日常取龙王补充，明确挑战再加战斗条目。守望者与堕天须指对应Boss，原初人类须在提瓦特语境，遗迹系列须指提瓦特敌人。愚人众先遣队问隶属取组织，点名或问打法取常规精英，两者都问取两处。旅游秘境排除。',
  '节日：稻妻祭名和枫丹歌剧节依地区识别；问三祭或荣花演武只取节庆，另问国家概况才加地区。海灯节日常或跨国比较取各国节日，历届主题和纪念对象取历届表，同时询问才双取。',
  '特殊机制：战力分级、战力等级限提瓦特战力讨论；地图及黑灰迷雾限提瓦特探索。物件磨损、宗教业障、品牌产品和民调好感度排除。邪眼或死之诅咒明确问代价、副作用或后果，追加代价机制。',
  '零散条目：白夜国取渊下宫，仙人和魔神同仙人与魔神。风仙和树妖须指至冬妖精或提瓦特族群。族群名排除其他作品与现实民俗。人物点名取人物，明确问其种族或血统时追加对应族群，已审的多处命中照旧。',
  ...Array.from(groups, ([group, rows]) => '【' + group + '】' + [...rows].join('；'))
].join('\n');

const skipTags = new Set('details summary script style think thinking template iframe table aside form'.split(' '));
function sceneText(raw) {
  function visit(node) {
    if (node.nodeName === '#text') return node.value;
    const attrs = node.attrs || [];
    if (skipTags.has(node.tagName) || attrs.some(a =>
      a.name === 'hidden' || a.name === 'aria-hidden' && a.value === 'true' ||
      a.name === 'class' && /(?:hero-bar|status|modal|memory|canshu|panel)/i.test(a.value))) return '\n';
    const text = (node.childNodes || []).map(visit).join('');
    // Speaker labels sometimes exist only in the author's c attribute.
    if (['npc','msg'].includes(node.tagName)) return '\n' + (attrs.find(a=>a.name==='c')?.value || '') + '\n' + text;
    return ['p','br','div','text','li'].includes(node.tagName) ? '\n' + text + '\n' : text;
  }
  return visit(parseFragment(raw));
}

const shortNames = new Set(['空','荧']);
// Evaluate each occurrence separately: a prop and its namesake may share a sentence.
const instrumentBefore = /(?:钢|竖|提|古|瑶|胡|管风|手风|风|木|口|七弦|六弦|弹奏|弹|抚|拨|调|练|学|拉|架|(?:这|那|一|两|几)[把张])(?:了|着|过)?(?:一曲|一会儿)?$/u;
const instrumentAfter = /^(?:声|音|谱|弦|键|曲|房|行|盒|架|师|艺|技|乐器|瑟|棋书画|放(?:回|进)(?:了)?琴盒|作为乐器|是(?:一[把种件])?乐器|的(?:琴弦|弦|音色|音准)|上(?:弹|奏))/u;
const sugarBefore = /(?:白|红|粗|细|绵|黄|食用|原料|配料|食材|适量|少许|少量|若干|半|[一二两三四五六七八九十百\d.]+)\s*(?:茶匙|汤匙|小勺|大勺|小包|小袋|毫克|公斤|千克|克|斤|勺|匙|包|袋|块|粒|杯|份)?(?:的)?\s*$|(?:加入|放入|倒入|撒入|拌入|掺入|加|撒|放|倒|称取|称量|采购|购买|买)(?:了)?(?:一点|一些|点|些)?\s*$/u;
const sugarAfter = /^(?:粉|粒|罐|颗粒|糖浆|糖水|结晶|是(?:一种)?(?:食材|调味品)|做(?:蛋糕|饼干|甜点)|的甜度|的颗粒|融化|溶化|溶解|化开|熬成|用量|调味|增甜|提甜|炒糖色|[和与、及](?:面粉|食盐|淀粉|黄油)|(?:倒|撒|放|拌|加)(?:入|进|到)|[ \t]*[：:]?\s*[\d一二两三四五六七八九十半.]+\s*(?:克|斤|勺|匙|杯))/u;

// Exclusions apply to individual occurrences. A later mention of the character still counts.
const characterNames = new Map([
  ['魈', /(?<!山)魈/u],
  ['北斗', /北斗(?!七星)/u],
  ['七七', /七七(?!八八)/u],
  ['胡桃', /胡桃(?!木)/u],
  ['白术', /(?<!(?:药材|中药|药用|生药|炒制的|麸炒)[：:、\s]{0,3})白术(?!药材|入药|粉末|饮片)/u],
  ['甘雨', /(?<!久旱逢)甘雨/u],
  ['闲云', /闲云(?!野鹤)/u],
  ['行秋', /(?<![旅举进步骑出执推])行秋|行秋(?!游|季|日|收|祭|考)/u],
  ['冰神', /冰神(?!像)/u],
  ['爱诺', /爱诺(?!贝尔)/u],
  ['火神', /火神(?!像)/u],
  ['伊法', /伊法(?!尔)/u],
  ['水神', /水神(?!像)/u],
  ['草神', /草神(?!像)/u],
  ['散兵', /散兵(?!游勇)/u],
  ['流浪者', /(?<!(?:[个名位群批些]|许多|不少|若干|一队|身为|沦为|普通的|无名的|无家可归的|四处漂泊的|(?:路上|路边|街上|城里|巷口|桥下)(?:的|有|住着|聚集着))(?:[\p{Script=Han}]{1,8}的)?\s{0,2})流浪者(?!们|群体|收容所|聚集地)/u],
  ['岩神', /岩神(?!像)/u],
  ['雷神', /雷神(?!像)/u],
  ['万叶', /万叶(?!集)/u],
  ['托马', /托马(?!斯|士)/u],
  ['一斗', /一斗(?!米|粮|麦|谷|酒|豆|水)/u],
  ['影', /(?<![\p{L}\p{N}])影(?![\p{L}\p{N}])|(?<=去找|拜访|询问|通知|看望|遇见|遇到|呼唤|旅行者|[找向跟请问喊与叫陪和见])影(?![响院视像片子集迷评印楼射业展剧坛碟域法])|(?<![树人身背倒黑阴幻剪投摄光残暗倩魅踪孤随无留电])影(?=说|答道|问道|点头|微笑|走来|今天|正在|同意|摇头|前来|转身|商量)/u],
]);
const optionalDot = new Set(['璃月·仙人','璃月·商人与匠人','璃月·方士与术士','稻妻·潜伏者','稻妻·其他','须弥·沙漠','须弥·其他','枫丹·剧院与明星','枫丹·其他']);
const liyueContext = /璃月|群玉阁|玉京台|月海亭|总务司|千岩军|往生堂|望舒客栈|万民堂|南十字船队|绝云间|凝光|刻晴|天权星|玉衡星/u;
const otherRegion = /蒙德|稻妻|须弥|枫丹|纳塔|至冬|挪德卡莱/u;
const civicSeven = /(?<!北斗|北斗的|天上的|天上|夜空中的|夜观|仰望|遥望星空中的)七星(?!瓢虫|连珠|阵|剑|灯|伴月)/u;

const inazumaContext = /稻妻|鸣神岛|鸣神大社|海祇岛|离岛码头|天守阁|神无冢|八酝岛|眼狩令|锁国令|雷电将军|雷电影|神里家|三奉行|社奉行|天领奉行|勘定奉行|九条裟罗|珊瑚宫/u;
const outsideInazuma = /蒙德|璃月|须弥|枫丹|纳塔|至冬|挪德卡莱|江户|德川|镰仓|室町/u;
const inazumaShogunate = /(?<!(?:江户|德川|室町|镰仓|足利)(?:时代|时期)?(?:的)?[ \t]{0,3})幕府/u;

// A combat cue must point at the named target, not an unrelated boss in the scene.
const raidenTitle = '雷电将军';
const bossCue = '(?:周本|(?<![a-z])boss(?![a-z]))';
const bossSeparator = '[\\s：:·「“【（(\\-]{0,4}';
function encounterPattern(title, reciprocal=false) {
  return new RegExp([
    '(?:挑战|迎战)' + '(?:一下子|一下|过|了|的是|目标是|目标为)?' + bossSeparator + title,
    bossCue + '(?:挑战|副本|目标|里|中|战)?(?:是|为|的|打|刷)?' + bossSeparator + title,
    title + '[」”】）)]?' + '(?:的|这个|作为|是|是个|是一个|是本周的|作为本周的|这一|这一场|的每周)?' + bossSeparator + bossCue,
    title + '的(?:周常|每周)?挑战',
    ...(reciprocal ? [title + '(?:向我|向我们|向旅行者)(?:发起|提出)挑战'] : []),
  ].join('|'),'iu');
}
const raidenEncounter = encounterPattern(raidenTitle);
const shoukiEncounter = encounterPattern('正机之神');

const fontaineContext = /枫丹|梅洛彼得堡|欧庇克莱|刺玫会|沫芒宫|蒸汽鸟报|那维莱特|最高审判官|莱欧斯利|芙宁娜|娜维娅|克洛琳德/u;
const outsideFontaine = /蒙德|璃月|稻妻|须弥|纳塔|至冬|挪德卡莱/u;
const outsideTitle = /(?:蒙德|璃月|稻妻|须弥|纳塔|至冬|挪德卡莱|外国|英国|法国)(?:的|当地的)?$/u;
const genericDragonBefore = /(?:传说|神话|民间故事|童话|寓言)(?:(?:中|里|上)?的?)$|(?:古籍|典籍|文献)(?:记载|描述)(?:中)?的$|(?:泛称|泛指|统称|所谓|普通|历代|各种|所有|不同|任意|一[种类群头条只]|[几两数][头条只种])(?:的|为|作)?[「“]?$/u;
const genericDragonAfter = /^(?:们|一族|族群|这一物种|这个种族|的种类|的传说|这种|这类|(?:是|作为)(?:一[种类]|(?:个|一个)?(?:泛称|统称)))/u;

function occurrence(scene,key,match) {
  for (let pos=scene.indexOf(key);pos!==-1;pos=scene.indexOf(key,pos+key.length)) {
    if (match(scene.slice(0,pos),scene.slice(pos+key.length))) return true;
  }
  return false;
}
function genericWaterDragon(left,right) {
  return genericDragonBefore.test(left) || genericDragonAfter.test(right);
}
const hearthLeadership = /(?:阿蕾奇诺|壁炉之家(?:的)?主人)(?:(?:(?:所)?(?:领导|掌管|管理|统领|创办|负责))?的(?:这个|那个)?(?:组织|机构|势力)|(?:介绍|谈起|讲述)(?:自家|本部)?(?:组织架构|组织成员|组织历史))/u;
const servantTitle = /(?:愚人众(?:的)?(?:执行官(?:第四席)?)?|执行官(?:第四席)?|第四席)(?:的)?[·「“\s]*仆人|[「“]仆人[」”]/u;
const tribesHeading = '纳塔六大部族';
const tribeNames = spans.filter(span=>byStart.get(span.parent)?.title===tribesHeading).map(span=>span.keys[0]);
const placeBeforeName = /(?:地名|地理|城市|城镇|村庄|国家|这座城|这个地方|该地|这片地区|西安|北京|南京|长安|此城|此地|这里|当地|这片土地|这条河|这座山|[京沪]|[\p{Script=Han}]{1,6}[市县村镇港岛山河城])(?:在古代)?(?:原来|过去|曾用)?的?[「“]?$/u;
const placeAfterName = /^(?:[」”]?(?:即|是|指|为|意为)(?:该地|此地|这座城|这个地方|地名|旧地名)|[（(](?:地名|旧称)|(?:叫|为|曰)[「“]?[\p{Script=Han}]{1,6}[城县镇村])/u;
const foreignWorld = /异世界|异界|另一个世界|其他世界|别的世界|平行世界|异次元|火影忍者|忍者世界/u;
function localFireNation(left,right) {
  const before=left.split(/[。！？；，、\n]/u).pop();
  const after=right.split(/[。！？；，、\n]/u)[0];
  const earlier=left.split(/[。！？；\n]/u).pop();
  const foreignSetting=/(?:身处|来到|到了|穿越到|置身|身在|在)(?:一个|那个)?(?:异世界|异界|另一个世界|平行世界)/u.test(earlier)
    && !/(?:回到|返回|再到|再去)|提瓦特|纳塔/u.test(earlier.slice(earlier.search(foreignWorld)));
  return !foreignSetting && !foreignWorld.test(before.split(/回到|再到|再去|返回/u).pop()) &&
    !/^[」”]?(?:位于|属于|来自|是|出自|[（(])(?:那部作品的)?(?:异世界|异界|另一个世界|其他世界|别的世界|平行世界|火影忍者|忍者世界)/u.test(after);
}
function comparesFestivals(scene) {
  return scene.split(/[。！？；\n]/u).some(part=>
    /(?:各国|别国|他国|多国|两国|其他国家|不同国家|其他国度)(?:的)?(?:节日|节庆|庆典|节俗|节祭)/u.test(part) ||
    /跨国(?:的)?(?:节日|节庆|庆典)?(?:对比|比较)|(?:对比|比较|相比|异同|区别|不同).{0,14}(?:其他国家|别国|他国)|(?:其他国家|别国|他国).{0,14}(?:比较|对比|相比|异同|区别|不同)/u.test(part) ||
    /(?:蒙德|璃月|稻妻|须弥|枫丹|至冬|挪德卡莱)(?:当地|传统|的|其他|其他的|当地的|传统的){0,2}(?:节日|节庆|庆典|节俗|祭典)|(?:圣夜巡礼|节日|节庆).{0,12}(?:和|与|跟|比照)(?:蒙德|璃月|稻妻|须弥|枫丹|至冬|挪德卡莱)/u.test(part) &&
    /对比|比较|相比|异同|有何不同|有什么不同|有什么区别|有何区别/u.test(part)
  );
}
const festivalKeys=spans.find(span=>span.title==='各国节日').keys;
const lanternThemes=spans.find(span=>span.title==='历届海灯节').keys.filter(key=>!key.includes('海灯节'));
const inazumaFestivals=/三祭|光华祭|若雷祭|夏日祭/u;
const lanternHistory=/历届|历年|往届|往年|每届|各届|首届|(?:两|几|多|前[几两]|上[几两])届|哪一?届|[某那这本上今]一?届|第[一二三四五六七八九十\d]+届|(?:19|20)\d{2}年?|主题|纪念(?:对象|的(?:是|谁)|了?谁|哪位|哪一位)|(?:为了|为的是)纪念|纪念的是哪位/u;
const lanternGeneral=/跨国|其他国家|别国|各国|日常|习俗|传统习惯|怎么过|如何庆祝|节日总览|节日总表/u;
const overviewWord='(?:国家概况|国情|国家概览|国家概述|国家概貌|概况|国家背景|整体情况|基本国情)';
function asksCountry(scene,region) {
  if (new RegExp(region+'(?:这个国家|一国|当地|本国)?(?:的)?'+overviewWord,'u').test(scene)) return true;
  const remainder=scene.replace(new RegExp('(?:蒙德|璃月|稻妻|须弥|枫丹|纳塔|至冬|挪德卡莱)(?:的)?'+overviewWord,'gu'),'');
  return /国家概况|国家概览|国家概述|国家概貌|国家背景|国情|(?:这个|该|本)国(?:的)?(?:概况|整体情况)/u.test(remainder);
}
function festivalSelection(scene,previous='') {
  const lanternName=text=>text.includes('海灯节') || lanternThemes.some(key=>text.includes(key));
  const lantern=lanternName(scene) || lanternName(previous) && (lanternHistory.test(scene) || lanternGeneral.test(scene) || comparesFestivals(scene)) && !festivalKeys.some(key=>scene.includes(key));
  const parts=scene.split(/[，,。！？；;\n]/u);
  const regional=(pattern,context,outside,excluded)=>parts.some(part=>
    pattern.test(part) && !excluded.test(part) && (context.test(part) ||
      !outside.test(part) && (context.test(scene) || context.test(previous) && !outside.test(previous) && !outside.test(scene))));
  const inazuma=regional(inazumaFestivals,inazumaContext,outsideInazuma,/一年三祭|三祭祖先|日本|京都|大阪|东京/u);
  const opera=regional(/歌剧节/u,fontaineContext,outsideFontaine,/萨尔茨堡|拜罗伊特|格林德伯恩|维也纳|意大利|德国|奥地利|英国|现实世界/u);
  const other=festivalKeys.some(key=>key!=='海灯节' && key!=='歌剧节' && !inazumaFestivals.test(key) && scene.includes(key));
  const comparison=comparesFestivals(scene) || /跨国|两国|别国|与.{0,8}相比/u.test(scene) ||
    other && /比较|对比|相比|区别|不同|异同/u.test(scene);
  const requests=scene.split(/[,，。！？；;\n]|并(?:且)?|以及|(?:同时|另外|再|也)(?=说|问|查|介绍|想知道|告诉)/u);
  const history=lantern && (lanternThemes.some(key=>scene.includes(key)) || requests.some(part=>{
    if (!lanternHistory.test(part)) return false;
    if (comparison && /比较|对比|相比|区别|异同|不同/u.test(part) &&
      (comparesFestivals(part) || /跨国|两国|别国|其他国家/u.test(part) || festivalKeys.some(key=>key!=='海灯节' && part.includes(key)))) return false;
    if (/历届|历年|往届|往年|两届|哪一?届|主题|纪念/u.test(part)) return true;
    return !/参加|庆祝|去看|游玩|去逛|一起过/u.test(part) && !comparison;
  }));
  const general=lantern && (!history || lanternGeneral.test(scene) || comparesFestivals(scene) ||
    parts.some(part=>part.includes('海灯节') && /参加|去看|庆祝|逛|体验/u.test(part) && !lanternHistory.test(part)));
  return {table:general || other || inazuma || opera,lantern,history,inazuma,natlan:scene.includes('荣花演武')};
}
// Remove only the organization words used as part of this person's title.
const childeTitle = /(?:(?:愚人众(?:的)?[\s·]*)?(?:(?:十一|11)?执行官(?:的)?(?:末席|第(?:十一|11)席)?|(?:末席|第(?:十一|11)席)执行官)|愚人众(?:的)?(?:末席|第(?:十一|11)席))[\s·「“【]*(?:公子|达达利亚)[」”】]?/gu;
const childeFight = encounterPattern('(?:公子|达达利亚)');
function childeText(scene) {
  return scene.replace(/贵公子/gu,'贵族').replace(childeTitle,'达达利亚')
    .replace(/达达利亚[（(]愚人众(?:十一)?执行官(?:末席|第(?:十一|11)席)?[）)]/gu,'达达利亚')
    .replace(/公子[·・]达达利亚/gu,'达达利亚');
}
const eyeBefore = /(?:黑发|银发|金发|白发|红发|[他她]有|生着|长着|生有|长有|生得|眯着|睁着|那双|一双|一对|双|那对|这对|(?:[他她它]|少年|少女|青年|男人|女人|男子|姑娘|女孩|男孩)的|[他她]那|[他她]有着|浅色的|冷淡的|漂亮的|清澈的|微闭|垂下|眯起|睁开|抬起|转动|对上|望进|凝视着)(?:[\p{Script=Han}]{1,7}的)?$/u;
const eyeAfter = /^(?:里|中|微[眯眨合]|一[眯闪亮转]|闪|映|望|凝视|注视|眨|扫|看向|泛起|透出|流露|(?:的)?(?:少年|少女|青年|男人|女人|姑娘|男子|女孩|男孩)|是(?:一种)?瞳色)/u;
const screenBefore = /(?:电影|影片|电视剧|剧集|影视剧|影视作品|影评|放映|重映|片名|看片|观影)(?:版|里的|中的|名叫|叫作|名为|作品)?[《「“\s：:]*$/u;
const screenAfter = /^[》」”]?(?:这部|那部|的|是(?:一部)?)?(?:电影|影片|电视剧|影评|导演|演员|票房|剧照|上映|剧集)/u;
const nodEnglish = /(?<![a-z0-9_])nod(?:[ \t]*[-‑][ \t]*|[ \t]+)krai(?![a-z0-9_])/iu;
const nodPlaces = /挪德卡莱|希汐岛|伦波岛|帕哈岛|那夏镇|银月之庭|霜月神殿|皮拉米达城|终夜长茔|霜月之坊/u;
const outsideNod = /蒙德|璃月|稻妻|须弥|枫丹|纳塔|至冬(?!影域)/u;
function nodScene(text) {
  // A stated destination supersedes the point of departure.
  const destinations=text.split(/(?:返回|回到|来到|抵达|前往|赶赴|动身去|转往)/u);
  const destination=destinations.at(-1);
  if (destinations.length>1 && (outsideNod.test(destination) || nodPlaces.test(destination) || nodEnglish.test(destination))) {
    return nodPlaces.test(destination) || nodEnglish.test(destination);
  }
  return nodPlaces.test(text) || nodEnglish.test(text);
}
const foodAfter = /^(?:风味的?|口味的?|味的?|的)?(?:蛋糕|火腿|甜点|冰淇淋|巧克力|慕斯|冰激凌|面包|奶茶|饼干|甜品|配方)/u;
const germanBefore = /(?:德国|德意志|巴登[-－·]?符腾堡)(?:西南部?|南部|境内|当地|著名|旅游|旅行|游览|探访|参观|前往|来到|的|那片|地区|时|期间|了|去|在|参观了|[，、\s]){0,9}$/u;
const germanAfter = /^(?:地区)?(?:[（(，、]?\s*(?:位于|坐落于|属于|在|是)?(?:德国|德意志|巴登[-－·]?符腾堡))/u;
const witcherBefore = /(?:巫师|猎魔人)\s*(?:3|３|三)(?:[》」”\s:：·的中里]|游戏|出现|遇到|提到|看到|讲到|世界|里面|那支|中的|里的|那群|时|曾|了|时的|游戏中|碰见|扮演|对抗|与|和|关于){0,20}$/u;
const witcherAfter = /^[》」”]?(?:是|属于|出自|来自|指|[（(：:])(?:游戏)?[《「“]?(?:巫师|猎魔人)\s*(?:3|３|三)/u;
const dossierLookup = /^(?:请)?(?:只)?(?:点查|查阅|调用|查询|查看|调取|调入|加载)?\s*[：:]?\s*[「“【《]?(旅行者(?:基础档案|元素形态|核心设定)|历届海灯节|深渊教团分类|神之心机制|(?:愚人众执行官)?全席补充)[」”】》]?[。！]?\s*$/u;
const ordinaryTraveller = /(?:[个位名群]|几[个位名]|普通|过路|路过|途经|外地来|往来|来往|许多|不少|游客|无名|流浪的|路上有|城里有|几名|无数|成群|过往)(?:[\p{Script=Han}]{0,8}的)?$/u;
function namedTraveller(left,right) {
  return !ordinaryTraveller.test(left) && !/^(?:影院|电影|们|人群|基础档案|元素形态|核心设定)/u.test(right);
}
const personBefore = /(?:去找|寻找|拜访|探望|询问|问问|叫上|请来|喊来|通知|请|找|与|向|跟|陪|叫住)$/u;
const shortNameAction = /^(?:商量|聊天|同行|说话|谈谈|见面|帮忙|打招呼|出发|讨论|谈起|询问|作为|身为|的降临者身份|是第|[说问答](?:道|了)|点头|摇头|走来|笑了|[，。！？、：:\s]|$)/u;
function namedTwin(scene,key) {
  return occurrence(scene,key,(left,right)=>{
    if (key==='空' && /^(?:气|位|地|间|缺|手|闲|旷|白|洞|港|调|虚|房|座|箱|腹|中|军|运|格|姐)/u.test(right) ||
        key==='荧' && /^(?:光|屏|幕|惑)/u.test(right)) return false;
    if (/旅行者[（(「“]?$/.test(left) && !ordinaryTraveller.test(left.replace(/旅行者[（(「“]?$/u,''))) return true;
    const boundaryBefore=!/[\p{L}\p{N}]$/u.test(left);
    const boundaryAfter=!/^[\p{L}\p{N}]/u.test(right);
    return boundaryBefore && boundaryAfter || (boundaryBefore || personBefore.test(left)) && shortNameAction.test(right);
  });
}
const otherWork = /(?:其他|别的|另一(?:个|部|款)?|其它|其他的)(?:作品|游戏|小说|动画|故事|神话)|异世界|北欧神话|魔兽(?:世界)?|《[^》\n]{1,32}》/u;
function teyvatTree(left,right) {
  const before=left.split(/[，。！？；\n]|提瓦特|原神[》]?|须弥/u).pop();
  const after=right.split(/[，。！？；\n]/u)[0];
  return !otherWork.test(before) && !(/^(?:[（(]|属于|来自|出自|指的是|指|是)/u.test(after) && otherWork.test(after));
}
const paimonOrigins = /派蒙(?:(?:谈起|谈到|说起|聊起|提起|讲述|讨论)(?:了)?(?:自己|自身)|自己|自身|她)?(?:有何|有什么|的|真正的|真实的)?(?:身世|来历|出身|真实身份)|派蒙(?:究竟|到底)?(?:是谁|来自哪里|从哪里来)|派蒙(?:是否就是|是不是|是否是|就是|是)天理|(?:派蒙(?:与|和|跟|同)天理(?:维系者)?|天理(?:维系者)?(?:与|和|跟|同)派蒙)(?:之间|有|是|的|究竟|到底|什么|怎样|何种|何|否|没有|真正|特殊|直接|存在|着){0,8}(?:关系|关联|联系|有关|相关)/u;
const descentIdentity = /(?:旅行者|空|荧)(?:自己|本人)?(?:的)?(?:是否|究竟|到底)?(?:是(?:不是)?|身为|作为|属于|被称为|被视为|并非|也是|才是|正是)?(?:一[名个位]|第[一二三四五六七八九十\d]+(?:位|个|名)?)?降临者(?:身份)?|(?:旅行者|空|荧)(?:讨论|谈起|提及)(?:自己|他|她)的降临者身份|(?:作为|身为|被称为)(?:第[一二三四五六七八九十\d]+(?:位|个|名)?)?降临者的?(?:旅行者|空|荧)/u;
const statueAction = /(?:七天神像.{0,12}(?:共鸣|切换(?:到|为|成)?(?:[风岩雷草水火冰]元素?|元素))|(?:共鸣|切换(?:到|为|成)?(?:[风岩雷草水火冰]元素?|元素)).{0,10}七天神像)/u;
const statueFollowup = /^(?:然后|再|现在|我想|我要|与它|与神像|和它|在这里|在这里再|在神像前|\s)*(?:开始)?(?:共鸣|与[风岩雷草水火冰]元素共鸣|切换(?:到|为|成)?(?:[风岩雷草水火冰]元素?|元素))/u;
// Match the use of a reaction at this occurrence, so a later combat use can still count.
const physicalSubjects = new Map([
  ['蒸发', /水分|水汽|水滴|汗水|水面|酒精|溶剂|财富|资金|市值|人间/u],
  ['融化', /冰块|冰雪|冰棒|冰激凌|冰淇淋|积雪|雪糕|巧克力|黄油|砂糖|心都/u],
  ['超载', /车辆|公交|货车|卡车|电梯|负荷|载重|服务器|电路|客车/u],
  ['冻结', /账户|帐号|账号|银行卡|资产|资金|余额|存款|湖面|水管|权限/u],
  ['感电', /漏电|电器|插座|电线|电工|触电|电路/u],
  ['绽放', /鲜花|烟花|花朵|花苞|笑容|花蕾|樱花|百花/u],
  ['激化', /矛盾|争端|分歧|冲突|局势|纠纷/u],
  ['碎冰', /一袋|一杯|一桶|一勺|一盘|几块|饮料|冰箱|鸡尾酒|刨冰/u],
  ['扩散', /花粉|气味|谣言|疫情|病毒|烟雾|消息/u],
  ['结晶', /盐水|食盐|糖浆|溶液|科研|智慧|爱情|汗水/u],
]);
const battleLanguage = /敌人|敌方|魔物|怪物|史莱姆|丘丘人|盗宝团|深渊法师|怪群|对手|对怪|打怪|战斗|实战|元素附着|挂[水火雷冰草]|[水火雷冰岩风草]元素|草原核|攻击|技能|伤害|配队|队伍|打出|触发|施放|补[水火雷冰草]|连招|反应倍率|打(?:一套|一轮)?$/u;
const namedReaction = /(?:普通|常规|基础|原|蔓|超|烈|点查|查阅|查询|查看|研究|比较|对比|触发|打出|造成|施加|打|用)[「“\s]*$/u;
const comparisonWords = /比较|对比|相比|区别|不同|异同|差别|差异|对照|比起|比一比|有何分别/u;
const reactionSubjects = /元素反应|月曜反应|星烁反应|蒸发|融化|超载|冻结|感电|绽放|激化|碎冰|扩散|结晶|[月星]超导/u;
function reactionAt(key,left,right) {
  if (/(?:月|星)$/u.test(left)) return false;
  const before=left.split(key).pop().split(/[，,。！？；;、\n]|然后|接着|之后|后(?=再?(?:触发|打出|施加|造成))|再用/u).pop();
  const after=right.split(key)[0].split(/[，,。！？；;、\n]|然后|接着|之后/u)[0];
  const subject=physicalSubjects.get(key);
  if (subject.test(before.slice(-14)) || subject.test(after.slice(0,10))) return false;
  if (/^(?:\s*(?:请)?(?:单点|点查|查阅|查询|查看|聊聊|说说|讲讲)?\s*[:：]?\s*[「“【《]?)$/u.test(before) &&
      /^[」”】》]?[。！？]?\s*$/u.test(after)) return true;
  if (/^(?:元素)?反应|^(?:伤害|队伍|配队|倍率|输出|流派)/u.test(after) ||
      /(?:打出|触发|造成|施加)(?:了)?[「“]?$/u.test(before)) return true;
  if (before.includes('◇') && /(?:跟|和|与|比|相对于)$/u.test(before) && comparisonWords.test(before+after)) return true;
  if (after.includes('◇') && /^(?:跟|和|与|比)/u.test(after) && comparisonWords.test(before+after)) return true;
  return namedReaction.test(before) || battleLanguage.test(before.slice(-18)) || battleLanguage.test(after.slice(0,18));
}
function reactionTable(scene) {
  // A qualifier such as “元素反应” immediately after a lunar/star name names that special reaction.
  const ordinary=scene.replace(/(?:元素反应\s*[：:]\s*)?(?:月(?:绽放|感电|结晶)|星(?:超导|扩散))(?:的)?(?:元素反应|反应)?/gu,'◇');
  if (/元素反应全表|元素扩散|元素结晶/u.test(ordinary)) return true;
  if (ordinary.split(/[，,。！？；;\n]/u).some(clause=>clause.includes('元素反应') && !clause.includes('◇'))) return true;
  for (const key of physicalSubjects.keys()) {
    if (occurrence(ordinary,key,(left,right)=>reactionAt(key,left,right))) return true;
  }
  return false;
}
function ordinaryComparison(scene) {
  return scene.split(/[。！？；\n]/u).some(clause=>
    /(?:普通|常规|基础)(?:元素)?(?:反应|绽放|感电)/u.test(clause) && comparisonWords.test(clause));
}
const abyssFights = new Map([
  ['andrius',encounterPattern('(?:(?:北风)?狼王(?:[·・]?安德留斯)?|安德留斯)',true)],
  ['dvalin',encounterPattern('(?:风魔龙(?:[·・]?特瓦林)?|特瓦林)',true)],
  ['apep',encounterPattern('(?:草龙(?:[·・]?阿佩普)?|阿佩普)(?:的绿洲守望者)?',true)],
  ['rene',encounterPattern('(?:雷内(?!尔)(?:[/／]水形幻人)?|水形幻人)',true)],
]);
const abyssOpponents = /狼王|安德留斯|特瓦林|风魔龙|阿佩普|草龙|雷内|水形幻人/u;
const teyvatSetting = /提瓦特|原神|蒙德|璃月|稻妻|须弥|枫丹|纳塔|至冬|挪德卡莱|坎瑞亚|戴因斯雷布|天理|七神|地脉|深渊教团|五大罪人/u;
const terrainBefore = /(?:悬崖|山崖|崖壁|峡谷|山谷|裂谷|脚下|面前的那道|一道|这道|那道|一条|万丈|万丈深|无底|深不见底|俯瞰|俯视|坠入|掉进|跌入|跌落|凝视|跳进|跳下)(?:[\p{Script=Han}]{0,6}的)?$/u;
const terrainAfter = /^(?:边|旁|峭壁|崖壁|谷底|底部|深不见底|有[一二三四五六七八九十百千\d]+米深|是(?:一[处道条个种片])?(?:峡谷|深沟|地形))/u;
function abyssName(scene,inherited) {
  return (teyvatSetting.test(scene) || inherited) && occurrence(scene,'深渊',(left,right)=>
    !/^(?:教团|使徒|咏者|法师|信徒|猎犬|螺旋)/u.test(right) &&
    !terrainBefore.test(left) && !terrainAfter.test(right));
}
const executiveNames = new Map([
  ['丑角','皮耶罗'],['博士','多托雷'],['少女','哥伦比娅'],['仆人','阿蕾奇诺'],
  ['公鸡','普契涅拉'],['散兵','散兵'],['木偶','桑多涅'],['女士','罗莎琳'],
  ['富人','潘塔罗涅'],['队长','卡皮塔诺'],['公子','达达利亚'],['统括官','皮耶罗'],
]);
const namedExecutive = [...new Set([...executiveNames.keys(),...executiveNames.values(),'艾弗提尔维特','赞迪克','瑟雷恩'])].join('|');
const seat = '(?:第[一二三四五六七八九十0-9]+席|末席)';
const executivePrefix = '(?:(?:愚人众(?:的)?[·\\s]*)?(?:(?:十一|11)?执行官(?:的)?(?:'+seat+')?|'+seat+'(?:执行官)?)|愚人众(?:的)?(?:'+seat+')?)';
const fullExecutiveTitle = new RegExp(executivePrefix+'(?:的)?[·：:「“【\\s]*('+namedExecutive+')[」”】]?','gu');
const postExecutiveTitle = new RegExp('('+namedExecutive+')[（(]愚人众(?:(?:十一)?执行官(?:'+seat+')?|'+seat+')[）)]','gu');
const fatuiNames = /皮耶罗|艾弗提尔维特|多托雷|赞迪克|罗莎琳|炎之魔女|卡皮塔诺|天柱骑士|瑟雷恩|普契涅拉|潘塔罗涅|达达利亚|阿蕾奇诺|桑多涅|哥伦比娅|散兵(?!游勇)/u;
const fatuiSetting = text=>/愚人众|执行官/u.test(text) || fatuiNames.test(text);
const royalPower = /执行官(?:们|群体)?(?:使用|佩戴|持有|所用|拥有|获得|获赐|受赐|靠|所持|的|们的|所用的|使用的|持有的|获得的|所获的|手中(?:的)?)?(?:邪眼|力量(?:的)?来源|权能(?:的)?来源|力量(?:究竟|到底)?(?:来自|源于|从哪|由谁|是从哪|是如何获得)|(?:如何|怎样|怎么|从哪(?:里|儿)?)(?:获得|得到)力量)|(?:邪眼|力量|权能)(?:如何|怎样)?(?:授予|赋予|赐予)(?:愚人众)?执行官/u;
const signoraFight = encounterPattern('(?:女士(?:[·・]?罗莎琳)?|罗莎琳|炎之魔女)',true);
function executiveText(text) {
  const person=(_,name)=>executiveNames.get(name) || name;
  return text.replace(fullExecutiveTitle,person).replace(postExecutiveTitle,person)
    .replace(/愚人众(?:十一|11)(?:位)?执行官/gu,'执行官全席');
}
function signoraBattle(scene) {
  return signoraFight.test(scene.replace(/(?:[这那某一各几多]位|[这那某一]名|普通|年轻的|路过的)女士|女士(?:们|组|组别|邀请赛|挑战赛)/gu,'路人'));
}
const covenCodes = /魔女会(?:的)?(?:成员)?(?:代号(?:为|是)?)?[\s·＋+:：「“【]*([ARMNB](?:(?:\s*[、,，/／和与及]\s*)[ARMNB])*)(?![A-Za-z0-9_])[」”】]?/gu;
function readCovenCodes(scene) {
  const codes=new Set();
  const prose=scene.replace(covenCodes,(_,list)=>{
    for(const code of list.match(/[ARMNB]/gu)) codes.add(code);
    return ' ';
  });
  return {codes,prose};
}
const covenLookup = /^(?:请)?(?:只)?(?:点查|查阅|调用|查询|查看|调取|调入|加载)?\s*[:：]?\s*[「“【《]?(魔女会组织性质|魔女会成员|魔女会经典逸闻|魔女会逸闻)[」”】》]?[。！]?\s*$/u;
const covenPersonLookup = /^(?:(?:请)?(?:只)?(?:点查|查阅|调用|查询|查看|调取|调入|加载)\s*[:：]?\s*[「“【《]?(?:魔女会(?:篇|档案)?[·・:：／/\s]*)?|[「“【《]?魔女会(?:篇|档案)[·・:：／/\s]*)(尼可[·・]?莱恩|杜林|布伦妮)[」”】》]?[。！]?\s*$/u;
const covenHeadings = ['魔女会组织性质','魔女会成员','尼可·莱恩','杜林','布伦妮','魔女会经典逸闻'];
const aliceCode = /艾莉丝(?:自己|本人)?(?:在魔女会(?:中)?的?|的)?(?:成员)?(?:字母)?代号|艾莉丝(?:在魔女会(?:中|里)?)?(?:使用|用|对应|是|以)(?:的)?(?:什么|哪[个一]?|哪一个)(?:字母|代号)|(?:哪个字母|什么代号|哪个代号)(?:代表|属于|对应)艾莉丝/u;
const italianPlaceBefore = /(?:意大利|皮埃蒙特|都灵)(?:的|北部|西北部|城市|地名|译名|旅游|旅行|前往|游览|参观|抵达|来到|去|时|在|城市|游|期间|[，、（(\s]){0,10}$/u;
const italianPlaceAfter = /^(?:市|城)?(?:[（(，、]?\s*(?:位于|坐落于|属于|是|在)?(?:意大利|皮埃蒙特|都灵))|^(?:旅游|旅行|游览).{0,8}(?:意大利|皮埃蒙特|都灵)/u;
function goldName(scene,inherited) {
  return occurrence(scene,'黄金',(left,right)=>{
    const before=left.split(/[。！？；，、\n]/u).pop();
    const after=right.split(/[。！？；，、\n]/u)[0];
    if (/(?:买|卖|交易|支付|收藏|熔炼|提炼|购买|价格|储备|囤积|一克|一斤|一块|一袋|一箱)(?:了|的)?(?:[几数一二两三四五六七八九十百千\d.]+(?:克|公斤|千克|斤|吨|盎司)|大量|少量|一批)?[「“]?$/u.test(before) ||
        /^(?:价格|首饰|珠宝|饰品|交易|储备|期货|货币|金条|项链|戒指|重量|纯度|矿石|时代|周|比例|分割|支付|付款|结账|抵债|兑换|熔成|熔化|炼成|铸成|称重)/u.test(after)) return false;
    return inherited || /魔女会|炼金术士/u.test(before+after);
  });
}
const heavenlyEntries = spans.filter(span=>span.group==='天理四影与天空岛' && span.keys.length);
const asmodayName = /(?<![a-z0-9_])asmoday(?![a-z0-9_])/iu;
const shadowCollective = /四(?:位|大|名|个)执政/u;
const rosterRequest = /名单|列出|列举|名录|哪(?:几|四|些)位|都有谁|分别是谁|有哪些|分工|分管|分配权能|权能分配|各(?:自|管|司)|分别(?:掌管|负责|执掌)/u;
const idiomBeforeOrder = /(?:没(?:有)?|还(?:有没|有没有)|哪(?:里|还|儿)?有|毫无|有违|有悖(?:于)?|违背|丧尽|不合|世间自有|世上自有|公道自有|存)[「“]?$/u;
const idiomAfterOrder = /^(?:难容|不容|昭昭|何在|何存|不公|不彰|循环|报应|良心|人情|公道|难伸|不在|不存|[，、 ]*灭人欲)/u;
function heavenlyOrder(left,right) {
  if (idiomBeforeOrder.test(left) || idiomAfterOrder.test(right) ||
      /还有$/u.test(left) && /^(?:吗|么|嘛|啊|[？?])/u.test(right) || /上$/u.test(left) && /^应/u.test(right)) return false;
  if (/^(?:的)?四影/u.test(right)) {
    // Possessive names are collective references unless the sentence specifies a relationship.
    return /(?:受命于|效忠于|隶属于|追随|由)$/u.test(left) || /^的四影.{0,5}(?:隶属|服从|效忠|受命)/u.test(right);
  }
  return true;
}
const shadeDossiers = heavenlyEntries.filter(entry=>entry.keys.some(key=>/^[空时死生]之执政$/u.test(key)));
function asksAboutShades(scene) {
  if (!rosterRequest.test(scene)) return false;
  if (shadowCollective.test(scene)) return true;
  if (!/分工|分管|分配权能|权能分配|各(?:自|管|司)|分别(?:掌管|负责|执掌)/u.test(scene)) return false;
  const named=shadeDossiers.filter(entry=>entry.keys.some(key=>hit(scene,key)));
  return named.length>1 || named.length===1 && /(?:其他|其余|另外)(?:三位|三名|执政)|权能分工/u.test(scene);
}
const scottishPlace = /苏格兰|斯凯岛|斯凯群岛|内赫布里底|Isle of Skye/iu;
function localCelestia(left,right,inScotland=false) {
  const before=left.split(/[。！？；\n]/u).pop();
  const after=right.split(/[。！？；，、\n]/u)[0];
  const local=/(?:提瓦特|原神|天理|寒天之钉)/u;
  const lastLocal=Math.max(...Array.from(before.matchAll(/提瓦特|原神|天理|寒天之钉/gu),match=>match.index),-1);
  if (scottishPlace.test(before.slice(lastLocal+1))) return false;
  if (/^(?:[（(，、]?\s*(?:坐落于|位于|属于|是|在|即|又称)?(?:英国)?(?:苏格兰|斯凯岛|内赫布里底|Isle of Skye))/iu.test(after)) return false;
  return !inScotland || lastLocal>=0 || local.test(after);
}
const azhdahaFight = encounterPattern('若陀(?:龙王)?',true);
const fallenWatchFight = encounterPattern('(?:守望者|堕天)');
const watchPlace = /风息山/u;
const snowfield = /白桦雪葬地|白沙皇/u;
const domainTourism = /旅游|旅行攻略|景区|景点|游客|观光|度假|打卡地|人间秘境|小众秘境|旅游胜地|自然风光/u;
const domainGame = /提瓦特|原神|天赋本|武器本|圣遗物|副本|逆悬的冰河|刷本|树脂/u;
const foreignBossWorld = scene=>otherWork.test(scene.replaceAll('《原神》','原神')) || /魔兽世界|守望先锋|艾尔登法环/u.test(scene);
const ruinEnemy = /敌人|敌方|魔物|怪物|机械敌|精英怪|常规精英|打法|怎么打|讨伐|击败|遗迹守卫|遗迹猎者|遗迹重机/u;
const ruinSetting = /遗迹守卫|遗迹猎者|遗迹重机/u;
function localClause(left,right,key) {
  return left.split(/[，,。；;！？!?\n]/u).pop()+key+right.split(/[，,。；;！？!?\n]/u)[0];
}
const scouts = '愚人众先遣队';
const scoutAffiliation = /隶属|归属|归谁管|归.{0,6}管辖|属于|所属|听命|受命|效力|上级|上司|母组织|哪个(?:组织|势力|部门)|组织关系/u;
const scoutCombat = /打法|怎么(?:打|击败|对付|应对|破盾)|如何(?:打|击败|对付|应对|破盾)|攻略|战斗|击败|讨伐|弱点|破盾|克制|挑战|对战/u;
const otherScoutTargets=spans.filter(span=>span.group==='世界Boss与秘境').flatMap(span=>span.keys).filter(key=>key!==scouts);
function scoutIntent(scene,followup) {
  let active=followup, named=false, affiliation=false, combat=false;
  for (const clause of scene.split(/[，,。；;！？!?\n]/u)) {
    if (clause.includes(scouts)) active=true;
    else if (!active || !(scoutAffiliation.test(clause) || scoutCombat.test(clause)) ||
      otherScoutTargets.some(key=>clause.includes(key))) active=false;
    if (!active) continue;
    named=true;
    affiliation ||= scoutAffiliation.test(clause);
    combat ||= scoutCombat.test(clause);
  }
  return {organization:affiliation,elite:named && (combat || !affiliation)};
}
const mechanicNames=spans.filter(span=>byStart.get(span.parent)?.title==='角色世界书条目' ||
  byStart.get(span.parent)?.title==='其他零散条目地区补充' || span.group==='天理四影与天空岛' ||
  span.group==='愚人众执行官全席').flatMap(span=>span.keys.filter(key=>key.length>1));
function mechanicSetting(text) {
  return !foreignBossWorld(text) && (teyvatSetting.test(text) || /七天神像|派蒙|神之眼/u.test(text) ||
    mechanicNames.some(name=>text.includes(name) && hit(text,name)) || hit(text,'魈'));
}
const rankQuestion=/战力(?:分级|等级|层级|阶位|分档)|(?:战力|实力).{0,6}(?:哪一?档|哪一?[级层阶]|第几[档层级梯队])/u;
const fogAliases=/地图迷雾|黑色迷雾|灰色迷雾/u;
const exploration=/地图|探索|解锁|开图|探路|探查|进入|闯入|走进|深入|穿过|穿越|探险|迷路|未知区域|危险等级/u;
const wearObject=/鞋(?:底|跟|面|子)?|轮胎|齿轮|零件|部件|机件|机械|机器|轴承|刹车(?:片)?|制动盘|衣物|袖口|布料|织物|地板|表面|刀刃|武器|剑刃|刀具|盔甲|装备|材料|金属|工具|器械|器物|物件|屏幕|外壳|书脊|石阶|路面|石板|漆面|边角|绳索|桌椅|摩擦|耗材/u;
const religion=/佛教|佛经|佛学|佛门|佛法|佛家|佛陀|佛寺|佛祖|佛说|念佛|念经|寺庙|寺院|僧人|和尚|法师开示|因果报应|诵经|拜佛|消业|前世因果|宗教|道教|修佛/u;
const marketOpinion=/品牌|产品|商品|消费品|消费者|民调|民意(?:调查|测验)|舆论调查|选民|候选人|问卷调查|市场调研/u;
function ordinarySense(left,right,key,context) {
  // Isolate this mention even when an unrelated use of the same word shares a clause.
  const split=/[，,。；;！？!?\n]|然后|随后|却|反观|再问|想起|提起|谈及|谈到|说到|提醒|但|以及|而|并且|[和与跟]/u;
  return context.test(left.split(key).pop().split(split).pop()) || context.test(right.split(key)[0].split(split)[0]);
}
const costSubjects=/邪眼|死之诅咒/u;
const costNouns='(?:代价|副作用|后果)';
const costAfter=new RegExp('^(?:[」”】）)]|的|使用|发作|生效|缠身|以后|之后|后|分别|各自|各|究竟|到底|可能|会|所|带来|引发|产生|导致|造成|付出|付出的|要|有|什么|哪些|怎样|何种|多大|何|是|什么样的|怎样的|严重的|潜在|具体|不良|负面|需要|承担|承受|面临|是否|不会|没有|[：:\\s]){0,12}'+costNouns,'u');
const costBefore=new RegExp(costNouns+'(?:是指|来自|源自|是|呢|如何|怎样|是什么|方面)?[：:\\s「“【]*(?:使用|佩戴|遭受|中了|受到)?[「“【]*$','u');
const costFollowup=new RegExp('^(?:[，,：:\\s]|那么|那|它们|它|这个|这种|使用|佩戴|遭受|中了|之后|以后|后|的|再|可能|潜在|严重|承担|需要|承受|是否|不会|讲讲|问问|说说|谈谈|请问|会|有|带来|造成|产生|付出|什么|怎样|哪些|何种|何|要|其|具体)*'+costNouns,'u');
function asksCost(scene,previous='') {
  if (costSubjects.test(previous) && costFollowup.test(scene)) return true;
  return ['邪眼','死之诅咒'].some(key=>occurrence(scene,key,(left,right)=>
    costBefore.test(left) || costAfter.test(right) || /^[」”】]?(?:之后|以后|了|后)?[，,：:]/u.test(right) &&
    costFollowup.test(right.replace(/^[」”】]?(?:之后|以后|了|后)?/u,''))));
}
const ancestryEntries=spans.filter(span=>byStart.get(span.parent)?.title==='长生种与特殊血统');
const ancestryKeys=ancestryEntries.flatMap(span=>span.keys);
const bloodlines=new Map([
  ['仙人与魔神',['闲云','甘雨','烟绯','八重神子']],
  ['龙王',['那维莱特','风魔龙·特瓦林','阿佩普']],['妖精',['菲林斯']],['食梦貘',['梦见月瑞希']],
  ['水妖',['沃雅妮莎']],['鬼族',['荒泷一斗']],['猫又',['绮良良']],['天狗',['九条裟罗']],
  ['半鹿人',['菈乌玛']],['换生灵',['莉奈娅']],['美露莘',['希格雯']],['纯阳之体',['重云']],
]);
const lineagePeople=Array.from(bloodlines,([group,names])=>names.map(name=>({
  group,keys:spans.find(span=>span.keys[0]===name).keys
}))).flat();
const lineageQuestion=/血统|血脉|血缘|种族|族裔|族群|什么族|哪(?:一|个|种|个种)?族|哪一支族群|什么物种|何种族类|特殊体质|什么体质/u;
const lineagePronoun=/^(?:那|那么|所以|请问|说说|谈谈|想知道|再问|我想知道|关于|\s)*(?:他|她|它|他们|她们|这位|这名|血统|血脉|种族)/u;
const foreignLineage=/其他作品|其他游戏|别的作品|另一(?:款游戏|部作品)|游戏外|异世界|现实(?:世界)?(?:的|中的)?(?:民俗|民间|神话|传说)|(?:北欧|希腊|日本|中国|英国|欧洲|西方|东方)(?:的)?(?:古代|传统|古|民间)?(?:民俗|神话|传说|童话|故事|志怪)|现实中|民俗学|现实民俗|民俗传说|民间传说|民间故事|民间信仰|格林童话|安徒生童话|日本妖怪传说|西游记|封神演义|凡人修仙传|妖精的尾巴|魔兽世界|指环王|游戏阴阳师/u;
const foreignLore=text=>foreignLineage.test(text) || otherWork.test(text.replaceAll('《原神》','原神'));
function validLineageMention(left,right,key,previous) {
  const split=/[，,；;。！？!?\n]|(?:回到|再说|再谈|转到|至于|再问)/u;
  const before=left.split(split).pop(), after=right.split(split)[0];
  const clause=before+key+after;
  const local=teyvatSetting.test(clause);
  if (foreignLore(clause) || !local && (foreignLore(left.split(/[；;。！？!?\n]/u).pop()) || foreignLore(previous)) &&
    !/(?:回到|再说|再谈|转到|至于|再问).*(?:提瓦特|原神)/u.test(left)) return false;
  if (key==='天狗' && /^食[日月]/u.test(after)) return false;
  if (key==='猫又' && /^(?:来了|来过|跑|睡|吃|叫|跳|走|回来了|不见了|出现了)/u.test(after) &&
    /(?:一只|那只|这只|我家(?:的)?|邻居家的|小|野|家|黑|白|橘)猫$/u.test(before+'猫')) return false;
  if (key==='风仙' || key==='树妖') {
    const context=foreignLore(previous) ? clause : previous+' '+clause;
    return /至冬|菲林斯/u.test(context) || /提瓦特|原神/u.test(context) && /种族|族群|妖精|血统|一族|族类|物种/u.test(context);
  }
  return true;
}
function namedLineagePeople(text) {
  return lineagePeople.filter(person=>person.keys.some(key=>text.includes(key) && hit(text,key)));
}
function askedBloodlines(input,priorPeople) {
  const groups=new Set();
  let subject=priorPeople;
  for (const part of input.split(/[，,。！？!?；;\n]/u)) {
    const named=namedLineagePeople(part);
    if (named.length) subject=named;
    if (!lineageQuestion.test(part) || foreignLore(part)) continue;
    const question=part.split(/介绍|解释|询问|打听|问起|谈到|谈论|告诉我|了解/u).pop();
    if (/别人|其他人|旁人|对方/u.test(question)) continue;
    const targets=namedLineagePeople(question);
    if (targets.length) for (const person of targets) groups.add(person.group);
    else if (lineagePronoun.test(question) && (subject.length===1 || /他们|她们/u.test(question))) {
      for (const person of subject) groups.add(person.group);
    }
  }
  return groups;
}
function hit(scene, key, inheritedLiyue, inheritedInazuma, inheritedFontaine) {
  if (key==='磨损' || key==='业障' || key==='好感度' || key==='好感度阶段') {
    const context=key==='磨损' ? wearObject : key==='业障' ? religion : marketOpinion;
    return occurrence(scene,key,(left,right)=>!ordinarySense(left,right,key,context));
  }
  if (key==='鹤观') return occurrence(scene,key,(left,right)=>
    !(/(?:丹顶|白|灰|黑颈|蓑羽|候鸟|鸟类)$/u.test(left) && /^(?:测|察|赏)/u.test(right)));
  if (key==='天理') return occurrence(scene,key,heavenlyOrder);
  if (key==='纳贝') return occurrence(scene,key,(left,right)=>!(left.endsWith('收') && right.startsWith('壳')) && !right.startsWith('尔'));
  if (key==='Asmoday') return asmodayName.test(scene);
  if (key==='天空岛') return occurrence(scene,key,localCelestia);
  if (key==='尼可') return /尼可(?!拉斯)/u.test(scene);
  if (key==='杜林') return occurrence(scene,key,(left,right)=>!italianPlaceBefore.test(left) && !italianPlaceAfter.test(right));
  if (key==='黄金') return goldName(scene,false);
  if (key==='魔女会') return scene.replaceAll('魔女会逸闻','').includes(key);
  if (key==='丑角') return occurrence(scene,key,(left,right)=>
    !/(?:戏曲|戏剧|舞台|京剧|戏班|戏台|小丑|剧中|剧团|扮演|饰演|扮作|扮成)(?:里|中|上|行当|角色|演员|的|一个|一位|一名|里的|中的|扮演){0,5}$/u.test(left) &&
    !/^(?:行当|演员|扮相|脸谱|的(?:戏服|脸谱|唱腔))/u.test(right));
  if (key==='统括官') return false; // The dossier checks Fatui context before accepting this title.
  if (key==='罗莎琳') return scene.includes(key) || signoraBattle(scene);
  if (key==='深渊') return abyssName(scene,false);
  if (key==='雷内') return /雷内(?!尔)/u.test(scene);
  if (key==='丘林') return /丘林(?!斯)/u.test(scene);
  if (key==='守望者·堕天') return /守望者[·・]?堕天/u.test(scene);
  if (key==='守望者' || key==='堕天') return false; // Short titles need a local boss cue.
  if (key==='愚人众先遣队') return false; // Resolved against the question below.
  if (key==='Mimi tomo') return /(?<![a-z])mimi\s+tomo(?![a-z])/iu.test(scene);
  if (key==='旅行者') return occurrence(scene,key,namedTraveller);
  if (shortNames.has(key)) return namedTwin(scene,key);
  if (key==='世界树') return occurrence(scene,key,teyvatTree);
  if (key.startsWith('旅行者切换')) {
    const element=key.slice(5);
    return occurrence(scene,'旅行者',(left,right)=>namedTraveller(left,right) &&
      ['切换','切换到','切换为','切换成'].some(action=>right.startsWith(action+element)));
  }
  if (key==='Nod-Krai') return nodEnglish.test(scene);
  if (key==='三月女神') return /三月女神(?!节)/u.test(scene);
  if (key==='影域') return /(?<!摄)影域|影域(?!名)/u.test(scene);
  if (key==='黑森林') return occurrence(scene,key,(left,right)=>
    !foodAfter.test(right) && !germanBefore.test(left) && !germanAfter.test(right) &&
    !/(?:吃|点|切|尝)(?:了)?(?:一块|一份|一片|一口)$/u.test(left));
  if (key==='狂猎') return occurrence(scene,key,(left,right)=>!witcherBefore.test(left) && !witcherAfter.test(right));
  if (key==='西风骑士团') return /西风骑士团(?!(?:的)?远征军)/u.test(scene) ||
    /远征军/u.test(scene) && /母组织|骑士团(?:本部|总部)|(?:蒙德|骑士团)总部/u.test(scene);
  if (key==='达达利亚' || key==='执行官公子') {
    const named=childeText(scene);
    return named.includes('达达利亚') || childeFight.test(named);
  }
  if (key==='愚人众' || key==='愚人众执行官') {
    const outsideTitle=childeText(scene.replaceAll(scouts,''));
    if (outsideTitle.includes(key)) return true;
    return key==='愚人众执行官' && /执行官(?:们|群体|集体|全体|组织|成员|的席位|的组织|的成员)|(?:全体|各位|各席|其他|历任|所有)(?:的)?执行官/u.test(outsideTitle);
  }
  if (key==='至冬') return occurrence(scene,key,(left,right)=>!/^(?:季|天|至|末|初|月|影域)/u.test(right) && (!/(?:春|夏|秋)(?:天|季|末|初)?$/u.test(left) || /^[国宫堡]/u.test(right)));
  if (key==='灰眸') return occurrence(scene,key,(left,right)=>!eyeBefore.test(left) && !eyeAfter.test(right));
  if (key==='雪国列车') return occurrence(scene,key,(left,right)=>!screenBefore.test(left) && !screenAfter.test(right) &&
    !(left.endsWith('《') && right.startsWith('》')));
  if (key==='第三人称射击') return occurrence(scene,key,(left,right)=>
    !/(?:普通|一般|常见|一款|这款|那款|玩(?:了)?(?:一款)?|游戏(?:类型|分类)[：:])$/u.test(left) &&
    !/(?:射击游戏|网游|手游|普通游戏|单机游戏)[^，。！？；\n]{0,16}$/u.test(left) &&
    !/^(?:类(?:型的?)?|的)?(?:游戏|网游|手游|端游|单机|电竞)/u.test(right));
  if (key==='古名' || key==='古名传承') return occurrence(scene,key,(left,right)=>
    !(/千$/u.test(left) && /^[句言篇作诗画曲]/u.test(right)) && !placeBeforeName.test(left) && !placeAfterName.test(right));
  if (key==='燃素') return /燃素(?!说)/u.test(scene);
  if (key==='火之国') return occurrence(scene,key,localFireNation);

  if (key==='审判庭' || key==='公爵') {
    return (fontaineContext.test(scene) || inheritedFontaine) && occurrence(scene,key,left=>!outsideTitle.test(left));
  }
  if (key==='仆人') return scene.trim()==='仆人' || scene.split('\n').some(line=>line.trim()==='仆人') || servantTitle.test(scene);
  if (key==='壁炉之家') return /壁炉之家(?!(?:的)?主人)/u.test(scene) || hearthLeadership.test(scene);
  if (key==='壁炉之家主人') return /壁炉之家(?:的)?主人/u.test(scene);
  if (key==='水龙王') return occurrence(scene,key,(left,right)=>validLineageMention(left,right,key,'') && !genericWaterDragon(left,right));
  if (key==='龙王') return occurrence(scene,key,(left,right)=>!left.endsWith('水') || genericWaterDragon(left.slice(0,-1),right));
  if (key==='幕府') return (inazumaContext.test(scene) || inheritedInazuma) && inazumaShogunate.test(scene);
  if (key==='七星') return (liyueContext.test(scene) || inheritedLiyue) && civicSeven.test(scene);
  if (optionalDot.has(key)) return scene.includes(key) || scene.includes(key.replace('·',''));
  if (characterNames.has(key)) return characterNames.get(key).test(scene);
  if (key==='风神') return /风神(?!像)/u.test(scene);
  if (key==='琴' || key==='砂糖') {
    const [before,after] = key==='琴' ? [instrumentBefore,instrumentAfter] : [sugarBefore,sugarAfter];
    for (let pos=scene.indexOf(key); pos!==-1; pos=scene.indexOf(key,pos+key.length)) {
      const left=scene.slice(Math.max(0,pos-24),pos);
      const right=scene.slice(pos+key.length,pos+key.length+24);
      if (after.test(right)) continue;
      if (key==='琴' && /听$/u.test(left) && /^(?:$|[\s，。！？、；]|消遣)/u.test(right)) continue;
      if (!before.test(left)) return true;
    }
    return false;
  }
  return scene.includes(key);
}

export function assembleSystem(rawInput, history = []) {
  const last = history.findLast(message => message.role === 'assistant');
  const rawPrevious = last ? sceneText(last.content) : '';
  const previous = executiveText(rawPrevious);
  const input = executiveText(rawInput);
  const scenes = [input, previous];
  const carryLiyue = liyueContext.test(previous) && !otherRegion.test(previous) && !otherRegion.test(input);
  const carryInazuma = inazumaContext.test(previous) && !outsideInazuma.test(previous) && !outsideInazuma.test(input);
  // Explicitly revisiting the person or the construct takes precedence over the previous reply.
  const wandererScenes = /流浪者|散兵|正机之神/u.test(input) ? [input] : scenes;
  const carryFontaine = fontaineContext.test(previous) && !outsideFontaine.test(previous) && !outsideFontaine.test(input);
  const currentTopics = [
    [/战力|实力|^\s*[tT][0-5](?:级|档)?\s*[。！？]?\s*$/u,['战力阶梯']],
    [/迷雾/u,['迷雾规则']],
    [/好感度/u,['好感度阶段']],
    [/邪眼|死之诅咒|代价|副作用|后果|磨损|业障|元素力耗尽|魔鳞病/u,['代价机制']],
    [/阿蕾奇诺|壁炉之家|仆人/u, ['壁炉之家','阿蕾奇诺']],
    [/若陀/u, ['龙王','若陀龙王']],
    [/守望者|堕天/u, ['守望者·堕天']],
    [/丘林|多莫沃伊|原初之冰雕刻师/u, ['丘林']],
    [/白沙皇|原初人类/u, ['白沙皇/原初人类']],
    [/秘境|天赋本|武器本|圣遗物本/u, ['秘境']],
    [/遗迹系列|愚人众先遣队/u, ['常规精英']],
    [/水龙王|龙王|那维莱特|最高审判官/u, ['龙王','那维莱特']],
    [/水神|芙宁娜/u, ['芙宁娜']],
    [/公爵|莱欧斯利/u, ['莱欧斯利']],
    [/审判庭/u, ['枫丹审判庭']],
    [/火神|玛薇卡/u, ['玛薇卡']],
    [/古名/u, ['古名']],
    [/燃素/u, ['燃素']],
    [/火之国/u, ['纳塔']],
    [/伊法/u, ['伊法']],
    [/巡礼/u, ['归火圣夜巡礼','各国节日']],
    [/冰神|安娜丝塔夏|冰之女皇|瓦沙嘉/u, ['安娜丝塔夏']],
    [/灰眸/u, ['灰眸']],
    [/至冬|影域/u, ['至冬']],
    [/影域/u, ['至冬影域']],
    [/远征军|西风骑士团/u, ['西风骑士团','西风骑士团远征军']],
    [/银月之庭|霜月神殿/u, ['银月之庭']],
    [/深渊|黑蛇众/u, ['深渊本质','深渊教团','深渊教团分类']],
    [abyssOpponents, ['北风狼王·安德留斯','风魔龙·特瓦林','阿佩普','草龙·阿佩普','雷内','雷内/水形幻人','守望者·堕天']],
    [/三月女神/u, ['三月女神']],
    [/黑森林/u, ['黑森林']],
    [/狂猎/u, ['狂猎']],
    [/爱诺/u, ['爱诺']],
    [/雪国列车/u, ['雪国列车']],
    [/第三人称射击/u, ['第三人称射击']],
    [/公子|达达利亚/u, ['达达利亚','愚人众','愚人众执行官','公子·达达利亚']],
  ];
  const currentOnly = new Set(currentTopics.filter(([pattern])=>pattern.test(input)).flatMap(([,keys])=>keys));
  if ([tribesHeading,...tribeNames].some(name=>input.includes(name))) {
    for (const key of ['纳塔',...tribeNames]) currentOnly.add(key);
  }
  const pilgrimage = /圣夜巡礼/u;
  const natlanPilgrimage = /(?:纳塔.{0,10}巡礼|巡礼.{0,10}纳塔)/u;
  const mentionsPilgrimage = text=>pilgrimage.test(text) || natlanPilgrimage.test(text);
  const followsPilgrimage = mentionsPilgrimage(previous) && !/蒙德|璃月|稻妻|须弥|枫丹|至冬/u.test(input);
  const currentPilgrimage = mentionsPilgrimage(input) || (input.includes('巡礼') && (followsPilgrimage || previous.includes('纳塔') && !/蒙德|璃月|稻妻|须弥|枫丹|至冬/u.test(input)));
  const comparePilgrimage = (currentPilgrimage || mentionsPilgrimage(previous)) && comparesFestivals(input)
    || !input.includes('巡礼') && mentionsPilgrimage(previous) && comparesFestivals(previous);
  const carryNod = nodScene(previous) && !outsideNod.test(input);
  const cardLocation = text=>nodScene(text) || /提瓦特|执灯人/u.test(text);
  const inheritedForeign = new Set();
  if (!cardLocation(previous) && !cardLocation(input)) {
    if (/(?:在|游览|游历|抵达|到|去)德国|德国(?:旅行|旅游)/u.test(previous)) inheritedForeign.add('黑森林');
    if (/(?:巫师|猎魔人)\s*[3３三]/u.test(previous)) inheritedForeign.add('狂猎');
  }
  const lookupName = dossierLookup.exec(rawInput.trim())?.[1];
  const lookup = lookupName==='全席补充' ? '愚人众执行官全席补充' : lookupName;
  const executiveFollowup = fatuiSetting(rawPrevious) &&
    /(?:他|她|他们|她们|这些执行官|这位执行官)(?:的|所用的)?(?:邪眼|力量来源|权能来源|力量从哪)/u.test(input);
  const focusedExecutive = fatuiNames.test(input) || /丑角|统括官|女士|邪眼|(?:第[一二三四五六七八九十0-9]+席|末席)|执行官|愚人众/u.test(rawInput);
  if (focusedExecutive) {
    for (const key of ['愚人众','愚人众执行官','愚人众执行官全席补充','邪眼','皮耶罗','多托雷','罗莎琳','卡皮塔诺','女士·罗莎琳','达达利亚','公子·达达利亚','阿蕾奇诺','哥伦比娅','桑多涅']) currentOnly.add(key);
  }
  const travellerTopics = /旅行者|空|荧|派蒙|降临者|世界树|七国旅历|七天神像|星之楔/u;
  const coreFollowup = previous.includes('派蒙') && /^(?:那|那么|说说|谈谈|讲讲|想了解|查查|\s)*(?:她的(?:身世|来历|出身)|她(?:和|与|跟)天理)/u.test(input);
  const statueWithAction = scene=>scene.split(/[。；！？\n]/u).some(sentence=>
    sentence.split('，').some(clause=>statueAction.test(clause)) ||
    /七天神像[^，]{0,6}，(?:然后|再|我|现在|选择|开始|打算|试着|在这里)*(?:与[风岩雷草水火冰]元素共鸣|共鸣|切换(?:到|为|成)?(?:[风岩雷草水火冰]元素?|元素))/u.test(sentence));
  const statueContinuation = previous.includes('七天神像') && statueFollowup.test(input);
  if (travellerTopics.test(input)) {
    for (const name of ['旅行者基础档案','旅行者核心设定','旅行者元素形态','天理与降临者','地脉与地图机制','冰元素旅行者']) currentOnly.add(name);
  }
  if (otherWork.test(previous.replace(/《原神》/gu,'提瓦特')) && !/提瓦特|须弥|原神/u.test(previous+input)) inheritedForeign.add('世界树');
  if (reactionSubjects.test(input)) {
    for (const name of ['元素反应全表','月曜反应','星烁反应']) currentOnly.add(name);
  }
  const lunarComparison=ordinaryComparison(input) &&
    (/月绽放|月感电/u.test(input) || !reactionSubjects.test(input.replace(/(?:普通|常规|基础)(?:元素)?(?:反应|绽放|感电)/gu,'')) && /月绽放|月感电/u.test(previous));
  const heading=covenLookup.exec(rawInput.trim())?.[1] || covenPersonLookup.exec(rawInput.trim())?.[1];
  const covenTarget=heading==='魔女会逸闻' ? '魔女会经典逸闻' : heading?.replace(/尼可[・]?莱恩/u,'尼可·莱恩');
  const covenScenes=new Map(scenes.map(scene=>[scene,readCovenCodes(scene)]));
  const bareCode=/^[「“【]?[ARMNB][」”】]?[。！？]?$/.test(input.trim());
  const covenFocus=bareCode || /魔女会|艾莉丝|尼可|杜林|布伦妮|魔女猎人|魔女领域|黄金|莱茵多特|安德斯多特|巴比洛斯/u.test(input);
  if(covenFocus) for(const name of covenHeadings) currentOnly.add(name);
  const aliceFollowup=/^(?:那|那么|请问|我想知道|说说|查查|\s)*(?:她的|她在魔女会的)(?:成员)?(?:字母)?代号/u.test(input) && previous.includes('艾莉丝');
  const heavenlyFocus=heavenlyEntries.some(span=>span.keys.some(key=>input.includes(key))) ||
    asmodayName.test(input) || shadowCollective.test(input);
  if (heavenlyFocus) for (const entry of heavenlyEntries) currentOnly.add(entry.keys[0]);
  const shadowFollowup=rosterRequest.test(input) && !heavenlyFocus &&
    /四影|四(?:位|大|名|个)执政/u.test(previous) && /^(?:那|那么|请|说说|列出|列举|给我|看看|查查|想知道|\s)*(?:他们|她们|这四位|名单|权能分工|各自|分别)/u.test(input);
  const shadowRelationFollowup = /四影|四(?:位|大|名|个)执政/u.test(previous) &&
    /(?:他们|她们|它们|这四位)(?:与|和|跟)天理|天理(?:和|与|跟)(?:他们|她们|它们|这四位)/u.test(input);
  const scottishScene=scottishPlace.test(previous) && !/提瓦特|原神|寒天之钉|天空岛上的天理/u.test(previous);
  const scoutFollowup=previous.includes(scouts) && !input.includes(scouts) &&
    /^(?:那|那么|再|也|请问|想知道|说说|讲讲|以及|还有|和|跟|\s)*(?:他们|它们|这支队伍|该部队|怎么打|如何应对|打法|隶属|归属|属于|所属|弱点|怎么破盾)/u.test(input);
  if (scoutFollowup) for (const key of ['常规精英','愚人众']) currentOnly.add(key);
  const scoutIntents=new Map(scenes.map(scene=>[scene,scoutIntent(scene,scene===input && scoutFollowup)]));
  const previousFest=festivalSelection(previous);
  const currentFest=festivalSelection(input,previous);
  const festivalFocus=[...festivalKeys,...lanternThemes].some(key=>input.includes(key)) ||
    currentFest.history || currentFest.table || mentionsPilgrimage(input) || currentPilgrimage;
  if (festivalFocus) currentOnly.add('归火圣夜巡礼');
  const festival=festivalFocus ? currentFest : previousFest;
  const festivalRegion=(name,regional)=>{
    // Country names qualifying a festival comparison do not request a country dossier.
    if (festival.lantern && scenes.some(scene=>scene.includes(name)) && !asksCountry(input,name)) {
      const text=festivalFocus ? input : previous;
      const regionLookup=new RegExp('(?:前往|回到|拜访|探索|离开|抵达)'+name,'u');
      if (!asksCountry(text,name) && !regionLookup.test(text)) return false;
    }
    if (!regional) return undefined;
    return asksCountry(input,name) || !festivalFocus && asksCountry(previous,name);
  };
  const priorPeople=namedLineagePeople(previous);
  const currentPeople=namedLineagePeople(input);
  const lineageFocus=currentPeople.length || ancestryKeys.some(key=>input.includes(key)) || lineageQuestion.test(input);
  const requestedLineages=lineageFocus ? askedBloodlines(input,priorPeople) : askedBloodlines(previous,[]);
  if (currentPeople.length) for (const person of priorPeople) currentOnly.add(person.keys[0]);
  if (lineageFocus) for (const entry of ancestryEntries) currentOnly.add(entry.keys[0]);
  if (input.includes('鹤观')) currentOnly.add('鹤观');
  const selected = spans.filter(span => {
    if (covenTarget) return span.group==='魔女会' && span.keys[0]===covenTarget;
    if (lookup) return span.keys[0]===lookup;
    if (span.regionContext==='挪德卡莱') {
      if (input.includes('魔女会')) return input.replaceAll('魔女会逸闻','').includes('魔女会') && (nodScene(input) || carryNod);
      if (covenFocus) return false;
      return previous.replaceAll('魔女会逸闻','').includes('魔女会') && (carryNod || nodScene(input) && !outsideNod.test(previous));
    }
    if (span.topic==='wanderer') {
      return (focusedExecutive ? [input] : wandererScenes).some(scene => span.keys.some(key => hit(scene,key))
        && (span.encounter!=='shouki' || shoukiEncounter.test(scene)));
    }
    if (span.encounter==='raiden') {
      const encounterScene = input.includes(raidenTitle) ? input : previous;
      return encounterScene.includes(raidenTitle) && raidenEncounter.test(encounterScene);
    }
    const sources = currentOnly.has(span.keys[0]) ? [input] : scenes;
    if (bloodlines.has(span.keys[0])) {
      if (requestedLineages.has(span.keys[0])) return true;
      return sources.some(scene=>span.keys.some(key=>occurrence(scene,key,(left,right)=>
        validLineageMention(left,right,key,scene===input ? previous : '') &&
        (key!=='龙王' || !left.endsWith('水') || genericWaterDragon(left.slice(0,-1),right)))));
    }
    if (span.keys[0]==='那维莱特' && sources.some(scene=>scene.includes('水龙王'))) {
      return sources.some(scene=>span.keys.some(key=>key==='水龙王' ? occurrence(scene,key,(left,right)=>
        validLineageMention(left,right,key,scene===input ? previous : '') && !genericWaterDragon(left,right)) : hit(scene,key)));
    }
    if (span.keys[0]==='战力阶梯' || span.keys[0]==='迷雾规则') {
      const rank=span.keys[0]==='战力阶梯';
      return sources.some(scene=>{
        if (scene.includes(span.keys[0])) return true;
        const inherited=scene===input ? previous : '';
        return scene.split(/[。；;！？!?\n]/u).some(part=>{
          if (!(rank ? rankQuestion : fogAliases).test(part) || foreignBossWorld(part)) return false;
          if (!mechanicSetting(part) && !mechanicSetting(scene) && !mechanicSetting(inherited)) return false;
          return rank || !/天气|气象|烟雾|天气预报|起雾|浓烟/u.test(part) &&
            (exploration.test(scene) || exploration.test(inherited));
        });
      });
    }
    if (span.keys[0]==='代价机制' && sources.some(scene=>asksCost(scene,scene===input ? previous : ''))) return true;
    if (span.encounter==='azhdaha') return sources.some(scene=>azhdahaFight.test(scene) ||
      /(?:去打|开打|讨伐|击败|怎么打|如何打|怎么击败)若陀|若陀(?:龙王)?(?:的)?(?:打法|怎么打|挑战攻略)/u.test(scene));
    if (span.keys[0]==='守望者·堕天') return sources.some(scene=>{
      const target=scene.replace(/(?:阿佩普的)?绿洲守望者|堕天使|值夜的守望者/gu,'他者');
      return hit(target,'守望者·堕天') || !foreignBossWorld(target) && !(scene===input && foreignBossWorld(previous) && !teyvatSetting.test(input) && !watchPlace.test(input)) && target.split(/[，,。；;！？!?\n]/u).some(part=>
        /守望者|堕天/u.test(part) && (fallenWatchFight.test(part) || /(?:守望者|堕天)(?:的)?(?:打法|战斗机制|弱点|怎么打)/u.test(part) || watchPlace.test(part) ||
          scene===input && watchPlace.test(previous)));
    });
    if (span.keys[0]==='白沙皇/原初人类') return sources.some(scene=>scene.includes('白沙皇') ||
      occurrence(scene,'原初人类',(left,right)=>{
        const part=localClause(left,right,'原初人类');
        return !foreignBossWorld(part) && !/地球|考古|人类学|旧石器|智人/u.test(part) &&
          (teyvatSetting.test(scene) || snowfield.test(scene) || scene===input && !foreignBossWorld(previous) && (teyvatSetting.test(previous) || snowfield.test(previous)));
      }));
    if (span.keys[0]==='秘境') return sources.some(scene=>span.keys.slice(1).some(key=>scene.includes(key)) ||
      occurrence(scene,'秘境',(left,right)=>{
        const part=localClause(left,right,'秘境');
        return !domainTourism.test(part) && (domainGame.test(part) || !domainTourism.test(scene) &&
          !(scene===input && domainTourism.test(previous) && !domainGame.test(previous)));
      }));
    if (span.keys[0]==='常规精英') return sources.some(scene=>scoutIntents.get(scene).elite ||
      span.keys.some(key=>key!=='遗迹系列' && hit(scene,key)) ||
      scene.includes('遗迹系列') && (teyvatSetting.test(scene) || ruinSetting.test(scene) ||
        scene===input && (teyvatSetting.test(previous) || ruinSetting.test(previous))) &&
        (ruinEnemy.test(scene) || scene===input && ruinEnemy.test(previous)) && !/考古|旅游|历史遗址/u.test(scene));
    if (span.keys[0]==='愚人众' && sources.some(scene=>scoutIntents.get(scene).organization)) return true;
    if (span.keys[0]==='天空岛') return sources.some(scene=>scene.includes('寒天之钉') ||
      occurrence(scene,'天空岛',(left,right)=>localCelestia(left,right,scene===input && scottishScene)));
    if (span.keys[0]==='四影' && (shadowFollowup || shadowRelationFollowup || sources.some(asksAboutShades))) return true;
    if (span.group==='魔女会' || ['尼可·莱恩','杜林','布伦妮'].includes(span.keys[0])) {
      return sources.some(scene=>{
        const {codes,prose}=covenScenes.get(scene);
        const name=span.keys[0];
        if (name==='尼可·莱恩' && codes.has('N')) return true;
        if (name==='魔女会成员' && (['A','R','M','B'].some(code=>codes.has(code)) || aliceCode.test(prose) || aliceFollowup ||
            scene===input && goldName(prose,/魔女会|炼金术士/u.test(previous)))) return true;
        if (span.requires && !scene.includes(span.requires)) return false;
        if (name==='杜林' && scene===input && /意大利|皮埃蒙特|都灵/u.test(previous) &&
            /^(?:(?:去|在|抵达|参观|游览|前往))?杜林(?:旅游|旅行|观光|度假)?[。！？]?$/u.test(prose)) return false;
        return span.keys.some(key=>!/^魔女会[ARMNB]$/u.test(key) && hit(prose,key));
      });
    }
    if (span.encounter==='signora') return (/周本|boss|挑战|迎战/iu.test(input) ? [input] : sources).some(signoraBattle);
    if (span.keys[0]==='皮耶罗' && sources.some(scene=>scene.includes('统括官') &&
        (fatuiSetting(scene) || scene===input && fatuiSetting(rawPrevious)))) return true;
    if (span.keys[0]==='愚人众执行官全席补充' && (executiveFollowup || sources.some(scene=>royalPower.test(scene) ||
        fatuiNames.test(scene) && /(?:他的|她的|其|的)(?:邪眼|力量来源|权能来源|力量从哪)/u.test(scene)))) return true;
    if (span.keys[0]==='愚人众执行官' && sources.some(scene=>fatuiNames.test(scene) && /(?:席位|排名|第几席|位列哪一席)/u.test(scene))) return true;
    if (span.keys[0]==='愚人众' && sources.some(scene=>fatuiNames.test(scene) && /(?:所属|效力的|隶属的|背后的|他们的|所在的|关于|谈谈|讨论|谈起|介绍|打听|聊聊)组织/u.test(scene))) return true;
    if (span.keys[0]==='元素反应全表') {
      return lunarComparison || sources.some(scene=>reactionTable(scene) ||
        /月绽放|月感电/u.test(scene) && ordinaryComparison(scene));
    }
    if (span.keys[0]==='月曜反应' && lunarComparison) return true;
    if (span.keys[0]==='旅行者元素形态' && (statueContinuation || sources.some(statueWithAction))) return true;
    if (span.keys[0]==='旅行者核心设定' && (coreFollowup || sources.some(scene=>
      scene.split(/[。；！？\n]/u).some(clause=>paimonOrigins.test(clause) ||
        descentIdentity.test(clause) && (hit(clause,'旅行者') || ['空','荧'].some(key=>namedTwin(clause,key))))))) return true;
    if (span.keys[0]==='西风骑士团' && previous.includes('远征军') &&
        /(?:它们|他们|她们|它|其)(?:的)?母组织|^(?:再)?(?:讲讲|说说|谈谈|介绍(?:一下)?)?(?:母组织|骑士团本部)/u.test(input)) return true;
    if (span.keys[0]==='深渊本质' && abyssName(input,teyvatSetting.test(previous) && !otherWork.test(input))) return true;
    if (abyssFights.has(span.encounter)) {
      const targetScenes=/周本|boss|挑战|迎战/iu.test(input) ? [input] : sources;
      return targetScenes.some(scene=>abyssFights.get(span.encounter).test(scene));
    }
    if (span.encounter==='childe') {
      const targetScenes=/周本|boss|挑战|迎战/iu.test(input) ? [input] : sources;
      return targetScenes.some(scene=>childeFight.test(childeText(scene)));
    }
    if (span.keys[0]==='归火圣夜巡礼' && (currentPilgrimage || sources.some(mentionsPilgrimage))) return true;
    if (span.keys[0]==='各国节日') return festival.table || comparePilgrimage &&
      (!festivalFocus || currentPilgrimage || comparesFestivals(input));
    if (span.keys[0]==='历届海灯节') return festival.history;
    if (span.title===span.keys[0]+'概况') {
      const name=span.keys[0];
      const include=festivalRegion(name,name==='稻妻' && festival.inazuma || name==='纳塔' && festival.natlan);
      if (include!==undefined) return include;
    }
    if (byStart.get(span.parent)?.title===tribesHeading && sources.some(scene=>scene.includes(tribesHeading))) return true;
    return sources.some((scene,index) => (!span.requires || scene.includes(span.requires))
      && span.keys.some(key=>(index!==0 || !inheritedForeign.has(key)) &&
        hit(scene,key,index===0 && carryLiyue,index===0 && carryInazuma,index===0 && carryFontaine)));
  });
  const active = new Set(selected.map(s=>s.start));
  const parents = new Set();
  for (const entry of selected) {
    for (let id=entry.parent; id!=null; id=byStart.get(id).parent) parents.add(id);
  }
  const anchor = prefix.indexOf('# 玩家身份判定机制');
  if (anchor < 0) throw Error('GENSHIN_OVERVIEW_ANCHOR_MISSING');
  const parts = [prefix.slice(0,anchor) + triggerIndex + '\n\n' + prefix.slice(anchor)];
  for (const span of spans) {
    if (active.has(span.start) || parents.has(span.start) && !span.keys.length) parts.push(span.text);
    else if (parents.has(span.start)) parts.push(span.text.split('\n',1)[0]);
  }
  return {system:parts.join('\n'), selected:selected.map(s=>s.start)};
}
export function assembleTurn(input, history) {
  const {system} = assembleSystem(input, history);
  const current = input + '\n' + suffix;
  return {current, messages:[{role:'system',content:system}, ...history, {role:'user',content:current}]};
}
export const fixedSystem = () => assembleSystem('').system;
