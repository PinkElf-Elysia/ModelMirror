import {keys} from '../../authoring/contracts.mjs';
import {check} from './contracts.mjs';

// A pure projection of a verified group view. Only the player's explicit input is scanned.
export function previewTurn(view, input) {
  keys(input, ['expectedRevision', 'mode', 'order', 'muted', 'text']);
  check(input.expectedRevision === view.revision, 'REVISION_CONFLICT');
  check(['fixed', 'mentions'].includes(input.mode) && typeof input.text === 'string' &&
    input.text.length <= 2000, 'TURN_RULE_INVALID');
  const cast = view.members.filter(member => member.kind === 'character');
  const byId = new Map(cast.map(member => [member.id, member]));
  for (const ids of [input.order, input.muted]) {
    check(Array.isArray(ids) && ids.length <= cast.length && ids.every(id => byId.has(id)) &&
      new Set(ids).size === ids.length, 'TURN_MEMBER_INVALID');
  }
  check(input.order.length === cast.length, 'TURN_MEMBER_INVALID');
  const diagnostics = new Set();
  const mentions = new Set();
  if (input.mode === 'mentions') {
    // Delimited tokens keep ordinary prose and email addresses out of the selection.
    for (const word of input.text.split(/[\s，。、！？；：,!?;:]+/u)) {
      if (!word.startsWith('@') || word === '@') continue;
      const token = word.slice(1);
      const ordinal = /^\[([1-9]\d*)\]$/.exec(token);
      const matches = ordinal ? cast.slice(Number(ordinal[1]) - 1, Number(ordinal[1])) :
        cast.filter(member => member.name === token);
      if (matches.length === 1) mentions.add(matches[0].id);
      else diagnostics.add(matches.length ? '同名提及请改用下方的成员编号：' + word : '未识别的提及：' + word);
    }
  }
  const ordered = input.mode === 'fixed' ? input.order : [...mentions, ...input.order.filter(id => !mentions.has(id))];
  const rows = ordered.map(id => {
    const member = byId.get(id);
    let reason = input.mode === 'fixed' ? '按固定顺序' : '玩家显式提及';
    let included = false;
    if (!member.active) reason = '已离场';
    else if (input.muted.includes(id)) reason = '已静音';
    else if (input.mode === 'mentions' && !mentions.has(id)) reason = '未被提及';
    else if (diagnostics.size) reason = '等待澄清提及';
    else included = true;
    return {id, included, reason};
  });
  return {groupId:view.id, revision:view.revision, rows, count:rows.filter(r => r.included).length,
    diagnostics:[...diagnostics]};
}
