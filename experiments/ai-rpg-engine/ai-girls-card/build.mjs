import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const meta=JSON.parse(await readFile(new URL('./resources/source.json',import.meta.url)));
let html=await readFile(new URL('./resources/original.html',import.meta.url),'utf8');
if(sha(html)!==meta.frontendSha256)throw Error('AI_GIRLS_FRONTEND_DRIFT');
const assets=JSON.parse(await readFile(new URL('./resources/assets.json',import.meta.url)));
for(const asset of assets){
 if(sha(await readFile(new URL('./public/'+asset.file,import.meta.url)))!==asset.sha256)throw Error('AI_GIRLS_ASSET_DRIFT: '+asset.file);
 html=html.replaceAll(asset.url,asset.file);
}
function once(from,to){if(html.split(from).length!==2)throw Error('ADAPTATION_ANCHOR_CHANGED');html=html.replace(from,to);}
const author=html.match(/<div class="authorBox">[\s\S]*?<\/div>\s*<\/div>/g);
if(author?.length!==1)throw Error('AUTHOR_ANCHOR_CHANGED');
once(author[0],'');
once('<div id="splash">','<div id="splash" role="button" tabindex="0" aria-label="开始游戏，进入角色面板">');
once('</head>','<link rel="stylesheet" href="adaptation.css">\n</head>');
once('</nav>','<button id="musicToggle" type="button" aria-pressed="false" aria-label="播放背景音乐"><span>音乐 ▶</span></button>\n</nav>');
once('<body>','<body>\n<audio id="backgroundMusic" src="assets/background.mp3" loop preload="none"></audio>');
once("copyText($('preview').value);","window.aiGirlsStart($('preview').value);");
once('</body>','<script src="bridge.js"></script>\n</body>');
await writeFile(new URL('./public/index.html',import.meta.url),html);
console.log('AI girls frontend assembled with '+assets.length+' verified local assets');
