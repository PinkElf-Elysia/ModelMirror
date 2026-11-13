import {readFile} from 'node:fs/promises';
import {cardDisplay} from '../card-replica/lib/card-presentation.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
const root=new URL('../card-replica/',import.meta.url);
const css=await readFile(new URL('src/card-presentation.css',root),'utf8');
// Reuse the reviewed card renderer, then isolate it. No response scripts, remote
// images, CG capture, state update or model dispatch is reachable from this path.
export async function comparisonFrame(card,raw){
 let html=['earth','god'].includes(card)?cardDisplay(raw,card):safeHtml(raw);
 const assets=[...new Set([...html.matchAll(/src="\.\/assets\/([a-zA-Z0-9_.-]+)"/g)].map(m=>m[1]))];
 for(const name of assets){if(!/\.(webp|png|jpe?g|gif)$/.test(name))continue;try{const bytes=await readFile(new URL('public/assets/'+name,root)),type=name.endsWith('.webp')?'image/webp':name.endsWith('.png')?'image/png':name.endsWith('.gif')?'image/gif':'image/jpeg';html=html.replaceAll('src="./assets/'+name+'"','src="data:'+type+';base64,'+bytes.toString('base64')+'"');}catch{}}
 const csp="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; media-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'";
 return {frame:'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+csp+'"><style>body{margin:0;padding:16px;color:#293549;background:#fff;font:16px/1.8 system-ui;overflow-wrap:anywhere}pre{white-space:pre-wrap}img{max-width:100%}'+css+'</style></head><body><div class="model-output">'+html+'</div></body></html>'};
}
