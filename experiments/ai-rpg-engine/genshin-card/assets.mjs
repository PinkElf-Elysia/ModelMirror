import {readFile} from 'node:fs/promises';
const prefix = '/rpg-app/genshin/';
const local = path => new URL('./' + path, import.meta.url);
const setupAssets = JSON.parse(await readFile(local('resources/assets.json')));
const portraits = JSON.parse(await readFile(local('resources/portraits.json')));
const assets = [...setupAssets, ...portraits.filter(a=>a.file), {file:'story.css',mediaType:'text/css; charset=utf-8'}];
const source = await readFile(local('resources/opening.html'), 'utf8');

export function renderSetup() {
  let html = source;
  for (const asset of setupAssets) {
    // Preserve the original shareable preset URL in copied avatar CSS.
    if (!asset.file.startsWith('avatar-')) html = html.replaceAll(asset.url, prefix + asset.file);
  }
  const presetImages = Object.fromEntries(setupAssets.filter(a=>a.file.startsWith('avatar-')).map(a=>[a.url,prefix+a.file]));
  return html.replace('img.src = url;', 'img.src = presetImages[url];').replace('const presetAvatars =', 'const presetImages = '+JSON.stringify(presetImages)+';\nconst presetAvatars =')
    .replace('</style>', '#transfer{font:inherit;font-weight:bold;padding:.7em 1.2em;margin:.75em;border:3px solid var(--border-color);border-radius:8px;background:var(--primary-color);color:#111;cursor:pointer}</style>')
    .replace('📋 一键复制角色卡设定</button>', '📋 一键复制角色卡设定</button><button type="button" hidden id="transfer">带入聊天</button>')
    .replace("const copyBtn =", "const transfer=document.getElementById('transfer');\n"
      +"transfer.hidden=parent===window || new URLSearchParams(location.search).get('chat')!=='1';\n"
      +"transfer.addEventListener('click',()=>{updatePreview();const text=document.getElementById('preview-area').textContent;"
      +"if(text.length>20000)return showToast('设定超过 20,000 字，请精简后再带入聊天。');"
      +"parent.postMessage({type:'genshin-opening',text},new URL(location.href).origin);});\nconst copyBtn =");
}

export async function genshinAsset(req,res,origins) {
  if (req.method!=='GET') return false;
  const path=new URL(req.url,'http://local').pathname;
  const asset=assets.find(a=>path===prefix+a.file);
  if (asset) {
    res.setHeader('Cross-Origin-Resource-Policy','cross-origin');
    res.writeHead(200,{'Content-Type':asset.mediaType});
    res.end(await readFile(local('public/'+asset.file)));
    return true;
  }
  if(path!==prefix) return false;
  const allowed=origins.map(o=>new URL(o).origin).join(' ');
  res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src "+allowed+"; font-src https://cdn.jsdelivr.net/npm/zpix-pixel-font@3.1.2/dist/zpix.woff2; form-action 'none'; base-uri 'none'; frame-ancestors 'self'");
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
  res.end(renderSetup());
  return true;
}
