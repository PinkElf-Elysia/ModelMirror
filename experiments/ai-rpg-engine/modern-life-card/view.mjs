import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('./resources/original.html', import.meta.url), 'utf8');
const googleFonts = /^@import url\('https:\/\/fonts\.googleapis\.com\/css2\?[^'\n]+'\);$/m;

// Keep the author interface as the source of truth. Fail on upstream markup drift.
function replaceOnce(html, anchor, replacement) {
  if (html.split(anchor).length !== 2) throw Error('MODERN_LIFE_INTERFACE_CHANGED');
  return html.replace(anchor, () => replacement);
}

function render() {
  const fonts = source.match(googleFonts)?.[0];
  if (!fonts) throw Error('MODERN_LIFE_FONTS_MISSING');
  let html = replaceOnce(source, fonts, "@import url('./card.css');");
  html = replaceOnce(html, '<title>现代真实人生 |', '<title>现代超真实人生 |');
  html = replaceOnce(html, '<h1>现代真实人生</h1>', '<h1>现代超真实人生</h1>');
  const copy = '<button class="copy-btn" id="copyButton"><span>复制完整开局配置</span></button>';
  html = replaceOnce(html, copy, copy + '\n<button type="button" class="step-btn primary" id="enterChat">以此开局进入聊天 →</button>');
  // Native confirm is disabled in an opaque sandbox. Keep confirmation inside the frame.
  html = replaceOnce(html, 'function deleteNpc(id){', 'async function deleteNpc(id){');
  html = replaceOnce(html, 'if(!window.confirm(', 'if(!await confirmNpcRemoval(');
  return replaceOnce(html, '</body>', '<dialog id="removeNpcDialog" aria-labelledby="removeNpcLabel"><p id="removeNpcLabel"></p><div class="dialog-actions"><button type="button" value="cancel" autofocus>取消</button><button type="button" value="delete">删除 NPC</button></div></dialog><script src="./bridge.js"></script></body>');
}

export const builderHtml = render();
