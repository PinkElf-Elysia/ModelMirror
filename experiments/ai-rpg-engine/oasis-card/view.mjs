import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('./resources/original.html', import.meta.url), 'utf8');
const fonts = `@font-face{font-family:Orbitron;font-weight:400 900;src:url('./fonts/orbitron.ttf') format('truetype');font-display:swap}
@font-face{font-family:'Noto Sans SC';font-weight:100 900;src:url('./fonts/noto-sans-sc.woff') format('woff');font-display:swap}`;

const transfer = `<button type="button" class="btn-copy" hidden id="enterChat">带入聊天 →</button>`;
const bridge = `
const enterChat = document.getElementById('enterChat');
if (parent !== window && new URLSearchParams(location.search).get('chat') === '1') {
  enterChat.hidden = false;
  enterChat.addEventListener('click', () => {
    updatePreview();
    parent.postMessage({type:'oasis-opening', text:previewBox.textContent}, new URL(location.href).origin);
  });
}
`;

export const builderHtml = source
  .replace(/^@import url\('[^']+'\);$/m, fonts)
  .replace('</style>', '#enterChat[hidden]{display:none}</style>')
  .replace(/(<button [^>]*id="copyBtn"[^>]*>[^<]*<\/button>)/, '$1' + transfer)
  .replace('</script>', bridge + '</script>');
