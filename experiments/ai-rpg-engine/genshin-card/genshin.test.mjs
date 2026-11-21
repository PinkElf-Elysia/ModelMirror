import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {assembleSystem,assembleTurn,triggerIndex} from './assembly.mjs';
import {renderSetup} from './assets.mjs';
import {genshinPresentation} from './presentation.mjs';
import {installGenshin,genshinOptions} from './integration.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {start} from '../studio/server.mjs';

const bytes = file=>readFile(new URL(file,import.meta.url));
const read = async file=>(await bytes(file)).toString('utf8');
const json = async file=>JSON.parse(await read(file));
const book = await json('resources/worldbook.json');
const source = await json('resources/source.json');
const prefix = await read('resources/prefix.txt'),suffix = await read('resources/suffix.txt');
const words = book.filter(e=>e.keys.length);

test('lossless author sources and all conditional slots in their original order',()=>{
  const all = [prefix,...book.map(e=>e.text)].join('\n');
  assert.equal(sha(all),source.sources[0].textSha256);
  assert.equal(sha(suffix),source.sources[1].textSha256);
  const every = assembleSystem([...words.flatMap(e=>e.keys),'挑战雷电将军','挑战正机之神','挑战公子','挑战狼王','挑战特瓦林','挑战阿佩普','挑战水形幻人','挑战女士','挑战若陀','蒙德概况','璃月概况','稻妻概况','须弥概况','枫丹概况','纳塔概况','至冬概况','挪德卡莱概况'].join('；'));
  assert.equal(every.system.replace(triggerIndex+'\n\n',''),all);
  for (const e of words) for (const key of e.keys) {
    assert.ok(triggerIndex.includes(key),key);
    const context = e.encounter ? '周本' : key==='海灯节' && e.title==='历届海灯节' ? '历届' : ({'风仙':'至冬妖精','树妖':'提瓦特族群','战力分级':'提瓦特','战力等级':'提瓦特','地图迷雾':'提瓦特探索','黑色迷雾':'提瓦特探索','灰色迷雾':'提瓦特探索','三祭':'稻妻','光华祭':'稻妻','若雷祭':'稻妻','夏日祭':'稻妻','歌剧节':'枫丹','守望者':'Boss','堕天':'Boss','原初人类':'提瓦特','遗迹系列':'提瓦特敌人','黄金':'炼金术士','统括官':'愚人众','深渊':'提瓦特','七星':'璃月','幕府':'稻妻','公爵':'枫丹','审判庭':'枫丹'}[key] || e.regionContext || e.requires);
    const query = context ? context + '：' + key : key;
    assert.ok(assembleSystem(query).selected.includes(e.start),e.title+' / '+key);
  }
  const {system}=assembleSystem('');
  assert.ok(system.includes('## 提瓦特世界书 · 规范名称与触发表'));
  assert.ok(system.indexOf(triggerIndex)<system.indexOf('# 玩家身份判定机制'));
  assert.ok(!system.includes('### 迪卢克·莱艮芬德'));
  assert.deepEqual(assembleTurn('向导请带路',[]).messages,[{role:'system',content:system},{role:'user',content:'向导请带路\n'+suffix}]);
});

test('world boss dossiers, short names and domain terms select their original slots',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for (const line of ['丘林','多莫沃伊','原初之冰雕刻师']) assert.deepEqual(ids(line),[1889],line);
  for (const line of ['丘林斯','值夜的守望者','其他游戏里的守望者Boss','堕天使','原初人类的迁徙','去景区探索秘境','旅游秘境推荐','考古遗迹系列']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('丘林斯来找丘林'),[1889]);
  assert.deepEqual(ids('风息山的守望者'),[1891,1985]);
  for (const line of ['挑战守望者','堕天Boss','守望者堕天','堕天的打法']) assert.deepEqual(ids(line),[1891],line);
  assert.deepEqual(ids('阿佩普的绿洲守望者周本'),[1674,1883]);
  assert.deepEqual(ids('提瓦特的原初人类'),[1892]);
  assert.deepEqual(ids('其他游戏的原初人类','提瓦特'),[]);
  assert.deepEqual(ids('守望者Boss','魔兽世界'),[]);
  assert.deepEqual(ids('原初人类','在提瓦特'),[1892]);
  for (const line of ['若陀','若陀龙王','找若陀聊天']) assert.deepEqual(ids(line,'挑战若陀'),[2010],line);
  for (const line of ['挑战若陀','若陀龙王周本','Boss：若陀']) assert.deepEqual(ids(line),[1879,2010],line);
  assert.deepEqual(ids('若陀问我是否挑战女士'),[1743,1881,2010]);
  for (const line of ['天赋本','武器本','圣遗物本','秘境','景区的人间秘境，回提瓦特刷秘境']) assert.deepEqual(ids(line),[1898],line);
  assert.deepEqual(ids('去参观秘境','正在景区观光'),[]);
  assert.deepEqual(ids('提瓦特敌人：遗迹系列'),[1895]);
  assert.deepEqual(ids('遗迹系列的打法','正在提瓦特冒险'),[1895]);
  assert.deepEqual(ids('遗迹系列的打法'),[]);
  assert.deepEqual(ids('提瓦特的遗迹系列文物'),[]);
  for (const line of ['无相系列','无相之雷']) assert.deepEqual(ids(line),[1673],line);
});

test('Fatui skirmisher affiliations and tactics resolve independently across turns',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for (const line of ['愚人众先遣队的隶属关系','愚人众先遣队归谁管']) assert.deepEqual(ids(line),[1506],line);
  for (const line of ['愚人众先遣队','愚人众先遣队怎么打','如何对付愚人众先遣队']) assert.deepEqual(ids(line),[1895],line);
  assert.deepEqual(ids('愚人众先遣队隶属哪个组织，怎么打？'),[1506,1895]);
  assert.deepEqual(ids('愚人众先遣队隶属谁？怎么打破他们的护盾？'),[1506,1895]);
  assert.deepEqual(ids('愚人众先遣队归谁管，挑战吞星之鲸'),[1506,1884]);
  assert.deepEqual(ids('愚人众先遣队怎么打','愚人众先遣队隶属哪个组织'),[1895]);
  assert.deepEqual(ids('那他们隶属哪个组织','愚人众先遣队的打法'),[1506]);
  assert.deepEqual(ids('怎么打？','愚人众先遣队归谁管'),[1895]);
  assert.deepEqual(ids('愚人众先遣队归谁管，怎么打丘丘王'),[1506,1895]);
});

test('every reviewed row matches one original conditional span, with a continuous ordered assembly',async()=>{
  const markdown=await read('触发词审核.md');
  const rows=[...markdown.matchAll(/^\| (\d+)–(\d+) \| ([^|]+) \|/gm)].map(m=>[Number(m[1]),Number(m[2]),m[3]]);
  assert.deepEqual(rows,words.map(e=>[e.start+1,e.start+e.text.split('\n').length,e.keys[0]]));
  let next=prefix.split('\n').length;
  for(const entry of book) {
    assert.equal(entry.start,next,entry.title);
    next+=entry.text.split('\n').length;
  }
  assert.equal(next,source.sources[0].paragraphs);
  assert.equal(suffix.split('\n').length,source.sources[1].paragraphs);
  assert.deepEqual([...new Set(words.map(e=>e.group))],[...markdown.matchAll(/^## (.+)$/gm)].map(m=>m[1].trim()));
});

test('miscellaneous names, foreign folklore and fairy aliases respect their scope',()=>{
  const ids=(q,h='')=>assembleSystem(q,[{role:'assistant',content:h}]).selected.map(n=>n+1);
  for(const q of ['丹顶鹤观测站','丹顶鹤观察点','一只猫又来了','天狗食日','天狗食月','民间传说里的天狗','格林童话里的妖精','日本民间传说里的猫又','北欧神话里的龙王','英国童话里的妖精','《妖精的尾巴》','凡人修仙传的纯阳之体','其他游戏的换生灵','中国神话中的水龙王','树妖','风仙']) assert.deepEqual(ids(q),[],q);
  for(const [s,id] of [['鹤观',1992],['去鹤观测量地形',1992],['丹顶鹤观测站，之后去鹤观',1992],['猫又',2016],['一只猫又来了，再谈猫又',2016],['白夜国',1988],['仙人和魔神',2010],['霜精',2012],['雪精',2012],['灯之妖精',2012],['提瓦特族群的树妖',2012],['提瓦特的妖精风仙',2012],['北欧神话里的龙王，回到提瓦特聊龙王',2011]]) assert.deepEqual(ids(s),[id],s);
  assert.deepEqual(ids('树妖','研究提瓦特族群'),[2012]);
  assert.deepEqual(ids('树妖','正在看其他游戏的妖精'),[]);
  assert.deepEqual(ids('水龙王','正聊日本民间传说'),[]);
  assert.deepEqual(ids('树妖','<details>研究提瓦特族群</details>'),[]);
});

test('daily character names and explicit lineage requests keep separate original slots',()=>{
  const ids=(q,h='')=>assembleSystem(q,[{role:'assistant',content:h}]).selected.map(n=>n+1);
  for(const [name,base,added] of [
    ['菲林斯',1374,[2012]],['梦见月瑞希',886,[2013]],['沃雅妮莎',1470,[2014]],['一斗',830,[2015]],['绮良良',878,[2016]],
    ['裟罗',806,[2017]],['菈乌玛',1382,[2018]],['莉奈娅',1422,[1531,2019]],['希格雯',1152,[2020]],['重云',597,[2021]],
    ['那维莱特',1088,[2011]],['甘雨',637,[2010]],['特瓦林',1677,[2011]],['阿佩普',1675,[2011]]
  ]) {
    const both=[base,...added];
    assert.deepEqual(ids('去找'+name+'商量'),[base],name);
    assert.deepEqual(ids(name+'的血统'),both,name);
    assert.deepEqual(ids('那她属于哪个族群？',name),both,name);
    assert.deepEqual(ids(name,name+'的血统'),[base],name);
  }
  assert.deepEqual(ids('那她的血统呢','<details>绮良良</details>'),[]);
  assert.deepEqual(ids('绮良良','菲林斯的血统'),[878]);
  assert.deepEqual(ids('请绮良良介绍一斗的血统'),[830,878,2015]);
  assert.deepEqual(ids('菲林斯谈论别人的血统'),[1374]);
  assert.deepEqual(ids('换生灵'),[1531,2019]);
});

test('current input, previous visible story, homonyms and speaker labels',()=>{
  assert.deepEqual(assembleSystem('天空中传来琴声，树影落在荧光石上').selected,[]);
  assert.deepEqual(assembleSystem('在晨曦酒庄见迪卢克').selected,[299,355]);
  assert.deepEqual(assembleSystem('回到那夏镇找执灯人').selected,[1507,1520]);
  const last = [{role:'user',content:'荆夫港'},{role:'assistant',content:'<div class="top-status-bar">晨曦酒庄</div><text><npc c="温迪"><t>好久不见。</t></npc></text><details>钟离</details>'}];
  assert.deepEqual(assembleSystem('继续',last).selected,[371]);
  assert.ok(assembleSystem('琴团长').selected.includes(315));
  assert.ok(assembleSystem('旅行者（空）').selected.includes(1578));
});

test('Mondstadt character senses and statue mentions are resolved per occurrence',()=>{
  for (const [id,negative,positive] of [
    [411,['给红茶加一勺砂糖','配方：砂糖 20 克','让砂糖溶解','把砂糖倒入锅里','买一袋白砂糖','去杂货店买砂糖','用砂糖做饼干'],
      ['砂糖','去找砂糖做实验','砂糖递来一包砂糖','把一勺砂糖交给砂糖']],
    [315,['我给旅行者琴谱','她听见琴声','练琴','温迪弹奏竖琴','乐师在给钢琴调音','留下来听琴。','弹了一会儿琴','把琴放回琴盒'],
      ['琴','我去找琴商量','琴，早上好','琴听见了琴声','弹琴的人向琴打招呼','听琴汇报','我把琴叫来商量']],
    [371,['风神像','走到风神像旁'],['风神','风神看向风神像','温迪站在风神像旁']]
  ]) {
    for (const text of negative) assert.ok(!assembleSystem(text).selected.includes(id),text);
    for (const text of positive) assert.ok(assembleSystem(text).selected.includes(id),text);
  }
});

test('Liyue homonyms exclude only the non-character occurrence',()=>{
  for (const [key, excluded] of [
    ['魈','山魈'], ['北斗','北斗七星'], ['七七','七七八八'], ['胡桃','胡桃木'],
    ['白术','药材白术'], ['白术','药材：白术'], ['甘雨','久旱逢甘雨'],
    ['闲云','闲云野鹤'], ['行秋','旅行秋游'], ['行秋','举行秋季庆典'], ['岩神','岩神像']
  ]) {
    const id=words.find(e=>e.group==='璃月' && e.keys.includes(key)).start;
    assert.ok(!assembleSystem(excluded).selected.includes(id),excluded);
    for (const line of [key, '我请'+key+'同行', excluded+'旁，有人叫住了'+key]) {
      assert.ok(assembleSystem(line).selected.includes(id),line);
    }
    assert.ok(!assembleSystem('继续',[{role:'assistant',content:'<text>'+excluded+'</text>'}]).selected.includes(id));
  }
});

test('Liyue compact faction names, five merchant aliases and conditional Qixing',()=>{
  for (const id of [545,548,551]) {
    const title=words.find(e=>e.start===id).keys[0];
    assert.deepEqual(assembleSystem(title).selected,[526,id]);
    assert.deepEqual(assembleSystem(title.replace('·','')).selected,[526,id]);
  }
  const trade=words.find(e=>e.start===548);
  assert.deepEqual(trade.keys.slice(1),['北国银行','万有铺子','望舒客栈','万民堂','南十字船队']);
  for (const alias of trade.keys.slice(1)) assert.deepEqual(assembleSystem(alias).selected,[548]);
  const matches=(input,previous='')=>assembleSystem(input,[{role:'assistant',content:previous}]).selected.includes(536);
  for (const line of ['璃月七星','在璃月港询问七星','凝光召集七星','在群玉阁拜见七星','在璃月谈到北斗七星后去找七星议事']) assert.ok(matches(line),line);
  for (const line of ['七星','北斗七星','在璃月仰望北斗七星','在璃月夜观七星','璃月的七星瓢虫']) assert.ok(!matches(line),line);
  assert.ok(matches('去找七星','<text>一行人抵达璃月港。</text>'));
  assert.ok(!matches('在稻妻寻找七星','<text>上一站是璃月港。</text>'));
  assert.ok(!matches('去找七星','<details>璃月港</details>'));
});

test('Raiden daily mentions and targeted boss encounters occupy separate slots',()=>{
  const person=797, boss=1880;
  const prev=text=>[{role:'assistant',content:text}];
  for (const input of ['雷电将军','我找雷电将军喝茶','雷电将军说要挑战风魔龙','雷电将军打听附近的Boss','一心净土']){
    const {selected}=assembleSystem(input);
    assert.ok(selected.includes(person),input);
    assert.ok(!selected.includes(boss),input);
  }
  for (const input of ['挑战雷电将军','周本：雷电将军','雷电将军周本','雷电将军的BOSS','Boss雷电将军','雷电将军是本周的boss','周本挑战雷电将军']) {
    const {selected}=assembleSystem(input);
    assert.ok(selected.includes(person) && selected.includes(boss),input);
  }
  assert.ok(!assembleSystem('只想打周本').selected.includes(boss));
  assert.ok(!assembleSystem('Boss：特瓦林',prev('<text>遇见雷电将军</text>')).selected.includes(boss));
  assert.ok(!assembleSystem('与雷电将军喝茶',prev('<text>挑战雷电将军</text>')).selected.includes(boss));
  assert.ok(assembleSystem('继续',prev('<text>挑战雷电将军</text>')).selected.includes(boss));
  assert.ok(!assembleSystem('继续',prev('<details>挑战雷电将军</details>')).selected.includes(boss));
});

test('Inazuma names still match people after a colliding common noun',()=>{
  for(const [id,ordinary,name] of [
    [797,'雷神像','雷神'],[797,'旅行者影院','影'],[797,'树影','影'],[797,'看电影','影'],
    [773,'万叶集','万叶'],[829,'一斗米','一斗'],[821,'托马斯','托马']
  ]) {
    assert.ok(!assembleSystem(ordinary).selected.includes(id),ordinary);
    for (const line of [name,'我去找'+name+'商量',ordinary+'旁，我去找'+name+'商量']) {
      assert.ok(assembleSystem(line).selected.includes(id),line);
    }
  }
  assert.ok(!assembleSystem('树影摇头，电影正在放映').selected.includes(797));
});

test('幕府 needs Inazuma context and the two regional headings accept compact spellings',()=>{
  const has=(input,previous='')=>assembleSystem(input,[{role:'assistant',content:previous}]).selected.includes(743);
  for (const name of ['幕府','江户幕府','稻妻史书提到了江户幕府']) assert.ok(!has(name),name);
  for (const name of ['稻妻幕府','在鸣神岛拜访幕府','雷电将军询问幕府','在稻妻对比江户幕府和当地幕府','天领奉行','三奉行','勘定奉行']) assert.ok(has(name),name);
  assert.ok(has('向幕府报告','<text>我们刚到稻妻。</text>'));
  assert.ok(!has('研究江户幕府','<text>我们刚到稻妻。</text>'));
  assert.ok(!has('向幕府报告','<details>稻妻</details>'));
  assert.ok(!has('到璃月调查幕府','<text>我们刚到稻妻。</text>'));
  for(const [word,id] of [['潜伏者',758],['其他',760]]) {
    assert.deepEqual(assembleSystem('稻妻'+word).selected,[733,id]);
    assert.deepEqual(assembleSystem('稻妻·'+word).selected,[733,id]);
  }
});

test('Sumeru names and headings retain their source slots',()=>{
  const selected=input=>assembleSystem(input).selected;
  assert.deepEqual(selected('去草神像旁'),[]);
  assert.deepEqual(selected('草神站在草神像旁'),[969]);
  for(const name of ['纳西妲','小吉祥草王']) assert.deepEqual(selected(name),[969]);
  for(const name of ['赛诺','大风纪官']) assert.deepEqual(selected(name),[945]);
  for(const [key,id] of [['沙漠',914],['其他',916]]) {
    assert.deepEqual(selected('须弥'+key),[897,id]);
    assert.deepEqual(selected('须弥·'+key),[897,id]);
    assert.ok(!selected(key).includes(id));
  }
});

test('wanderer identity, construct lore and weekly boss have distinct triggers',()=>{
  const slots=[985,1677,1882];
  const hits=(input,last='')=>assembleSystem(input,[{role:'assistant',content:last}]).selected.filter(id=>slots.includes(id));
  for(const input of ['路上有个流浪者','遇到一名衣衫褴褛的流浪者','城里的流浪者们','这群流浪者','城里有流浪者','无家可归的流浪者','散兵游勇']) assert.deepEqual(hits(input),[],input);
  for(const input of ['散兵','流浪者','我去找流浪者商量','有个流浪者向散兵问路','散兵游勇走后，流浪者来了']) assert.deepEqual(hits(input),[985],input);
  for(const input of ['正机之神','讨论正机之神的来历','正机之神旁边还有其他Boss']) assert.deepEqual(hits(input),[1677],input);
  for(const input of ['挑战正机之神','周本：正机之神','正机之神周本','Boss正机之神','正机之神的BOSS','周本打正机之神','正机之神是一个Boss']) assert.deepEqual(hits(input),[1677,1882],input);
  assert.deepEqual(hits('跟散兵聊正机之神'),[985,1677]);
  assert.deepEqual(hits('散兵带我去挑战正机之神'),slots);
  assert.deepEqual(hits('散兵游勇正在挑战别的Boss'),[]);
  const fight='<text>去挑战正机之神。</text>';
  assert.deepEqual(hits('继续',fight),[1677,1882]);
  assert.deepEqual(hits('与散兵喝茶',fight),[985]);
  assert.deepEqual(hits('散兵游勇',fight),[]);
  assert.deepEqual(hits('聊聊正机之神的来历',fight),[1677]);
  assert.deepEqual(hits('继续','<details>挑战正机之神</details>'),[]);
  assert.deepEqual(hits('继续','<text><npc c="流浪者"><t>走吧。</t></npc></text>'),[985]);
});

test('Fontaine people, organizations and dragon lore resolve per mention and turn',()=>{
  const relevant=[1059,1087,1103,1143,2010];
  const hits=(input,previous='')=>assembleSystem(input,[{role:'assistant',content:previous}]).selected.filter(id=>relevant.includes(id));
  for (const name of ['阿蕾奇诺','仆人','去找愚人众执行官「仆人」','壁炉之家主人','拜访壁炉之家的主人']) assert.deepEqual(hits(name),[1143],name);
  for (const ordinary of ['府里来了个仆人','让仆人收拾房间','他是贵族家的仆人']) assert.deepEqual(hits(ordinary),[],ordinary);
  assert.deepEqual(hits('调查壁炉之家的组织结构'),[1059]);
  for (const input of ['壁炉之家主人谈壁炉之家的组织','谈谈阿蕾奇诺领导的组织','阿蕾奇诺的组织','壁炉之家主人介绍组织架构']) assert.deepEqual(hits(input),[1059,1143],input);
  assert.deepEqual(hits('找壁炉之家主人喝茶','<text>调查壁炉之家</text>'),[1143]);
  assert.deepEqual(hits('继续','<npc c="仆人"><t>跟我来。</t></npc>'),[1143]);
  for (const input of ['最高审判官','水龙王','我去找水龙王商量','水龙王问候旅行者']) assert.deepEqual(hits(input),[1087],input);
  for (const input of ['龙王','普通传说中的水龙王','聊聊一条水龙王','古籍记载的水龙王','水龙王一族','水龙王是一个统称']) assert.deepEqual(hits(input),[2010],input);
  assert.deepEqual(hits('找水龙王商量','<text>传说中的水龙王</text>'),[1087]);
  assert.deepEqual(hits('传说中的水龙王','<text>那维莱特</text>'),[2010]);
  assert.deepEqual(hits('请水龙王讲一讲传说中的水龙王'),[1087,2010]);
  assert.deepEqual(hits('水神像','<text>水神登台了</text>'),[]);
  assert.deepEqual(hits('水神看向水神像'),[1103]);
});

test('Fontaine context gates court and duke, both fortress slots survive',()=>{
  for (const [key,id] of [['审判庭',1047],['公爵',1095]]) {
    const has=(input,previous='')=>assembleSystem(input,[{role:'assistant',content:previous}]).selected.includes(id);
    assert.ok(!has(key));
    assert.ok(has('去枫丹拜访'+key));
    assert.ok(has('去找'+key,'<text>我们到了梅洛彼得堡。</text>'));
    assert.ok(!has('去找'+key,'<details>枫丹</details>'));
    assert.ok(!has('去璃月找'+key,'<text>上一站是枫丹。</text>'));
    assert.ok(!has('枫丹来信提到外国的'+key));
  }
  for (const [key,id] of [['剧院与明星',1055],['其他',1057]]) {
    for (const dot of ['','·']) assert.deepEqual(assembleSystem('枫丹'+dot+key).selected,[1037,id]);
  }
  assert.deepEqual(assembleSystem('梅洛彼得堡').selected,[1051,1990]);
});

test('Natlan names reject ordinary senses per occurrence and preserve the approved aliases',()=>{
  for (const [id, excluded, included] of [
    [1260,['火神像'],['火神','去火神像找玛薇卡']],
    [1196,['千古名句','这座城的古名','这个地方的古名','西安的古名是长安'],['古名','古名传承','千古名句也提到古名传承']],
    [1195,['燃素说','研究燃素说'],['燃素','燃素说之外再聊燃素']],
    [1184,['异世界的火之国','火之国（异世界）','到了异世界，这里的首都是火之国的王都','火影忍者里的火之国'],['火之国','异世界的火之国，回到提瓦特的火之国']],
    [1292,['伊法尔'],['伊法','告别伊法尔后找伊法']],
    [1268,[],['茜特菈莉','茜特拉莉']],
  ]) {
    for (const input of excluded) {
      assert.deepEqual(assembleSystem(input,[{role:'assistant',content:included[0]}]).selected,[],input);
    }
    for (const input of included) assert.deepEqual(assembleSystem(input).selected,[id],input);
  }
});

test('collective tribe heading and individual tribes select exactly the intended source spans',()=>{
  const names=['回声之子','悬木人','流泉之众','花羽会','沃陆之邦','烟谜主'];
  const history=[{role:'assistant',content:'<text>介绍纳塔六大部族。</text>'}];
  const all=[1184,1202,1203,1204,1205,1206,1207];
  assert.ok(triggerIndex.includes('纳塔六大部族'));
  assert.deepEqual(assembleSystem('纳塔六大部族').selected,all);
  assert.deepEqual(assembleSystem('继续',history).selected,all);
  for (const [i,key] of names.entries()) {
    assert.deepEqual(assembleSystem(key,history).selected,[1202+i],key);
    assert.deepEqual(assembleSystem(key).selected,[1202+i],key);
  }
  assert.deepEqual(assembleSystem('比较回声之子与花羽会',history).selected,[1202,1205]);
});

test('pilgrimage festival comparison is conditional and a daily follow-up drops the festival supplement',()=>{
  const read=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected.filter(id=>[1197,1906].includes(id));
  for (const s of ['归火圣夜巡礼','圣夜巡礼','比较圣夜巡礼的两套服装','在璃月比较圣夜巡礼的两套服装']) assert.deepEqual(read(s),[1197],s);
  const comparison='把圣夜巡礼与璃月的节庆作比较';
  for (const s of [comparison,'归火圣夜巡礼和各国节日','圣夜巡礼的跨国比较','对比别国的庆典与归火圣夜巡礼']) assert.deepEqual(read(s),[1197,1906],s);
  assert.deepEqual(read('去参加归火圣夜巡礼',comparison),[1197]);
  assert.deepEqual(read('去看巡礼','<text>刚到纳塔</text>'),[1197]);
  assert.deepEqual(read('那和其他国家的节庆相比呢？','去参加圣夜巡礼'),[1197,1906]);
  assert.deepEqual(read('继续',comparison),[1197,1906]);
  assert.deepEqual(read('继续','<details>'+comparison+'</details>'),[]);
  assert.deepEqual(read('去看海灯节'),[1906]);
  assert.deepEqual(read('各国节日'),[1906]);
});

test('regional festivals use the local setting and only add requested country dossiers',()=>{
  const ids=(q,last='')=>assembleSystem(q,[{role:'assistant',content:last}]).selected;
  for(const key of ['三祭','光华祭','若雷祭','夏日祭']) {
    assert.deepEqual(ids('介绍稻妻的'+key),[1906],key);
    assert.deepEqual(ids(key,'正在稻妻'),[1906],key);
    assert.deepEqual(ids(key),[],key);
  }
  for(const s of ['一年三祭','三祭祖先','京都夏日祭','萨尔茨堡歌剧节','歌剧节']) assert.deepEqual(ids(s),[],s);
  assert.ok(ids('枫丹的歌剧节').includes(1906));
  assert.ok(!ids('去维也纳歌剧节','在枫丹').includes(1906));
  assert.deepEqual(ids('纳塔荣花演武'),[1906]);
  assert.deepEqual(ids('稻妻三祭和国家概况'),[733,1906]);
  assert.deepEqual(ids('介绍纳塔荣花演武，也说说国家概况'),[1184,1906]);
  assert.deepEqual(ids('再介绍国家概况','稻妻三祭'),[733,1906]);
  assert.deepEqual(ids('稻妻三祭和纳塔概况'),[1184,1906]);
  assert.deepEqual(ids('七国主线'),[1931]);
});

test('Lantern Rite tables follow the requested scope and switch across visible turns',()=>{
  const ids=(q,last='')=>assembleSystem(q,[{role:'assistant',content:last}]).selected;
  for(const s of ['去看海灯节','2025年参加海灯节','比较璃月海灯节和蒙德风花节','比较海灯节与风花节的主题','比较2025年海灯节与风花节的主题']) assert.deepEqual(ids(s),[1906],s);
  for(const s of ['点查历届海灯节','海灯节某届的主题','2026年海灯节纪念谁','比较两届海灯节','历届海灯节纪念对象',
    '明霄升海平','飞彩镌流年','磬弦奏华夜','彩鹞栉春风','春曦画桃符','奔霄颂玉轮']) assert.deepEqual(ids(s),[1921],s);
  assert.deepEqual(ids('比较海灯节与风花节，再说2025年的纪念对象'),[1906,1921]);
  assert.deepEqual(ids('海灯节有什么习俗，以及哪一届的主题是春曦画桃符'),[1906,1921]);
  assert.deepEqual(ids('那一届的主题呢','璃月正在过海灯节'),[1921]);
  assert.deepEqual(ids('去参加海灯节','历届海灯节'),[1906]);
  assert.deepEqual(ids('点查历届海灯节','对比圣夜巡礼与各国节日'),[1921]);
  assert.deepEqual(ids('那一届的主题呢','<details>海灯节</details>'),[]);
});

test('special mechanisms resolve local power and exploration contexts, including visible history',()=>{
  const ids=(q,last='')=>assembleSystem(q,[{role:'assistant',content:last}]).selected;
  for(const title of ['战力等级','战力分级']) {
    assert.deepEqual(ids(title),[],title);
    assert.deepEqual(ids('提瓦特'+title),[1946],title);
    assert.deepEqual(ids(title,'钟离'),[620,1946],title);
  }
  for(const q of ['T0','T1','T5','T2级','其他游戏的战力等级']) assert.deepEqual(ids(q,'提瓦特战力等级'),[],q);
  assert.deepEqual(ids('钟离属于哪个战力层级'),[620,1946]);
  assert.deepEqual(ids('法涅斯的战力阶梯'),[1814,1946]);
  for(const key of ['地图迷雾','黑色迷雾','灰色迷雾']) {
    assert.deepEqual(ids(key),[],key);
    assert.deepEqual(ids('提瓦特探索：'+key),[1964],key);
    assert.deepEqual(ids(key,'正在提瓦特探路'),[1964],key);
    assert.deepEqual(ids(key,'<details>正在提瓦特探索</details>'),[],key);
  }
  assert.deepEqual(ids('提瓦特的天气预报说有灰色迷雾'),[]);
  assert.deepEqual(ids('其他游戏的地图迷雾','提瓦特探索'),[]);
  assert.deepEqual(ids('我们到了提瓦特。看看地图迷雾'),[1964]);
});

test('attrition, karma and affinity distinguish individual mentions of their ordinary senses',()=>{
  const ids=(q,last='')=>assembleSystem(q,[{role:'assistant',content:last}]).selected;
  for(const q of ['鞋底磨损','机械零件严重磨损','磨损的轴承','佛教所说的业障','念经消除业障',
    '品牌好感度','人们对该产品的好感度','民调显示选民的好感度下降']) assert.deepEqual(ids(q),[],q);
  for(const q of ['磨损','业障','佛经中的业障和提瓦特的业障','检查鞋底磨损后再问磨损机制']) assert.deepEqual(ids(q),[1956],q);
  assert.deepEqual(ids('鞋底磨损提醒我钟离也会磨损'),[620,1956]);
  assert.deepEqual(ids('魈的业障'),[556,1956]);
  assert.deepEqual(ids('柯莱的魔鳞病'),[929,1956]);
  assert.deepEqual(ids('好感度'),[1970]);
  assert.deepEqual(ids('民调统计品牌好感度，但我想提高钟离对我的好感度'),[620,1970]);
  assert.deepEqual(ids('轮胎磨损','谈论磨损'),[]);
  assert.deepEqual(ids('产品好感度','角色好感度'),[]);
});

test('cost supplement requires consequences tied to the Eye or the death curse',()=>{
  const ids=(q,last='')=>assembleSystem(q,[{role:'assistant',content:last}]).selected;
  for(const term of ['代价','副作用','后果']) {
    assert.deepEqual(ids('使用邪眼的'+term),[1956,1997],term);
    assert.deepEqual(ids('死之诅咒会带来什么'+term),[1545,1956],term);
    assert.deepEqual(ids('那它的'+term+'呢','邪眼'),[1956,1997],term);
  }
  assert.deepEqual(ids('邪眼会不会有副作用'),[1956,1997]);
  assert.deepEqual(ids('死之诅咒的具体后果'),[1545,1956]);
  assert.deepEqual(ids('邪眼 的副作用'),[1956,1997]);
  assert.deepEqual(ids('执行官邪眼的代价'),[1760,1956,1997]);
  assert.deepEqual(ids('若娜瓦的死之诅咒'),[1545,1844]);
  assert.deepEqual(ids('若娜瓦的死之诅咒有什么后果'),[1545,1844,1956]);
  assert.deepEqual(ids('邪眼和死之诅咒各有什么副作用'),[1545,1956,1997]);
  assert.deepEqual(ids('邪眼','执行官邪眼的后果'),[1997]);
  assert.deepEqual(ids('死之诅咒','死之诅咒的副作用'),[1545]);
  assert.deepEqual(ids('收好邪眼再问药物的副作用'),[1997]);
  assert.deepEqual(ids('药物有什么副作用','使用邪眼'),[1997]);
  assert.deepEqual(ids('它的后果呢','<details>邪眼</details>'),[]);
});

test('Snezhnaya names distinguish props, eye color, seasons, films and generic games',()=>{
  for (const [ids, ordinary, proper] of [
    [[1349],['冰神像','在冰神像前'],['冰神','冰神看向冰神像']],
    [[1342,2003],['她有一双灰眸','他的灰眸映着雪光','她生着灰眸','黑发灰眸','灰眸少年','灰眸的少女'],['灰眸','启用灰眸系统','灰眸的少女启动了灰眸']],
    [[1312],['从秋季至冬季','秋末至冬初'],['至冬','秋季至冬季期间去至冬']],
    [[1389],['热爱诺贝尔','热爱诺贝尔文学奖'],['爱诺','热爱诺贝尔的爱诺']],
    [[1322],['看电影《雪国列车》','《雪国列车》的导演','雪国列车这部电影'],['雪国列车','看完电影《雪国列车》，我登上雪国列车']],
    [[1323],['第三人称射击游戏','第三人称射击类手游','在射击游戏中切换第三人称射击'],['第三人称射击','在影域开启第三人称射击模式']],
  ]) {
    const hits=(input,previous='')=>assembleSystem(input,[{role:'assistant',content:previous}]).selected.filter(id=>ids.includes(id));
    for (const s of ordinary) assert.deepEqual(hits(s,proper[0]),[],s);
    for (const s of proper) assert.deepEqual(hits(s),ids,s);
  }
  for (const s of ['旅行者切换冰元素','冰元素旅行者']) assert.deepEqual(assembleSystem(s).selected,[1325,1578,1584]);
  assert.deepEqual(assembleSystem('星烁反应').selected,[1324,1632]);
  assert.deepEqual(assembleSystem('臻冰造物').selected,[1326,1560,2004]);
});

test('Tartaglia titles, explicit organization discussion and targeted weekly fights',()=>{
  const hits=(input,last='')=>assembleSystem(input,[{role:'assistant',content:last}]).selected;
  for (const s of ['达达利亚','跟达达利亚喝茶','执行官公子','愚人众执行官公子','愚人众执行官第十一席「公子」','愚人众十一执行官末席「公子」达达利亚','愚人众第十一席「公子」','达达利亚（愚人众执行官第十一席）','公子·达达利亚','达达利亚说要挑战别的Boss']) {
    assert.deepEqual(hits(s),[1357],s);
  }
  for (const s of ['挑战公子','挑战达达利亚','公子周本','Boss：公子','达达利亚的Boss','周本：公子·达达利亚','挑战愚人众执行官第十一席「公子」']) {
    assert.deepEqual(hits(s),[1357,1878],s);
  }
  for (const s of ['贵公子','贵公子周本','挑战贵公子']) assert.deepEqual(hits(s),[],s);
  assert.deepEqual(hits('问愚人众执行官公子关于愚人众的事'),[1357,1506]);
  assert.deepEqual(hits('问执行官公子关于其他执行官的事'),[1357,1710]);
  assert.deepEqual(hits('跟达达利亚聊愚人众和执行官群体'),[1357,1506,1710]);
  assert.deepEqual(hits('愚人众执行官公子','愚人众执行官集体去挑战公子'),[1357]);
  assert.deepEqual(hits('继续','<text>挑战公子</text>'),[1357,1878]);
  assert.deepEqual(hits('继续','<details>挑战公子</details>'),[]);
  assert.ok(!hits('挑战别的Boss','挑战达达利亚').includes(1878));
});

test('Nod-Krai names and source slots separate local, foreign and ordinary readings',()=>{
  const read=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for(const name of ['Nod-Krai','nod-krai','NOD KRAI','nOd   kRaI','Nod‑Krai']) assert.deepEqual(read(name),[1494],name);
  assert.deepEqual(read('Xnod-kraiX'),[]);
  for(const name of ['黑森林蛋糕','黑森林风味巧克力','黑森林火腿','德国的黑森林','去德国旅游时参观黑森林','黑森林位于德国']) assert.deepEqual(read(name,'黑森林'),[],name);
  for(const name of ['黑森林','在黑森林吃黑森林蛋糕','参观德国黑森林后，回到提瓦特的黑森林']) assert.deepEqual(read(name),[1525],name);
  for(const name of ['巫师三：狂猎','《巫师3：狂猎》','玩《巫师3》时遇到了狂猎','狂猎来自《巫师3》']) assert.deepEqual(read(name,'狂猎'),[],name);
  for(const name of ['狂猎','玩完《巫师3：狂猎》，回去调查狂猎']) assert.deepEqual(read(name),[1508,1666],name);
  assert.deepEqual(read('去黑森林','我们正在德国旅游。'),[]);
  assert.deepEqual(read('调查狂猎','刚打开《巫师3》。'),[]);
  assert.deepEqual(read('回到提瓦特调查狂猎','刚打开《巫师3》。'),[1508,1666]);
  assert.deepEqual(read('去黑森林','<details>在德国旅游</details>'),[1525]);
  assert.deepEqual(read('三月女神节','三月女神'),[]);
  assert.deepEqual(read('三月女神节过后提起三月女神'),[1529]);
  assert.deepEqual(read('摄影域名','至冬影域'),[]);
  for(const name of ['影域','至冬影域','查完摄影域名再进影域']) assert.deepEqual(read(name,'至冬'),[1532],name);
  assert.deepEqual(read('离开至冬影域后回到至冬'),[1312,1532]);
  assert.deepEqual(read('银月之庭'),[1515,1521]);
  assert.deepEqual(read('霜月神殿','银月之庭'),[1521]);
  assert.deepEqual(read('月之轮'),[1528,1999]);
  assert.deepEqual(read('换生灵'),[1530,2018]);
});

test('expedition and coven select the parent or local dossier only in the stated context',()=>{
  const read=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for (const name of ['远征军','西风骑士团远征军','西风骑士团的远征军']) assert.deepEqual(read(name,'西风骑士团'),[1510],name);
  for (const name of ['谈谈远征军的母组织','西风骑士团及其远征军','远征军向骑士团总部汇报']) assert.deepEqual(read(name),[284,1510],name);
  assert.deepEqual(read('再讲讲他们的母组织','西风骑士团远征军'),[284,1510]);
  assert.deepEqual(read('西风骑士团'),[284]);
  assert.deepEqual(read('魔女会'),[1768]);
  assert.deepEqual(read('魔女会的尼可'),[498,1768,1783]);
  assert.deepEqual(read('在挪德卡莱见魔女会的尼可'),[498,1494,1511,1768,1783]);
  for(const last of ['<text>抵达那夏镇。</text>','<text>从蒙德返回挪德卡莱。</text>','<text>抵达 NOD KRAI。</text>']) assert.ok(read('调查魔女会',last).includes(1511),last);
  for(const last of ['<details>抵达那夏镇。</details>','在蒙德']) assert.ok(!read('调查魔女会',last).includes(1511),last);
  assert.ok(!read('回到蒙德谈魔女会','抵达那夏镇').includes(1511));
  assert.ok(!read('从挪德卡莱返回蒙德找魔女会').includes(1511));
  assert.ok(read('继续','<text>在伦波岛遇见魔女会。</text>').includes(1511));
});

test('traveller names, direct dossier lookups and seven elemental switches',()=>{
  const ids=(line,prior='')=>assembleSystem(line,[{role:'assistant',content:prior}]).selected;
  for (const name of ['空','荧']) {
    for (const line of [name,'我去找'+name+'商量']) assert.deepEqual(ids(line),[1578],line);
  }
  for (const line of ['空气','天空','空位','荧光石','我去找空地','我去找荧光石','一位路过的旅行者','路上遇到个旅行者','普通旅行者','一位旅行者空手而来','旅行者影院']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('路过旅行者影院，我去找荧商量'),[1578]);
  for(const [name,start] of [['旅行者基础档案',1578],['旅行者元素形态',1584],['旅行者核心设定',1596]]) {
    for(const query of [name,'点查：'+name,'请查阅「'+name+'」']) assert.deepEqual(ids(query,'温迪和派蒙到了七天神像'),[start],query);
  }
  for(const element of '风岩雷草水火冰') {
    const expected=element==='冰'?[1325,1578,1584]:[1578,1584];
    for (const key of ['旅行者切换'+element+'元素',element+'元素旅行者']) assert.deepEqual(ids(key),expected,key);
  }
  assert.deepEqual(ids('七国旅历'),[1596]);
  assert.deepEqual(ids('旅行者与星之楔'),[1578,1596,2000]);
  assert.deepEqual(ids('星之楔'),[2000]);
  assert.deepEqual(ids('其他作品的世界树'),[]);
  assert.deepEqual(ids('介绍世界树','我们正在讨论北欧神话'),[]);
  assert.deepEqual(ids('回到须弥调查世界树','刚谈过别的游戏'),[897,1596]);
  assert.deepEqual(ids('比较北欧神话的世界树和提瓦特的世界树'),[1596]);
});

test('Paimon origins, statue resonance and Descender identity require the relevant context',()=>{
  const ids=(line,prior='')=>assembleSystem(line,[{role:'assistant',content:prior}]).selected;
  assert.deepEqual(ids('派蒙今天想吃什么','谈派蒙的身世'),[1578]);
  assert.deepEqual(ids('跟派蒙聊聊派蒙的来历'),[1578,1596]);
  assert.deepEqual(ids('派蒙说起自己的身世'),[1578,1596]);
  assert.ok(!ids('派蒙问凯亚和天理的关系').includes(1596));
  assert.deepEqual(ids('派蒙与天理是什么关系'),[1578,1596,1861]);
  assert.ok(!ids('派蒙问起凯亚的身世').includes(1596));
  assert.deepEqual(ids('谈谈她的身世','和派蒙同行'),[1578,1596]);
  assert.deepEqual(ids('激活七天神像','在七天神像与风元素共鸣'),[1637]);
  assert.deepEqual(ids('和派蒙去七天神像'),[1578,1637]);
  for (const line of ['在七天神像与风元素共鸣','到七天神像切换火元素','与七天神像共鸣','在七天神像前，选择与风元素共鸣']) assert.deepEqual(ids(line),[1584,1637],line);
  assert.deepEqual(ids('与风元素共鸣','刚激活七天神像'),[1584,1637]);
  assert.deepEqual(ids('激活七天神像，音乐会引起听众共鸣'),[1637]);
  assert.deepEqual(ids('泛谈降临者','旅行者是第四降临者'),[1861]);
  assert.deepEqual(ids('旅行者向降临者问路'),[1578,1861]);
  for (const line of ['旅行者是第四降临者','旅行者身为一名降临者','旅行者的降临者身份','身为降临者的旅行者','跟荧讨论她的降临者身份','空作为降临者']) assert.deepEqual(ids(line),[1578,1596,1861],line);
});

test('elemental reaction mentions distinguish physical senses at each occurrence',()=>{
  const ids=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for (const [name,ordinary] of [
    ['蒸发','水分慢慢蒸发'],['融化','冰块融化'],['超载','车辆超载'],
    ['冻结','冻结账户'],['感电','电器漏电让人感电'],['绽放','鲜花绽放'],
    ['激化','矛盾激化'],['碎冰','一袋碎冰'],['扩散','花粉扩散'],['结晶','盐水结晶'],
  ]) {
    assert.deepEqual(ids(ordinary,'触发'+name+'反应'),[],ordinary);
    for (const line of [name,'点查：'+name,'触发'+name+'反应',ordinary+'后触发'+name+'反应']) assert.deepEqual(ids(line),[1611],line);
  }
  assert.deepEqual(ids('冰块融化后触发融化反应'),[1611]);
  assert.deepEqual(ids('魔物被冻结了'),[1611]);
  assert.deepEqual(ids('先让敌人感电，再看账户是否冻结'),[1611]);
  assert.deepEqual(ids('冰块融化后触发月绽放'),[1625]);
  for (const term of ['界外力量','光界力','虚无力']) assert.deepEqual(ids(term),[1607],term);
  for (const term of ['元素扩散','元素结晶','扩散反应','结晶反应']) assert.deepEqual(ids(term),[1611],term);
});

test('lunar and stellar special reactions retain their dedicated slots until a common reaction is compared',()=>{
  const ids=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for (const line of ['月感电','触发月绽放','月感电的元素反应','月绽放是哪一种元素反应','元素反应：月绽放','对比月绽放和月感电']) assert.deepEqual(ids(line,'触发感电'),[1625],line);
  for (const line of ['比较月绽放与普通反应','月感电和感电有什么区别','比较绽放和月绽放','月绽放与普通绽放的区别']) assert.deepEqual(ids(line),[1611,1625],line);
  assert.deepEqual(ids('那与普通感电相比呢','触发月感电'),[1611,1625]);
  assert.deepEqual(ids('今天只聊月绽放','比较月绽放与普通反应'),[1625]);
  for (const line of ['星超导','星扩散','星扩散反应','星扩散属于什么元素反应']) assert.deepEqual(ids(line,'触发月感电'),[1632],line);
  assert.deepEqual(ids('月超导','星扩散'),[]);
  assert.ok(!triggerIndex.includes('月超导'));
  assert.deepEqual(ids('地脉异常'),[1637,1702]);
  assert.deepEqual(ids('星烁反应'),[1324,1632]);
  assert.deepEqual(ids('激活七天神像'),[1637]);
  assert.deepEqual(ids('在七天神像切换元素'),[1584,1637]);
});

test('Abyss concepts, classes and direct lookups occupy their original source slots',()=>{
  const ids=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for (const line of ['深渊','站在悬崖边俯瞰深渊','提瓦特脚下的深渊','站在坎瑞亚的深渊边','在蒙德山谷里有一道深渊']) assert.ok(!ids(line).includes(1649),line);
  assert.deepEqual(ids('提瓦特的深渊'),[1649]);
  assert.ok(ids('在悬崖边俯瞰深渊，再讨论提瓦特的深渊').includes(1649));
  assert.ok(ids('讨论深渊','在提瓦特旅行').includes(1649));
  assert.ok(!ids('瞧那万丈深渊','在提瓦特旅行').includes(1649));
  assert.ok(!ids('讨论深渊','<details>提瓦特</details>').includes(1649));
  for(const element of '风岩雷草水火冰') assert.deepEqual(ids('无相之'+element),[1673]);
  for(const kind of ['深渊法师','深渊信徒','黑蛇众']) assert.deepEqual(ids(kind,'讨论提瓦特的深渊').filter(i=>[1649,1660,1679].includes(i)),[1679]);
  assert.deepEqual(ids('五大罪人'),[1654]);
  for (const greeting of ['Mimi tomo','MIMI TOMO']) assert.deepEqual(ids(greeting),[1686]);
  for (const [name,id] of [['深渊教团分类',1679],['神之心机制',1697]]) {
    for(const line of [name,'点查：'+name,'请查阅「'+name+'」']) assert.deepEqual(ids(line,'温迪和特瓦林提起神之心'),[id]);
  }
  assert.deepEqual(ids('神之心'),[1697,1996]);
  assert.deepEqual(ids('比较深渊教团和深渊教团分类'),[1660,1679]);
});

test('Abyss dossiers and weekly encounters distinguish daily names, targets and overlapping titles',()=>{
  const ids=(input,prior='')=>assembleSystem(input,[{role:'assistant',content:prior}]).selected;
  for (const [aliases,local,boss] of [
    [['狼王','北风狼王','安德留斯','北风狼王·安德留斯'],1672,1877],
    [['风魔龙','特瓦林','风魔龙·特瓦林'],1676,1876],
    [['阿佩普','草龙','草龙·阿佩普','阿佩普的绿洲守望者'],1674,1883],
    [['雷内','水形幻人','雷内/水形幻人'],1675,1885],
  ]) {
    for (const name of aliases) {
      assert.deepEqual(ids('谈谈'+name+'的故事','挑战'+name),[local],name);
      for(const cue of ['挑战了'+name,'周本：'+name,name+'的Boss']) assert.deepEqual(ids(cue),[local,boss],cue);
      assert.deepEqual(ids('继续','<text>挑战'+name+'</text>'),[local,boss]);
      assert.ok(!ids('挑战其他Boss','<text>挑战'+name+'</text>').includes(boss));
    }
  }
  for (const line of ['雷内尔','挑战雷内尔','雷内尔周本']) assert.deepEqual(ids(line,'挑战雷内'),[],line);
  assert.deepEqual(ids('告别雷内尔后挑战水形幻人'),[1675,1885]);
  assert.deepEqual(ids('安德留斯说要挑战特瓦林'),[1672,1676,1876]);
  assert.deepEqual(ids('阿佩普的绿洲守望者和守望者·堕天'),[1674,1891]);
  assert.deepEqual(ids('狼王向我发起挑战'),[1672,1877]);
});

test('Harbinger titles select exactly one dossier unless a separate organization or seat is requested',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for (const [title,id] of [['丑角',1725],['博士',1734],['少女',1413],['仆人',1143],['公鸡',1710],['散兵',985],['木偶',1437],['女士',1743],['富人',1710],['队长',1751],['公子',1357]]) {
    for(const line of ['执行官'+title,'去找愚人众执行官「'+title+'」']) assert.deepEqual(ids(line,'聊愚人众和执行官席位'),[id],line);
  }
  for(const [line,id] of [['第八席女士',1743],['愚人众博士',1734],['艾弗提尔维特',1725],['愚人众统括官',1725],['执行官全席',1710],['愚人众十一执行官',1710]]) assert.deepEqual(ids(line),[id],line);
  for(const line of ['第一席','第十席','第8席','末席']) assert.deepEqual(ids(line,'刚遇见卡皮塔诺和皮耶罗'),[],line);
  assert.deepEqual(ids('统括官'),[]);
  assert.deepEqual(ids('去找统括官','见到愚人众使者'),[1725]);
  for(const line of ['戏曲里的丑角','舞台上扮演丑角','京剧丑角','丑角行当','戏班里的一名丑角']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('看完京剧丑角，再去见执行官丑角'),[1725]);
  assert.deepEqual(ids('请愚人众执行官博士介绍愚人众'),[1506,1734]);
  assert.deepEqual(ids('问执行官博士关于组织的事'),[1506,1734]);
  assert.deepEqual(ids('问愚人众执行官博士他排第几席'),[1710,1734]);
  assert.deepEqual(ids('请执行官博士谈愚人众和其他执行官'),[1506,1710,1734]);
});

test('Signora battles and Harbinger power origins require explicit subjects',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for(const line of ['罗莎琳','炎之魔女','第八席「女士」','罗莎琳（愚人众第八席）']) assert.deepEqual(ids(line,'去挑战女士'),[1743],line);
  for(const line of ['女士周本','Boss：女士','挑战女士','挑战罗莎琳','炎之魔女的Boss','第八席女士周本']) assert.deepEqual(ids(line),[1743,1881],line);
  for(const line of ['女士','挑战这位女士','那位女士的Boss','女士们','挑战女士们','挑战女士组冠军','某位女士的Boss','一位女士在问路']) assert.deepEqual(ids(line,'去挑战女士'),[],line);
  assert.deepEqual(ids('罗莎琳想挑战其他Boss'),[1743]);
  assert.deepEqual(ids('邪眼','讨论执行官的邪眼'),[1997]);
  assert.deepEqual(ids('执行官的邪眼'),[1760,1997]);
  assert.deepEqual(ids('那他的邪眼呢','去找执行官博士'),[1760,1997]);
  for(const line of ['执行官的力量来源','执行官的力量从哪里来','执行官如何获得力量']) assert.deepEqual(ids(line),[1760],line);
  for(const line of ['点查全席补充','点查：愚人众执行官全席补充','愚人众执行官全席补充']) assert.deepEqual(ids(line,'执行官使用邪眼'),[1760],line);
});

test('Hexenzirkel codes require a full scoped letter and select only the requested dossiers',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for(const code of ['A','R','M','B','N']) {
    const slots=code==='N'?[498,1783]:[1773];
    for(const key of ['魔女会'+code,'魔女会＋'+code,'魔女会成员代号「'+code+'」']) assert.deepEqual(ids(key),slots,key);
    assert.deepEqual(ids(code,'聊魔女会N'),[],code);
  }
  assert.deepEqual(ids('魔女会A、R、M、B'),[1773]);
  assert.deepEqual(ids('魔女会的N和R'),[498,1773,1783]);
  assert.deepEqual(ids('魔女会NR'),[1768]);
  assert.deepEqual(ids('在希汐岛询问魔女会N'),[498,1511,1518,1783]);
  for(const [name,id] of [['魔女会组织性质',1768],['魔女会成员',1773],['魔女会经典逸闻',1805],['魔女会逸闻',1805]]) {
    assert.deepEqual(ids(name,'抵达那夏镇'),[id],name);
    assert.deepEqual(ids('点查：'+name,'在希汐岛讨论魔女会'),[id],name);
  }
  for(const [name,id] of [['尼可·莱恩',1783],['杜林',1791],['布伦妮',1799]]) {
    assert.deepEqual(ids('点查「'+name+'」','在那夏镇'),[id],name);
    assert.deepEqual(ids('点查魔女会篇·'+name),[id],name);
  }
  assert.deepEqual(ids('讲讲魔女会逸闻'),[1805]);
  assert.deepEqual(ids('魔女领域'),[1805]);
});
test('coven names distinguish people, places and gold; Alice adds the roster for code queries',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  assert.deepEqual(ids('尼可拉斯','魔女会的尼可'),[]);
  assert.deepEqual(ids('魔女会的尼可拉斯'),[1768]);
  assert.deepEqual(ids('尼可莱恩'),[498]);
  assert.deepEqual(ids('尼可拉斯向魔女会的尼可莱恩问路'),[498,1768,1783]);
  for(const line of ['去意大利的杜林旅游','杜林位于意大利','去杜林旅游看都灵的比赛']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('去杜林旅游','刚刚抵达意大利'),[]);
  assert.deepEqual(ids('从意大利的杜林回来后，拜访魔女会的杜林'),[482,1768,1791]);
  for (const line of ['黄金','炼金术士用黄金支付','炼金术士把黄金熔成金锭']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('魔女会购买了两公斤黄金'),[1768]);
  assert.deepEqual(ids('魔女会的黄金'),[1768,1773]);
  assert.deepEqual(ids('炼金术士「黄金」'),[1773]);
  assert.deepEqual(ids('艾莉丝','去找魔女会的尼可'),[1768]);
  for(const line of ['艾莉丝的代号是什么','艾莉丝在魔女会用哪个代号','我想知道艾莉丝是哪个字母']) assert.deepEqual(ids(line),[1768,1773],line);
  assert.deepEqual(ids('那她的代号呢','遇见艾莉丝'),[1768,1773]);
});

test('Four Shades roster, individual offices and Heavenly Principles have independent triggers',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for(const [title,id] of [['空之执政',1826],['时之执政',1835],['死之执政',1844],['生之执政',1853]]) {
    assert.deepEqual(ids(title,'天理和四影是什么关系'),[id],title);
    assert.deepEqual(ids('天理四影中的'+title),[1819,id],title);
  }
  for(const line of ['四影名单','天理四影','天理四影如何分工','列出四位执政','四位执政分别掌管什么']) assert.deepEqual(ids(line),[1819],line);
  assert.deepEqual(ids('时之执政的权能是什么'),[1835]);
  assert.deepEqual(ids('空之执政与时之执政如何分工'),[1819,1826,1835]);
  assert.deepEqual(ids('天理四影中的若娜瓦'),[1819,1844]);
  for(const line of ['四影效忠于天理','天理与四影是什么关系','受命于天理的四影','天理四影和天理是什么关系']) assert.deepEqual(ids(line),[1819,1861],line);
  assert.deepEqual(ids('她们与天理的关系呢','天理四影'),[1819,1861]);
  assert.deepEqual(ids('天理四影','四影效忠于天理'),[1819]);
  assert.deepEqual(ids('生之执政莱茵多特'),[1773,1853]);
  assert.deepEqual(ids('旅行者是第四降临者'),[1578,1596,1861]);
  assert.deepEqual(ids('派蒙与天理的关系'),[1578,1596,1861]);
});

test('celestial names and foreign place names are disambiguated at each occurrence',()=>{
  const ids=(line,last='')=>assembleSystem(line,[{role:'assistant',content:last}]).selected;
  for(const line of ['没有天理','还有没有天理','还有天理吗','天理难容','天理昭昭','世间自有天理','存天理，灭人欲','上天理应公正']) assert.deepEqual(ids(line,'天理'),[],line);
  assert.deepEqual(ids('天理难容，去天空岛找天理'),[1861,1867]);
  for(const line of ['收纳贝壳','纳贝尔']) assert.deepEqual(ids(line,'生之执政'),[],line);
  assert.deepEqual(ids('收纳贝壳后我去找纳贝商量'),[1853]);
  for(const line of ['苏格兰天空岛','天空岛位于苏格兰','英国苏格兰的天空岛']) assert.deepEqual(ids(line),[],line);
  assert.deepEqual(ids('明天去天空岛','正在苏格兰旅行'),[]);
  assert.deepEqual(ids('离开苏格兰的天空岛，回到提瓦特的天空岛'),[1867]);
  assert.deepEqual(ids('天空岛','<details>苏格兰</details>'),[1867]);
  for(const line of ['寒天之钉','天空岛']) assert.deepEqual(ids(line),[1867],line);
  assert.deepEqual(ids('原初的那一位'),[1814]);
  for(const line of ['Asmoday','asmoday','ASMODAY']) assert.deepEqual(ids(line),[1826],line);
  assert.deepEqual(ids('Asmodaylight'),[]);
  for(const line of ['卡伊洛斯','常世大神','千风之神','不变世界的统领与执政']) assert.deepEqual(ids(line),[1835],line);
});

test('witch dossiers require a named character and explicit coven context in the same source',()=>{
  for(const [key,local,coven] of [['杜林',482,1791],['尼可',498,1783],['尼可·莱恩',498,1783],['布伦妮',506,1799]]){
    assert.deepEqual(assembleSystem(key).selected,[local]);
    assert.ok(assembleSystem('魔女会的'+key).selected.includes(coven));
    assert.ok(assembleSystem('继续',[{role:'assistant',content:'<text>说说魔女会的'+key+'</text>'}]).selected.includes(coven));
    assert.ok(!assembleSystem(key,[{role:'assistant',content:'<text>魔女会</text>'}]).selected.includes(coven));
    assert.ok(!assembleSystem('继续',[{role:'assistant',content:'<details>魔女会</details><text>'+key+'</text>'}]).selected.includes(coven));
    for(const other of [1783,1791,1799].filter(i=>i!==coven)) assert.ok(!assembleSystem('魔女会的'+key).selected.includes(other));
  }
  assert.ok(!assembleSystem('魔女猎人').selected.includes(1799));
  assert.ok(assembleSystem('魔女会的魔女猎人').selected.includes(1799));
});

test('reference assets and sandboxed setup handoff',async()=>{
  assert.equal(sha(await bytes('resources/opening.html')),source.openingSha256);
  const assets=[...await json('resources/assets.json'),...await json('resources/portraits.json')];
  for(const asset of assets) assert.equal(sha(await bytes('public/'+asset.file)),asset.sha256);
  const html=renderSetup();
  assert.match(html,/genshin-opening/);
  for (const id of ['preview-area','copy-button','transfer','story-left','story-right','p-words','avatar-code-box']) assert.ok(html.includes('id="'+id+'"'),id);
  assert.match(html,/img\.src = presetImages\[url\]/);
  assert.doesNotMatch(await read('public/story.css'),/https:\/\/catai\.wiki|#installedBuiltInCss/);
  const display=genshinPresentation('<text><npc c="温迪" onclick="alert(1)"><n>温迪</n><t>你好</t></npc></text><details class="hero-modal"><summary>档案</summary>内容</details><img src="bad"><script>bad()</script>');
  assert.match(display,/<npc c="温迪">/);assert.match(display,/class="hero-modal"/);assert.doesNotMatch(display,/onclick|script|<img/);
});

test('two simulated archive turns use current lore and preserve the post prompt',async t=>{
  assert.equal(genshinOptions({}),null);
  const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
  const fake=async(url,options)=>{
    if(options.method==='GET') return Response.json({models:[{model,name:'离线验证',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
    const body=JSON.parse(options.body),raw='<text><npc c="温迪"><n>温迪</n><t>欢迎。</t></npc></text>',sse='data: [DONE]\n\n';
    seen.push(body.messages);
    return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
  };
  const directory=await mkdtemp(join(tmpdir(),'genshin-test-'));
  const {server}=await start({port:0,directory,autoSummaryV2:true});
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  installGenshin(server,{directory,control:{enabled:true,limit:2,baseURL:'http://server:8000',serviceToken:'synthetic-genshin-local-test-credential',fetcher:fake}});
  const origin='http://127.0.0.1:'+server.address().port;
  async function api(route,body){
    const res=await fetch(origin+'/rpg-app/genshin/chat/api/'+route,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await res.json();assert.ok(res.ok,JSON.stringify(data));return data;
  }
  let s=await api('sessions',{opening:''});
  assert.equal(seen.length,0);
  const path='sessions/'+s.id;
  for(const input of ['在晨曦酒庄见迪卢克','继续']){
    const state=await api(path+'/memory-palace');
    s=await api(path+'/send',{input,operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision});
    assert.equal(seen.at(-1).at(-1).content,input+'\n'+suffix);
    assert.equal(s.turns.at(-1).input,input);
  }
  assert.match(seen[0][0].content,/### 迪卢克·莱艮芬德/);
  assert.match(seen[1][0].content,/### 温迪/);
  assert.doesNotMatch(seen[1][0].content,/### 迪卢克·莱艮芬德/);
  assert.match(s.turns[0].html,/genshin-story/);
  assert.equal((await fetch(origin+'/rpg-app/genshin/resources/worldbook.json')).status,404);
});
