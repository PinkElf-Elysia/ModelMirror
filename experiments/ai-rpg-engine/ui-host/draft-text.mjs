// Reads only proposal.narrative from a bounded JSON prefix. No regex extraction,
// HTML evaluation, fallback to raw JSON, or exposure of other fields.
export function narrativeDraft(raw) {
  if (typeof raw !== 'string' || raw.length > 1048576) return '';
  let offset = 0, output = '';
  const incomplete = Symbol('incomplete'), invalid = Symbol('invalid');
  const ws = () => { while (/[\x20\t\r\n]/u.test(raw[offset] ?? '') && offset < raw.length) offset++; };
  function string(capture = false) {
    if (raw[offset++] !== '"') throw invalid;
    let text = '';
    const publish = () => { if (capture) output = text.replace(/[\uD800-\uDBFF]$/u, ''); };
    while (offset < raw.length) {
      const ch = raw[offset++];
      if (ch === '"') { publish(); return text; }
      if (ch.charCodeAt(0) < 32) throw invalid;
      if (ch !== '\\') text += ch;
      else {
        if (offset === raw.length) { publish(); throw incomplete; }
        const escaped = raw[offset++];
        if (escaped === 'u') {
          const hex = raw.slice(offset, offset + 4);
          if (!/^[a-f0-9]*$/iu.test(hex)) throw invalid;
          if (hex.length < 4) { publish(); throw incomplete; }
          text += String.fromCharCode(parseInt(hex, 16)); offset += 4;
        } else {
          const escapes = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
          if (!Object.hasOwn(escapes, escaped)) throw invalid;
          text += escapes[escaped];
        }
      }
    }
    publish(); throw incomplete;
  }
  function value(keys, depth = 0) {
    if (depth > 32) throw invalid;
    ws(); if (offset === raw.length) throw incomplete;
    if (raw[offset] === '"') return string(keys.length === 2 && keys[0] === 'proposal' && keys[1] === 'narrative');
    if (raw[offset] === '{') {
      offset++; ws(); const seen = new Set();
      if (raw[offset] === '}') { offset++; return; }
      while (true) {
        ws(); if (offset === raw.length) throw incomplete;
        const key = string(); if (seen.has(key)) throw invalid; seen.add(key);
        ws(); if (offset === raw.length) throw incomplete; if (raw[offset++] !== ':') throw invalid;
        value([...keys, key], depth + 1); ws(); if (offset === raw.length) throw incomplete;
        const next = raw[offset++]; if (next === '}') return; if (next !== ',') throw invalid;
      }
    }
    if (raw[offset] === '[') {
      offset++; ws(); if (raw[offset] === ']') { offset++; return; }
      while (true) {
        value([...keys, null], depth + 1); ws(); if (offset === raw.length) throw incomplete;
        const next = raw[offset++]; if (next === ']') return; if (next !== ',') throw invalid;
      }
    }
    const start = offset;
    while (offset < raw.length && !/[\x20\t\r\n,}\]]/u.test(raw[offset])) offset++;
    const token = raw.slice(start, offset);
    if (!token) throw invalid;
    try { const parsed = JSON.parse(token); if (parsed !== null && !['number', 'boolean'].includes(typeof parsed)) throw invalid; }
    catch { if (offset === raw.length && /^(?:t(?:r(?:u(?:e)?)?)?|f(?:a(?:l(?:s(?:e)?)?)?)?|n(?:u(?:l(?:l)?)?)?|-?\d+(?:\.\d*)?(?:[eE][+-]?\d*)?)$/u.test(token)) throw incomplete; throw invalid; }
  }
  try { ws(); if (raw[offset] !== '{') return ''; value([]); ws(); if (offset !== raw.length) return ''; }
  catch (cause) { if (cause !== incomplete) return ''; }
  return output;
}
