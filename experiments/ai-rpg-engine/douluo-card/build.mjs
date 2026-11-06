import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=new URL('./',import.meta.url);
const read=path=>readFile(new URL(path,root),'utf8');
const manifest=JSON.parse(await read('resources/MANIFEST.json'));
let html=await read('resources/original.html');
if(createHash('sha256').update(html).digest('hex')!==manifest.htmlSha256)throw Error('DOULUO_SOURCE_DRIFT');
for(const asset of JSON.parse(await read('resources/assets.json'))){
 if(!asset.file)throw Error('DOULUO_ASSET_MISSING');
 const bytes=await readFile(new URL('public/'+asset.file,root));
 if(createHash('sha256').update(bytes).digest('hex')!==asset.sha256)throw Error('DOULUO_ASSET_DRIFT');
 html=html.replaceAll(asset.url,'./'+asset.file);
}
html=html.replace(/<link rel="preconnect"[^>]+>\s*/, '').replace(/<link href="https:\/\/fonts.googleapis.com\/[^>]+>/,'<link rel="stylesheet" href="./assets/fonts.css">');
html=html.replace('if(BG_MUSIC_URL) return;',"if(typeof SONGS!=='undefined'&&SONGS.length){this.mg.gain.value=0;return;}");
html=html.replace("var p=bgMusic.play(); if(p&&p.catch) p.catch(function(){});\n  toast", "if(musicWanted){var p=bgMusic.play(); if(p&&p.catch) p.catch(function(){});}else{bgMusic.pause();}\n  toast");
html=html.replace("try{bgVideo.play()}catch(e){}", "bgVideo.play().catch(function(){})");
html=html.replace('<button class="btn btn-gold" id="copyBtn">复制</button>', '<button class="btn btn-gold" id="copyBtn">复制</button><button class="btn btn-gold" id="enterChatBtn">以此档案进入聊天</button>');
html=html.replace("$('copyBtn').addEventListener('click',function(){ copyText(buildText(),'档案已复制到剪贴板'); });", "$('copyBtn').addEventListener('click',function(){ copyText(buildText(),'档案已复制到剪贴板'); });\n $('enterChatBtn').addEventListener('click',function(){if(window.parent===window){toast('请从故事馆打开此卡片');return;}var text=buildText();if(text.length>20000){toast('档案过长，请缩短到 20000 字以内');return;}window.parent.postMessage({type:'douluo-opening',text:text},'*');});");
// No remote fallback: every listed character has an authored local image.
html=html.replaceAll("('https://picsum.photos/seed/douluo-'+s.img+'/540/920.jpg')", "''");
html=html.replace('</head>',`<style>
:focus-visible{outline:2px solid var(--gold);outline-offset:3px}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}#bgDust,#fxCanvas,#entryFx,#soulFx,#orbLightning,#bgVideo,.dm-layer{display:none!important}}
</style></head>`);
await writeFile(new URL('public/index.html',root),html);
console.log('Built douluo with intact source content, local media, chat handoff and audio initialization fix');
