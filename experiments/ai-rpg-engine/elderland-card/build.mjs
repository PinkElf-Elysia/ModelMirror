import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const base=new URL('./',import.meta.url),hash=x=>createHash('sha256').update(x).digest('hex');
export async function render(){
 let html=await readFile(new URL('resources/original.html',base),'utf8');
 const manifest=JSON.parse(await readFile(new URL('resources/MANIFEST.json',base),'utf8'));
 if(hash(html)!==manifest.frontend.sha256)throw Error('ELDERLAND_SOURCE_CHANGED');
 const replace=(before,after)=>{if(!html.includes(before))throw Error('ADAPTER_ANCHOR_MISSING: '+before.slice(0,60));html=html.replace(before,after);};
 for(const asset of JSON.parse(await readFile(new URL('resources/ASSETS.json',base),'utf8'))){
  if(hash(await readFile(new URL('public/'+asset.file,base)))!==asset.sha256)throw Error('ELDERLAND_ASSET_CHANGED');
  replace(asset.url,'/rpg-app/elderland/'+asset.file);
 }
 // The source import follows CSS rules, so browsers ignore it. Keep the same
 // rendered system-font fallback without making a third-party font request.
 html=html.replace(/^@import url\([^\n]+\);$/m,'/* Original late font import is ignored by browsers; preserve its fallback. */');
 replace('<div class="opening-screen" id="opening">','<div class="opening-screen" id="opening" role="button" tabindex="0" aria-label="开始冒险，再次激活可跳过动画">');
 replace("this.classList.add('open');", "if(this.classList.contains('open')||matchMedia('(prefers-reduced-motion: reduce)').matches){finishOpening();return;}\nthis.classList.add('open');");
 replace("this.style.display='none';document.getElementById('main').classList.add('active');",'finishOpening();');
 // Selecting an already-rendered location in another region must also bind
 // its country, rather than carrying the last country's value across tabs.
 replace('worldState.selectedRegion=rk;','worldState.selectedRegion=rk;\nworldState.selectedCountry=country;');
 replace('function toggleAbility(el){','function toggleAbility(el,event){');
 html=html.replaceAll('toggleAbility(this)','toggleAbility(this,event)');
 replace('if(event.target!==cb)cb.checked=!cb.checked;','if(event?.target!==cb)cb.checked=!cb.checked;');
 replace('function renderCustomAbilities(){',`function escapeCustom(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function renderCustomAbilities(){`);
 // Only escape user-editable values at their HTML sinks. Preview remains plain text.
 replace('${ability.name}</strong>','${escapeCustom(ability.name)}</strong>');
 replace('${ability.desc}</p>','${escapeCustom(ability.desc)}</p>');
 replace('${ability.ring}环</span>','${escapeCustom(ability.ring)}环</span>');
 replace('<button class="copy-btn" onclick="copyPreview()">复制角色卡</button>','<div class="preview-actions"><button class="copy-btn" onclick="copyPreview()">复制角色卡</button><button class="copy-btn" id="open-chat" hidden>带入聊天 →</button></div><p id="transfer-status" role="status"></p>');
 replace('</style>',`
[hidden]{display:none!important;}
:focus-visible{outline:2px solid var(--royal-blue);outline-offset:4px;}
.tab-btn{position:relative;}
.preview-actions{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:0.01ms!important;transition-duration:0.01ms!important;scroll-behavior:auto!important;}}
</style>`);
 replace('</body>',`<script>
function finishOpening(){document.getElementById('opening').style.display='none';document.getElementById('main').classList.add('active');}
const opening=document.getElementById('opening');
opening.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();opening.click();}});
// Preserve native checkbox activation; keyboard users can also reach the
// author-created country, location, ability and talent cards.
function enhanceControls(){
 document.querySelectorAll('.country-card,.location-item').forEach(el=>{if(el.dataset.keyboard)return;el.dataset.keyboard='1';el.tabIndex=0;el.setAttribute('role','button');el.addEventListener('keydown',e=>{if(e.target===el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();el.click();}});});
 document.querySelectorAll('input,select,textarea').forEach(el=>{if(el.labels?.length||el.getAttribute('aria-label'))return;const label=el.closest('.form-group')?.querySelector('label')?.textContent||el.parentElement?.textContent||el.placeholder||el.id;el.setAttribute('aria-label',label.trim().slice(0,100));});
 const dwarf=document.querySelector('#tab-dwarf .world-map');if(dwarf)dwarf.alt='矮人山脉地图';
}
window.addEventListener('DOMContentLoaded',enhanceControls);
const controlObserver=new MutationObserver(enhanceControls);
controlObserver.observe(document.querySelector('.content-wrapper'),{childList:true,subtree:true});
window.addEventListener('pagehide',()=>controlObserver.disconnect());
const chatButton=document.getElementById('open-chat');
if(new URLSearchParams(location.search).get('chat')==='1'&&parent!==window){
 chatButton.hidden=false;
 chatButton.addEventListener('click',()=>{generatePreview();const text=document.getElementById('preview-box').textContent;if(text.length>20000){document.getElementById('transfer-status').textContent='角色卡超过 20,000 字，请精简自定义内容后再带入聊天。';return;}parent.postMessage({type:'elderland-opening',text},new URL(location.href).origin);});
}
</script></body>`);
 return html;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 const html=await render(),output=new URL('public/index.html',base);
 if(process.argv.includes('--check')){if(await readFile(output,'utf8')!==html)throw Error('GENERATED_HTML_STALE');}
 else await writeFile(output,html);
}
