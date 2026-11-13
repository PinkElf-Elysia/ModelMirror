import {readFile} from 'node:fs/promises';
const assets=JSON.parse(await readFile(new URL('./resources/assets.json',import.meta.url)));
const types={'index.html':'text/html; charset=utf-8','bridge.js':'text/javascript; charset=utf-8','adaptation.css':'text/css; charset=utf-8',...Object.fromEntries(assets.map(a=>[a.file,a.mime]))};
export async function aiGirlsAsset(req,res){
 const path=new URL(req.url,'http://local').pathname;
 if(!['GET','HEAD'].includes(req.method)||!path.startsWith('/rpg-app/ai-girls/'))return false;
 const file=path.slice('/rpg-app/ai-girls/'.length)||'index.html';
 if(!Object.hasOwn(types,file))return false;
 const bytes=await readFile(new URL('./public/'+file,import.meta.url));
 res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 res.setHeader('Access-Control-Allow-Origin','*');
 const headers={'Content-Type':types[file],'Accept-Ranges':'bytes'};
 const range=req.headers.range;
 if(range){
  const match=/^bytes=(\d*)-(\d*)$/.exec(range);
  let start=Number(match?.[1]||0),end=match?.[2]?Number(match[2]):bytes.length-1;
  if(match&&!match[1]&&match[2]){start=Math.max(0,bytes.length-Number(match[2]));end=bytes.length-1;}
  if(!match||(!match[1]&&!match[2])||!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start>=bytes.length||end<start){res.writeHead(416,{'Content-Range':'bytes */'+bytes.length});res.end();return true;}
  end=Math.min(end,bytes.length-1);res.writeHead(206,{...headers,'Content-Range':`bytes ${start}-${end}/${bytes.length}`,'Content-Length':end-start+1});res.end(req.method==='HEAD'?undefined:bytes.subarray(start,end+1));return true;
 }
 res.writeHead(200,{...headers,'Content-Length':bytes.length});res.end(req.method==='HEAD'?undefined:bytes);return true;
}
