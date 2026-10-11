// Author-visible setup data. No prompt, provider or platform-memory policy lives here.
export const fields = ['姓名','性别','年龄','身份','外貌','性格','关系网','背景经历'];
export const features = [
 ['总结','📜','会在底部生成剧情的发展进程，也就是记忆区，可以编辑修改剧情进程，也可以把重要部分复制粘贴到自定义配置里。'],
 ['选项','🎲','会在底部生成六个选项供你选择，属于懒人模式。'],
 ['直播','📺','开启后会在底部生成直播间滚动弹幕，弹幕来自高维度观众的调侃，不影响剧情发展。'],
 ['画面特写','🔍','开启后，每次回复都会用微观特写镜头描写具体的部位、行为或事物。'],
 ['美化','✨','开启后状态栏 UI 会更美观，但消耗会略微增加，按自己的喜好选择。'],
 ['小说模式','📖','开启后 AI 会以网络小说的方式创作，更多描写 NPC 和配角的戏份，但可能会代替玩家做出反应和行动，请谨慎开启。'],
 ['剧情辅助','🧭','想不出剧情该怎么发展时，这个功能会在底部生成剧情发展建议。'],
 ['雌小鬼吐槽','😈','开启后会在底部生成一个由 AI 扮演的雌小鬼，用阴阳怪气的语调吐槽剧情、嘲讽用户。如果接受不了被 AI 嘲讽，千万不要开启。'],
 ['幕后采访','🎤','开启后会在底部生成一段主持人对角色的幕后采访，角色会脱离剧情，以演员的身份评价、调侃、分析自己在故事中扮演的角色。']
];
export const featureOrder = ['总结','选项','直播','人称视角','画面特写','美化','小说模式','剧情辅助','回复长度','雌小鬼吐槽','幕后采访'];
export const persons = ['第一人称','第二人称','第三人称'];
export const lengths = ['简短','中等','冗长'];
export const eras = [
 ['🏺','商朝','约公元前1600年-前1046年'],['🗡️','秦朝','公元前221年-前207年'],
 ['🏮','汉朝','公元前202年-公元220年'],['⚔️','三国','公元220年-280年'],
 ['🌸','唐朝','公元618年-907年'],['🖌️','宋朝','公元960年-1279年'],
 ['🏯','明朝','公元1368年-1644年'],['🪭','清朝','公元1636年-1912年'],
 ['🎩','民国','公元1912年-1949年'],['🏙️','现代都市',''],['🚀','未来世界',''],
 ['🧟','丧尸世界',''],['☯️','修仙世界',''],['🔮','魔法世界',''],['🐉','西幻世界',''],['👁️','惊悚世界','']
];
export const powers = [
 ['☄️','制造灾难','饥荒 地震 陨石'],['🌈','恩赐福泽','降雨 长生 超能力'],['🦖','召唤生物','恐龙 巨人 外星人'],
 ['🐙','降临诡秘','鬼怪 丧尸 克苏鲁'],['🎭','制造副本','规则怪谈 惊悚游戏'],['⚖️','制定规则','天条 法则 禁令'],
 ['⏳','篡改历史','穿越 科技 改命'],['⚔️','爆发战争','神兵 天降 征伐'],['🌀','连接位面','修仙 魔法 同人'],
 ['⏰','操控时间','暂停 倒流 加速'],['💭','编织梦境','托梦 噩梦 清醒梦'],['⚡','降下神罚','天雷 审判 净化'],
 ['🧠','赐予智慧','顿悟 灵感 启示'],['💀','唤醒死者','复活 亡灵 通灵'],['🌌','扭曲空间','折叠 传送 迷宫'],
 ['🌊','操控海洋','海啸 退潮 深渊'],['🔥','天降神火','焚世 净化 凤凰'],['🕳️','抹除存在','遗忘 消失 虚无']
];
export function blankCharacter() { return Object.fromEntries(fields.map(k=>[k,''])); }
export function defaultFeatures() { return Object.fromEntries(features.map(([k])=>[k,k==='总结'])); }
export function newSetup() {
 return {version:1,user:blankCharacter(),others:[blankCharacter()],era:'',eraExtra:'',opening:'',features:defaultFeatures(),person:'第二人称',length:'中等',omitEmpty:false};
}
export function featureValue(s,k) {
 return k==='人称视角'?s.person:k==='回复长度'?s.length:s.features[k]?'开启':'关闭';
}
export function cycleFeature(s,k) {
 if(k==='人称视角') s.person=persons[(persons.indexOf(s.person)+1)%persons.length];
 else if(k==='回复长度') s.length=lengths[(lengths.indexOf(s.length)+1)%lengths.length];
 else if(features.some(([name])=>name===k)) s.features[k]=!s.features[k];
 else throw new Error('Unknown feature');
}
// Display placeholders are not copied into the initial user message.
export function setupBlocks(s, display=false) {
 const blocks=[];
 const addCharacter=(name,c)=>{
  const rows=fields.filter(k=>!s.omitEmpty||c[k].trim()).map(k=>({name:k,value:c[k]||(display?'未填写':'')}));
  if(rows.length) blocks.push({name,rows});
 };
 addCharacter('用户角色',s.user);
 s.others.forEach((c,i)=>addCharacter('其他角色·'+(i+1),c));
 const selectedEra=eras.find(e=>e[1]===s.era);
 const eraText=selectedEra?.[2]?s.era+'（'+selectedEra[2]+'）':s.era;
 const era=[eraText,s.eraExtra].filter(Boolean).join('；');
 if(!s.omitEmpty||era) blocks.push({name:'时代背景',rows:[{name:'时代背景',value:era||(display?'未填写':'')}]});
 if(!s.omitEmpty||s.opening) blocks.push({name:'开场白',rows:[{value:s.opening||(display?'未填写':'')}]});
 blocks.push({name:'功能区域',rows:featureOrder.map(k=>({name:k,value:featureValue(s,k),feature:true}))});
 return blocks;
}
export function serializeSetup(s) {
 return setupBlocks(s).map(b=>'【'+b.name+'】\n'+b.rows.map(r=>(r.name?r.name+'：':'')+r.value).join('\n')).join('\n\n');
}
// Restore only known fields; malformed drafts are preserved by the UI, never silently overwritten.
export function restoreSetup(raw) {
 const s=JSON.parse(raw);
 const character=c=>c&&fields.every(k=>typeof c[k]==='string')&&['','男','女'].includes(c.性别);
 if(s?.version!==1||!character(s.user)||!Array.isArray(s.others)||!s.others.every(character)||
 !['',...eras.map(e=>e[1])].includes(s.era)||typeof s.eraExtra!=='string'||typeof s.opening!=='string'||
 !persons.includes(s.person)||!lengths.includes(s.length)||typeof s.omitEmpty!=='boolean'||
 !features.every(([k])=>typeof s.features?.[k]==='boolean')) throw new Error('草稿格式无法识别');
 const clean=newSetup();
 for(const k of ['era','eraExtra','opening','person','length','omitEmpty']) clean[k]=s[k];
 clean.user=Object.fromEntries(fields.map(k=>[k,s.user[k]]));
 clean.others=s.others.map(c=>Object.fromEntries(fields.map(k=>[k,c[k]])));
 clean.features=Object.fromEntries(features.map(([k])=>[k,s.features[k]]));
 return clean;
}
