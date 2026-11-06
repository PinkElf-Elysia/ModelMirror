import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const base=new URL('./',import.meta.url);
export async function render(){
 let html=await readFile(new URL('resources/original.html',base),'utf8');
 const manifest=JSON.parse(await readFile(new URL('resources/MANIFEST.json',base),'utf8'));
 if(createHash('sha256').update(html).digest('hex')!==manifest.frontend.sha256)throw new Error('SOURCE_HTML_CHANGED');
 const replace=(before,after)=>{if(!html.includes(before))throw new Error('ADAPTER_ANCHOR_MISSING: '+before.slice(0,60));html=html.replace(before,after);};
 const assets=JSON.parse(await readFile(new URL('resources/ASSETS.json',base),'utf8'));
 for(const asset of assets){
  const bytes=await readFile(new URL('public/'+asset.file,base));
  if(createHash('sha256').update(bytes).digest('hex')!==asset.sha256)throw new Error('SOURCE_ASSET_CHANGED');
  // Opaque-origin sandbox documents cannot use CSP 'self' for image fetches.
  // Inline the exact artwork so the sandbox remains origin-isolated and offline.
  replace(asset.url,'data:'+asset.mediaType+';base64,'+bytes.toString('base64'));
 }
 html=html.replace(/^@import url\([^\n]+\);$/m,'/* System fonts keep the card usable offline. */');
 replace("${appState.canonRole||", "${canonRole||");
 replace("${appState.canonRole&&", "${canonRole&&");
 replace("el.addEventListener('click',e=>{\n      if(e.target!==cb) cb.checked=!cb.checked;", "cb.addEventListener('change',()=>{");
 replace('function renderCustom(type){', `function escapeCustom(value){return String(value).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function renderCustom(type){`);
 replace('value="${s.name}"','value="${escapeCustom(s.name)}"');
 replace('>${s.desc}</textarea>','>${escapeCustom(s.desc)}</textarea>');
 replace('<input type="checkbox" id="dst-${i}">','<input type="checkbox" id="dst-${i}" aria-label="${escapeCustom(d.name)}">');
 replace('<div class="opening-screen" id="opening">','<div class="opening-screen" id="opening" role="button" tabindex="0" aria-label="睁开双眼，再次激活可跳过动画">');
 replace('</style>',`
[hidden]{display:none!important;}
:focus-visible{outline:2px solid #a57e41;outline-offset:4px;}
.preview-actions{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:0.01ms!important;transition-duration:0.01ms!important;scroll-behavior:auto!important;}}
</style>`);
 replace('<button class="copy-btn" onclick="copyPreview()">复 制 档 案</button>',`<div class="preview-actions"><button class="copy-btn" onclick="copyPreview()">复 制 档 案</button><button class="copy-btn" id="open-chat" hidden>带 入 聊 天 →</button></div>`);
 replace('</body>',`<script>
 const openingButton=document.getElementById('opening');
 openingButton.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openingButton.click();}});
 if(matchMedia('(prefers-reduced-motion: reduce)').matches)__finishOpening();
 document.querySelectorAll('.arsenal-header').forEach(header=>{header.tabIndex=0;header.setAttribute('role','button');header.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();header.click();}});});
 document.querySelectorAll('input,select,textarea').forEach(input=>{if(!input.labels?.length&&!input.getAttribute('aria-label'))input.setAttribute('aria-label',input.getAttribute('placeholder')||input.id||'选项');});
 const chatButton=document.getElementById('open-chat');
 if(new URLSearchParams(location.search).get('chat')==='1'&&parent!==window){
   chatButton.hidden=false;
   chatButton.addEventListener('click',()=>{generatePreview();parent.postMessage({type:'dragon-raja-opening',text:document.getElementById('preview-box').textContent},new URL(location.href).origin);});
 }
 </script></body>`);
 return html;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 const html=await render(),output=new URL('public/index.html',base);
 if(process.argv.includes('--check')){if(await readFile(output,'utf8')!==html)throw new Error('GENERATED_HTML_STALE');}
 else await writeFile(output,html);
}
