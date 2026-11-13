import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const base=new URL('./',import.meta.url);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export async function render(){
  let html=await readFile(new URL('resources/original.html',base),'utf8');
  const manifest=JSON.parse(await readFile(new URL('resources/MANIFEST.json',base),'utf8'));
  if(sha(html)!==manifest.frontend.sha256)throw Error('SOURCE_HTML_CHANGED');
  const replace=(from,to)=>{if(!html.includes(from))throw Error('ADAPTER_ANCHOR_MISSING: '+from.slice(0,80));html=html.replace(from,to);};
  const assets=JSON.parse(await readFile(new URL('resources/ASSETS.json',base),'utf8'));
  let fonts=await readFile(new URL('resources/fonts.css',base),'utf8');
  // Identical variable-font faces at several weights can share one declaration.
  const faces=new Map();
  for(const block of fonts.matchAll(/@font-face\s*\{[^}]+\}/g)){
    const key=block[0].replace(/font-weight:[^;]+;/,'');
    const weight=Number(block[0].match(/font-weight:\s*(\d+)/)?.[1]||400);
    const entry=faces.get(key)||{block:block[0],weights:[]};entry.weights.push(weight);faces.set(key,entry);
  }
  fonts=[...faces.values()].map(({block,weights})=>block.replace(/font-weight:[^;]+;/,'font-weight: '+Math.min(...weights)+(weights.length>1?' '+Math.max(...weights):'')+';')).join('\n');
  for(const asset of assets){
    const bytes=await readFile(new URL('public/'+asset.file,base));
    if(sha(bytes)!==asset.sha256)throw Error('SOURCE_ASSET_CHANGED: '+asset.file);
    const data='data:'+asset.mediaType+';base64,'+bytes.toString('base64');
    if(asset.mediaType.startsWith('font/'))fonts=fonts.replaceAll(asset.url,data);
    else replace(asset.url,data);
  }
  html=html.replace(/^@import url\([^\n]+\);$/m,fonts);
  // Escape only player-authored form values when rendering them as HTML.
  replace('function renderCustomAb(){',`function escapeCustom(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}\nfunction renderCustomAb(){`);
  html=html.replaceAll("'+a.name+'","'+escapeCustom(a.name)+'").replaceAll("'+a.desc+'","'+escapeCustom(a.desc)+'");
  replace('<div class="opening-screen" id="opening">','<div class="opening-screen" id="opening" role="button" tabindex="0" aria-label="开启来信，再次点击可跳过动画">');
  replace('</style>',`\n[hidden]{display:none!important;}
:focus-visible{outline:2px solid var(--crimson);outline-offset:3px;}
.preview-actions{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important;}.opening-bg{background-image:none!important;}}
</style>`);
  replace('<button class="copy-btn" onclick="copyPreview()">复 · 制 · 此 · 生</button>','<div class="preview-actions"><button class="copy-btn" onclick="copyPreview()">复 · 制 · 此 · 生</button><button class="copy-btn" id="open-chat" hidden>带 入 聊 天 →</button></div>');
  replace('</body>',`<script>
const openingButton=document.getElementById('opening');
openingButton.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openingButton.click();}});
if(matchMedia('(prefers-reduced-motion: reduce)').matches){openingButton.style.display='none';document.getElementById('main').classList.add('active');}
// Dynamically rendered disclosures retain the original click behavior and appearance.
function bindAccessibility(){
  const selectors='.chapter-card-head,.canon-group-head,.ability-cat-head,.picker-head,.entry-item,.canon-item,.picker-item,.wand-del';
  document.querySelectorAll(selectors).forEach(el=>{
    if(el.dataset.keyboardBound)return;el.dataset.keyboardBound='1';el.tabIndex=0;el.setAttribute('role','button');
    el.addEventListener('keydown',e=>{if(e.target===el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();el.click();}});
  });
  document.querySelectorAll('input,select,textarea').forEach(el=>{
    if(el.labels?.length||el.hasAttribute('aria-labelledby'))return;
    const fieldLabel=el.closest('.form-group')?.querySelector('label');
    if(fieldLabel){if(!fieldLabel.id)fieldLabel.id='hp-field-label-'+Array.from(document.querySelectorAll('label')).indexOf(fieldLabel);el.setAttribute('aria-labelledby',fieldLabel.id);return;}
    if(el.hasAttribute('aria-label'))return;
    const label=el.closest('.ability-item,.talent-item')?.querySelector('.abname,.talent-name')?.textContent;
    el.setAttribute('aria-label',label||el.getAttribute('placeholder')||el.id||'选项');
  });
}
window.addEventListener('DOMContentLoaded',bindAccessibility);
document.getElementById('entry-reason').setAttribute('aria-label','入局契机');
new MutationObserver(bindAccessibility).observe(document.getElementById('main'),{childList:true,subtree:true});
const chatButton=document.getElementById('open-chat');
if(new URLSearchParams(location.search).get('chat')==='1'&&parent!==window){
  chatButton.hidden=false;
  chatButton.addEventListener('click',()=>{generatePreview();parent.postMessage({type:'harry-potter-opening',text:document.getElementById('preview-box').textContent},new URL(location.href).origin);});
}
</script></body>`);
  return html;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const html=await render(),output=new URL('public/index.html',base);
  if(process.argv.includes('--check')){if(await readFile(output,'utf8')!==html)throw Error('GENERATED_HTML_STALE');}
  else await writeFile(output,html);
}
