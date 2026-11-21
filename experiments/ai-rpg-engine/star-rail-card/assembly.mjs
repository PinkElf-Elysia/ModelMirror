import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {parseFragment} from 'parse5';

const read = name => readFileSync(new URL('./resources/' + name, import.meta.url), 'utf8');
const template = read('system.txt');
export const book = JSON.parse(read('worldbook.json'));
const slots = ['世界','人物','命途','面板','直播','文风','年表','剧情'];
const rules = book.filter(entry => entry.mode === 'rule');
const ruleSlots = new Set(rules.map(entry => entry.slot));
const originalTitles = new Set(book.filter(entry => entry.mode !== 'rule').map(entry => entry.title));
const seenAliases = new Set();
const guides = slots.map(slot => {
  const names = book.filter(entry => entry.slot === slot).map(entry => {
    const aliases = entry.keys.filter(key => key !== entry.title);
    if (entry.openingPlace) aliases.push('开局：' + entry.openingPlace);
    const visible = [];
    for (const alias of aliases) {
      if (!originalTitles.has(alias) && !seenAliases.has(alias) &&
          (slot !== '剧情' || alias.startsWith('开局：'))) visible.push(alias);
      seenAliases.add(alias);
    }
    return visible.length ? entry.title + '〔' + visible.join('、') + '〕' : entry.title;
  });
  return slot + '：' + names.join('／');
});
export const guide = ['【世界书名称库】','需要某条资料时，可在本轮末行写〈调用：条目全名〉，宿主于下一轮装配。剧情名称和揭示条件沿用原文。', ...guides, '规则：'+rules.map(e=>e.title).join('／')].join('\n');
export function systemPrompt() { return template.replace('⟦索引⟧', () => guide); }

const norm = s => s.normalize('NFKC').toLowerCase().replace(/[•・]/g,'·');
function matches(text, key, entry) {
  const k = norm(key);
  if (key === '黑塔') return text.replace(/(?:空间站[「“"]?黑塔|黑塔[」”"]?空间站|大黑塔)/g,'').includes(k);
  if (key === '刃' || key === '岚') {
    const contexts = key === '刃' ? /(?:星核猎手|千冶|角色|找|问|与|和|见到|叫住|看向)[「“]?刃(?![口尖具])/ : /(?:星神|巡猎|帝弓|拜见)[「“]?岚/;
    return text === k || contexts.test(text) || new RegExp('(?:^|[\\s「“《])'+k+'(?:$|[\\s」”》，。])').test(text);
  }
  if (/^[a-z0-9 ]+$/.test(k)) return new RegExp('(?<![a-z0-9])'+k+'(?![a-z0-9])').test(text);
  if (entry.slot === '剧情' && [...k].length <= 3)
    return text===k || ['「'+k+'」','【'+k+'】','《'+k+'》','支线·'+k,'任务'+k].some(x=>text.includes(x));
  return text.includes(k);
}

function previousContext(markup) {
  const story = [], active = [], options = [];
  function walk(n, parent = '') {
    const tag = n.tagName;
    if (tag === 'o') {
      const plain = el => el.nodeName === '#text' ? el.value : (el.childNodes || []).map(plain).join('');
      options.push(plain(n));
      return;
    }
    if (['script','style','template','textarea','code','pre','fbar'].includes(tag)) return;
    const cls = n.attrs?.find(a=>a.name==='class')?.value || '';
    if (tag==='details' && cls==='k') {
      // Only published active storyline names are carried from this panel.
      const gather = el => el.nodeName==='#text' ? el.value : el.tagName==='br' ? '\n' : (el.childNodes||[]).map(gather).join('');
      const list = gather(n).split(/【ACTIVE_STORYLINES】/u)[1]?.split(/【COMPLETED_STORYLINES】/u)[0] || '';
      for (const line of list.split('\n')) if (line.includes('|')) active.push(line.split('|')[0]);
    }
    if (tag==='details' && cls!=='w') return;
    if (parent==='map' && !(tag==='pos' || (tag==='i' && cls==='g'))) {
      // Only the current chapter and location are actionable in the navigation panel.
      if (n.nodeName!=='#text') for (const c of n.childNodes || []) walk(c,'map');
      return;
    }
    if (n.nodeName==='#text') { (parent==='active' ? active : story).push(n.value); return; }
    const group = tag==='details' ? 'map' : parent==='map' ? 'active' : parent;
    for (const c of n.childNodes || []) walk(c,group);
  }
  walk(parseFragment(markup));
  const requested = [...markup.trimEnd().matchAll(/(?:^|\n)〈调用：([^〈〉\r\n]{1,120})〉(?=\s*(?:〈调用：|$))/gu)].map(m=>m[1]);
  return {options,story:norm(story.join(' ')),active:norm(active.join(' ')),requested:new Set(requested)};
}

// A collapse of a defensive layer is a combat event. Consume only that
// occurrence so a character can still break down emotionally in the same line.
const defenseCollapse = /(?:护层|护盾|护罩|屏障|韧性层|装甲|护甲)(?:的)?(?:在[^，。；！？\n]{1,12}(?:下|后))?(?:已(?:经)?|开始|逐渐|瞬间|猛然|轰然|彻底|完全|随之|终于|濒临|即将|直接|正在|被击中后)*崩溃/gu;
const brokenGuard = /(?:敌人|对手|敌方|怪物|虚卒)(?:陷入|进入|已经|仍在|处于)(?:破防|失衡|僵直)|(?:韧性层?|防御姿态)(?:已|被|彻底|完全)*(?:击穿|击碎|打破|瓦解)/u;
const resolvedEvents = [
  /(?:战斗|交战|战事)(?:已经?|终于|就此|彻底|正式|暂时)*(?:结束|平息|告一段落)/u,
  /(?:最后一(?:名|个|只)敌人|敌人全部|敌军全体)(?:已经?|被|遭|悉数)*(?:击败|击退|消灭|倒下|撤退)/u,
  /(?:敌人|敌军)(?:已经?|全部|悉数|尽数)*(?:撤离|全灭)/u,
  /警报(?:已经?|终于)?解除/u,
  /(?:没有|未发现|未检测到)(?:后续|新的|其他)(?:军团信号|敌对信号|敌军信号|敌人踪迹)/u,
];
const combatResolution = new RegExp(
  '(?<!如果|要是|一旦|等到|假如|等)(?:' + resolvedEvents.map(event=>event.source).join('|') + ')(?!了?[吗么？?])', 'u'
);
// After a resolution, references to the battle just fought are common in
// dialogue and cleanup. Limit this filter to the preceding narrative.
const aftermathReferences = /(?:打扫|清理|收拾|清扫)(?:了下?|完|这片|眼前的)?战场|(?:刚才|刚刚|先前|刚结束|方才)(?:的|那场|的那场|那次)(?:战斗|交战|攻击|对决|搏杀)/gu;

// Resolve the current request before budgeting lore. User actions, the last
// narrative and the chosen option are evidence; unchosen menu items are not.
function sceneRules(current, previous, firstTurn) {
  const choice = current.match(/^(?:我?选(?:择|项)?\s*)?([a-g])(?:项)?[。.!！]?\s*$/u);
  const option = choice && previous.options.find(text =>
    norm(text).trimStart().startsWith('选项' + choice[1]));
  // Read in scene order. Earlier hits (including map labels) cease to
  // describe a live fight after a resolution. A later attack can start one.
  const action = [option && norm(option), current].filter(Boolean).join('\n');
  const scene = [previous.active, previous.story, action].join('\n');
  const historyPhases = previous.story.split(combatResolution);
  const actionPhases = action.split(combatResolution);
  const resolved = historyPhases.length > 1 || actionPhases.length > 1;
  let combat = scene;
  if (actionPhases.length > 1) combat = actionPhases.at(-1);
  else if (resolved) combat = historyPhases.at(-1).replace(aftermathReferences, '') + '\n' + action;
  const emotion = scene.replace(defenseCollapse, '');
  const chosen = new Set();
  if (combat.search(defenseCollapse) >= 0 || brokenGuard.test(combat)) chosen.add('战斗机制');
  for (const entry of rules) {
    let evidence = scene;
    if (entry.title === '引擎03执行') evidence = emotion;
    if (entry.title === '战斗机制' || entry.title === '引擎02执行') evidence = combat;
    if (previous.requested.has(entry.title) || entry.keys.some(key=>evidence.includes(norm(key))))
      chosen.add(entry.title);
  }
  if (firstTurn) chosen.add('文风正反例');
  // When no scene can be identified, make all six actionable rule fragments
  // available to the model in this request. Clear everyday scenes need no fallback.
  const everyday = /日常|闲聊|喝茶|散步|休息|看看窗外|早餐|晚餐|整理行李/u;
  const continuing = /^(?:继续|接着|然后呢|下一步|嗯|……)[。.!！?？…]*$/u.test(current);
  const quiet = everyday.test(current) || (option && everyday.test(option)) ||
    (continuing && everyday.test(previous.story));
  if (!resolved && !quiet && !rules.some(e=>e.title!=='文风正反例' && chosen.has(e.title))) {
    for (const entry of rules) if (entry.title !== '文风正反例') chosen.add(entry.title);
  }
  // Duel wording and mechanical combat cues must each supply both fragments.
  if (chosen.has('引擎02执行') || chosen.has('战斗机制')) {
    chosen.add('战斗机制');
    chosen.add('引擎02执行');
  }
  return chosen;
}

export function selectEntries(history, input) {
  const last = history.findLast(m=>m.role==='assistant');
  const prev = previousContext(last?.content || '');
  const current = norm(input.trim());
  const registration = input.includes('【游玩档案】') && !/◆\s*开局章节[：:]/u.test(input);
  const opening = registration ? input.match(/◆\s*开局[：:]\s*([^◆\r\n]+)/u)?.[1].trim() : '';
  const requiredRules = sceneRules(current, prev, !last);
  const candidates = [];
  const roll = createHash('sha256').update(JSON.stringify([history.length,last?.content || '',input])).digest();
  for (const entry of book) {
    let priority;
    if (entry.mode==='rule') {
      if (!requiredRules.has(entry.title)) continue;
      priority=0;
    } else if (entry.mode==='always') priority=0;
    else if (prev.requested.has(entry.title) || input.includes('〈调用：'+entry.title+'〉')) priority=1;
    else if (opening && opening===entry.openingPlace) priority=1;
    else if (entry.keys.some(k=>matches(current,k,entry))) priority=2;
    else if (entry.mode==='event') {
      const index=entry.slot==='文风'?0:1;
      if (roll[index]>=25 || (entry.slot==='直播' && /下播|关直播|未开播/.test(input + (last?.content.match(/<zh>[^<]*<\/zh>/)?.[0]||'')))) continue;
      priority=5;
    } else if (entry.keys.some(k=>matches(prev.active,k,entry))) priority=3;
    else if (entry.keys.some(k=>matches(prev.story,k,entry))) priority=4;
    else continue;
    candidates.push({entry,priority});
  }
  candidates.sort((a,b)=>a.priority-b.priority || a.entry.start-b.entry.start);
  let remaining=24000;
  const chosen = new Set();
  for (const {entry} of candidates) {
    if (entry.mode!=='always') {
      if (entry.body.length>remaining) continue;
      remaining-=entry.body.length;
    }
    chosen.add(entry);
  }
  return book.filter(e=>chosen.has(e));
}

function renderSystem(system, selected) {
  // Historical slots leave their separator in place. New rule slots represent
  // entire source lines and disappear with their newline when not selected.
  return system.replace(/⟦([^⟧\n]+)⟧(\n?)/gu, (marker, slot, newline) => {
    const bodies = selected.filter(entry=>entry.slot===slot).map(entry=>entry.body);
    if (ruleSlots.has(slot) && !bodies.length) return '';
    return bodies.join('\n') + newline;
  });
}

export function fixedPrompt() {
  return renderSystem(template.replace('⟦索引⟧', ''), book.filter(entry=>entry.mode==='always')).slice(0,-1);
}

export function assembleTurn(system, history, input) {
  const content = renderSystem(system,selectEntries(history,input));
  return {current:input,messages:[{role:'system',content},...history,{role:'user',content:input}]};
}
