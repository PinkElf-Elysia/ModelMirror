// Shared browser/host metadata policy. Original-site observations and local rules: B2-B.md.
export const LANGUAGES = Object.freeze([
  ['en', 'English'], ['zh-CN', '简体中文'], ['zh-TW', '繁體中文'], ['ja', '日本語'], ['ko', '한국어'],
].map(Object.freeze));
export const AUDIENCES = Object.freeze([['male', '男性向'], ['female', '女性向']].map(Object.freeze));
export const TAG_LIMIT = 8;
export const TAG_CHAR_LIMIT = 80;
export const TAG_SEEDS = Object.freeze([
  '多角色', '自定义', '校园', '武侠', '奇幻', '科幻', '日常', '历史', '冒险', '悬疑',
  '玄幻', '种田', '现代', '都市', '古代', '推理', '经营', '探索', '成长', '纯爱', '百合', '群像',
  '互动故事', '现代幻想', '星际科幻', '大世界', '轻松', '治愈', '生存', '海洋',
]);
// Deliberately bounded local filtering, not a semantic moderation or model call.
const RESTRICTED = Object.freeze([
  '乱伦', '亂倫', '近亲交配', '近親交配', 'incest', '色情', '情色', '性爱', '性愛', '性交',
  '性奴', '性虐', '性癖', '性瘾', '性癮', '口交', '肛交', '乳交', '内射', '內射',
  '调教', '調教', '催眠调', '催眠調', '强奸', '強姦', '轮奸', '輪姦', '迷奸', '兽交', '獸交',
  '恋童', '戀童', '幼女', '幼男', '萝莉', '蘿莉', '正太', '兽娘发情', '發情', '发情',
  '乳房', '阴道', '陰道', '阴茎', '陰莖', '肉文', '黄文', '黃文', '成人向', '成人内容',
  '成人內容', '涩涩', '澀澀', '色色', '开局即可色', 'nsfw', 'r18', '18禁', '成人色情',
  'porn', 'hentai', 'sex', 'erotic', 'fetish', 'bdsm', 'loli', 'shota', 'ntr',
]);
export const tagKey = value => value.normalize('NFKC').toLowerCase().replace(/\s/gu, '');
export function restrictedTag(value) {
  const words = value.normalize('NFKC').toLowerCase().normalize('NFKD').replace(/[\p{M}\p{Cf}]/gu, '');
  const key = tagKey(words).replace(/[\p{P}\p{S}]/gu, '');
  return RESTRICTED.some(term => {
    if (!/^[a-z0-9]+$/.test(term)) return key.includes(term);
    const matcher = new RegExp('(?:^|[^a-z])' + term + '(?:$|[^a-z])','u');
    return matcher.test(words) || matcher.test(key);
  });
}
export function tagIssue(value) {
  if (typeof value !== 'string' || !value.isWellFormed() || !value.trim() || [...value].length > TAG_CHAR_LIMIT) return 'AUTHORING_TAG_TEXT';
  if (restrictedTag(value)) return 'AUTHORING_TAG_RESTRICTED';
  if (value !== value.trim() || !/^[\p{L}\p{M}\p{N} ]+$/u.test(value)) return 'AUTHORING_TAG_CHARACTERS';
  return null;
}
export function tagCatalog(values = []) {
  const seen = new Set();
  return [...TAG_SEEDS, ...values].filter(value => {
    if (tagIssue(value)) return false;
    const key = tagKey(value);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}
export function metadataIssues(metadata, {publication = false} = {}) {
  const issues = [], add = (code, path) => issues.push({code, path:'/metadata/' + path});
  if (!LANGUAGES.some(([value]) => value === metadata.language)) add('AUTHORING_LANGUAGE_INVALID', 'language');
  if (metadata.audience === '') { if (publication) add('AUTHORING_AUDIENCE_REQUIRED', 'audience'); }
  else if (!AUDIENCES.some(([value]) => value === metadata.audience)) add('AUTHORING_AUDIENCE_INVALID', 'audience');
  if (metadata.tags.length > TAG_LIMIT) add('AUTHORING_TAG_LIMIT', 'tags');
  const seen = new Set();
  metadata.tags.forEach((tag, index) => {
    const issue = tagIssue(tag);
    if (issue) add(issue, 'tags/' + index);
    else {
      const key = tagKey(tag);
      if (seen.has(key)) add('AUTHORING_TAG_DUPLICATE', 'tags/' + index);
      seen.add(key);
    }
  });
  return issues;
}
export const METADATA_MESSAGES = Object.freeze({
  AUTHORING_LANGUAGE_INVALID:'请选择五种支持语言之一；旧值已保留，没有自动映射。',
  AUTHORING_AUDIENCE_REQUIRED:'请选择作品面向群体：男性向或女性向。',
  AUTHORING_AUDIENCE_INVALID:'旧受众分类无法对应男性向或女性向，请重新选择；原值未改写。',
  AUTHORING_TAG_LIMIT:'最多只能选择 8 个标签，请移除多余标签。',
  AUTHORING_TAG_TEXT:'标签不能为空，最多 80 个 Unicode 字符；建议控制在 10 字以内。',
  AUTHORING_TAG_CHARACTERS:'标签只接受文字、数字和空格，不接受表情、控制字符或标点。',
  AUTHORING_TAG_RESTRICTED:'此标签不适用于本地创作标签库，请使用合适的题材或玩法分类。',
  AUTHORING_TAG_DUPLICATE:'标签已存在，请使用现有标签。',
});
