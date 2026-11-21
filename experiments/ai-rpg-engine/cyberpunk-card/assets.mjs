import {readFile} from 'node:fs/promises';

const root = new URL('./', import.meta.url);
const prefix = '/rpg-app/cyberpunk/';
const assets = JSON.parse(await readFile(new URL('resources/ASSETS.json', root), 'utf8'));
export const isCyberpunkAsset = path => assets.some(asset => path === prefix + asset.file);

export async function cyberpunkAsset(req, res, origins) {
  const path = new URL(req.url,'http://local').pathname;
  if (req.method !== 'GET' || (path !== prefix && !isCyberpunkAsset(path))) return false;
  const asset = assets.find(item => path === prefix + item.file);
  if (asset) {
    const bytes = await readFile(new URL('public/' + asset.file, root));
    res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
    res.writeHead(200, {'Content-Type': asset.mediaType, 'Content-Length': bytes.length});
    res.end(bytes);
    return true;
  }
  const sources = origins.map(origin => new URL(origin).origin).join(' ');
  res.setHeader('Content-Security-Policy', `sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src ${sources}; base-uri 'none'; form-action 'none'; frame-ancestors 'self'`);
  res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
  res.end(await renderSetup());
  return true;
}

export async function renderSetup() {
  const source = await readFile(new URL('resources/original.html', root), 'utf8');
  const background = assets.find(item => item.file === 'background.jpg');
  return source.replace(background.url, prefix + background.file)
    .replace('</style>', '#transfer[hidden]{display:none}</style>')
    .replace('<button class="btn btn-copy"', '<button hidden id="transfer" type="button" class="btn btn-primary">带入聊天 →</button>\n        <button class="btn btn-copy"')
    .replace("  goToTab(6);", "  document.getElementById('transfer').hidden = !(parent !== window && new URLSearchParams(location.search).get('chat') === '1');\n  goToTab(6);")
    .replace('</script>', `
document.getElementById('transfer').addEventListener('click', () => {
  const text = document.getElementById('previewBox').textContent;
  if (text.length > 20000) return showToast('角色卡超过 20,000 字，请精简后再带入聊天。');
  parent.postMessage({type: 'cyberpunk-opening', text}, new URL(location.href).origin);
});
</script>`);
}
