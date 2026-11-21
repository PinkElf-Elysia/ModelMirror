import atlas from '../../type-moon-card/resources/dialogue-atlas.json' with {type:'json'};
// Only these exact names, roles and continuities refer to the archived portraits.
const definitions = [
  ['01','两仪式', '', '空之境界|KNK'],
  ['02','苍崎青子', '', '魔法使之夜|MAHOYO|TSUKI-R'],
  ['03','阿尔托莉雅|阿尔托莉雅·潘德拉贡','Saber','FSN|FZ|FGO'],
  ['04','久远寺有珠','','魔法使之夜|MAHOYO'],
  ['05','远坂凛','','FSN'],['06','爱尔奎特|爱尔奎特·布伦史塔德','','TSUKI|TSUKI-R'],
  ['07','间桐樱','','FSN'],['08','伊莉雅丝菲尔|伊莉雅|伊莉雅丝菲尔·冯·爱因兹贝伦','','FSN'],
  ['09','爱丽丝菲尔|爱丽丝菲尔·冯·爱因兹贝伦','','FZ'],
  ['10','贞德','Ruler','FA|FGO'],['11','莫德雷德','Saber','FA|FGO'],
  ['12','尼禄|尼禄·克劳狄乌斯','Saber','EXTRA|CCC|FGO'],
  ['13','玉藻前','Caster','EXTRA|CCC|FGO'],
  ['14','岸波白野（女性）|岸波白野（女）','','EXTRA|CCC'],
  ['15','BB','MoonCancer','CCC|FGO'],
  ['16','格蕾','','CASE|事件簿'],['17','莱妮丝|莱妮丝·埃尔梅罗·阿奇佐尔缇','','CASE|事件簿'],
  ['18','沙条绫香（Prototype）|沙条绫香','','Prototype|FP'],
  ['19','沙条爱歌','','Prototype|FP|苍银'],
  ['20','希耶尔','','TSUKI|TSUKI-R'],['21','远野秋叶','','TSUKI|TSUKI-R'],
  ['22','翡翠','','TSUKI|TSUKI-R'],['23','琥珀','','TSUKI|TSUKI-R'],
  ['24','希翁·艾尔特纳姆·阿特拉西亚|希翁','','MB'],
  ['25','玛修|玛修·基列莱特','Shielder','FGO'],
  ['26','奥尔加玛丽|奥尔加玛丽·阿尼姆斯菲亚','','FGO'],
  ['27','斯卡哈','Lancer','FGO'],
  ['28','阿尔托莉雅·卡斯特','Caster','FGO'],
  ['29','美游·艾德费尔特|美游','','PRILLYA|魔伊'],
  ['30','希波吕忒','Rider','FSF'],
];
const catalog = new Map();
const normalize = key => key.normalize('NFKC').trim().toLowerCase();
for (const [number,names,role,series] of definitions) {
  const path='/rpg-app/type-moon/portraits/npc-'+number+'.webp';
  for(const name of names.split('|')){
    // Bare ambiguous identities require an explicit work or form.
    if(!['沙条绫香','希翁'].includes(name))catalog.set(normalize(name),path);
    for(const work of series.split('|')){
      catalog.set(normalize(name+'-'+work),path);
      if(role)catalog.set(normalize(name+'-'+role+'-'+work),path);
    }
  }
}
catalog.set(normalize('阿尔托莉雅-Caster-FGO'),'/rpg-app/type-moon/portraits/npc-28.webp');
const emotions=['happy','angry','sad','laugh','neutral','confused'];
const dialogueRoot='/rpg-app/type-moon/';
const registrations=new Map(Object.entries(atlas.characters).map(([key,entry])=>[normalize(key),entry]));
for(const [alias,target] of Object.entries(atlas.aliases)){
  const destination=registrations.get(normalize(target));
  if(destination && !registrations.has(normalize(alias)))registrations.set(normalize(alias),destination);
}
export function typeMoonPortrait(key,emotion){
  if(typeof key!=='string'||key.length>150)return undefined;
  const id=normalize(key), entry=registrations.get(id);
  if(entry){
    if(emotion==='untrusted' && entry.untrusted)return {avatar:dialogueRoot+entry.untrusted.avatar,portrait:dialogueRoot+entry.untrusted.portrait,frame:4};
    if(entry.avatar && entry.portrait){
      const index=emotions.indexOf(emotion);
      return {avatar:dialogueRoot+entry.avatar,portrait:dialogueRoot+entry.portrait,frame:index<0?4:index};
    }
  }
  const url=catalog.get(id);
  return url?{avatar:url,portrait:url}:undefined;
}
