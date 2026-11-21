// Chinese reading labels for the permanent prompt. Matching keys belong to the worldbook.
const chineseNames = new Map([
  [141, ['贞德', '圣诞幼年反转']], [163, ['溶解莉莉丝']], [164, ['热情迷唇']],
  [168, ['第三兽之右']], [197, ['阿蒂拉', '圣诞']], [220, ['BB', '泳装']],
  [238, ['帝王花']], [240, ['第三兽之左']], [266, ['谜之他人格·Λ']],
  [333, ['第四兽']], [351, ['地球原型']], [424, ['岸波白野', '男性']],
  [426, ['藤蔓之滴']], [431, ['幻月']], [445, ['热情迷唇']], [457, ['伊丽莎白·巴托里', '五星']]
]);
const forms = new Map([
  ['Alter', '反转'], ['Lily', '幼年'], ['圣诞Alter', '圣诞反转'], ['Prototype', '原型'],
  ['女性', '女性'], ['夏', '泳装'], ['桑巴／圣诞', '桑巴圣诞']
]);

export function servantDisplayNames(cards, publishedNames) {
  const rows = cards.map(card => {
    const label = chineseNames.get(card.no);
    const name = publishedNames.get(card.no) || card.name;
    const base = name.replace(/\([^)]*\)$/u, '');
    const form = base.match(/〔([^〕]+)〕$/u)?.[1];
    const cls = card.classWords[1] || ({'Grand Caster':'冠位术士', Beast:'兽阶'})[card.className];
    if (label) return {base:label[0], form:label[1], cls};
    if (base !== name && /\(女性\)$/u.test(name)) return {base, form:'女性', cls};
    if (form && (forms.has(form) || /^(万圣节|新娘|圣诞|勇者|灰姑娘|矿工|暑假|Assassin|EXTRA)$/u.test(form))) {
      return {base:base.slice(0, -(form.length + 2)), form:['Assassin','EXTRA'].includes(form) ? undefined : forms.get(form) || form, cls};
    }
    return {base:base.replace(/〔Alter〕/gu, '〔反转〕'), cls};
  });
  const families = Map.groupBy(rows, row => row.base);
  return rows.map(row => {
    const family = families.get(row.base);
    if (family.length === 1) return row.base;
    if (row.form && family.filter(other => other.form === row.form).length === 1) return row.base+'（'+row.form+'）';
    const qualifier = row.form ? [row.cls, row.form] : [row.cls];
    if (!row.form && family.some(other => other !== row && other.cls === row.cls)) qualifier.push('常态');
    if (!qualifier[0]) throw Error('缺少同名英灵的中文职阶 '+row.base);
    return row.base+'（'+qualifier.join('·')+'）';
  });
}

