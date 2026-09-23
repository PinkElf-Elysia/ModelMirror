import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
const root=new URL('./',import.meta.url);
const names=['CONTRACT.md','index.html','prototype.svg','preview.mjs'];
const sha=x=>createHash('sha256').update(x).digest('hex');
export async function inspect(){
 const files={};for(const name of names)files[name]=await readFile(new URL(name,root));
 const html=files['index.html'].toString('utf8'),contract=files['CONTRACT.md'].toString('utf8').replaceAll('\r\n','\n');
 const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];assert.ok(script,'prototype script');
 const result=spawnSync(process.execPath,['--check','--input-type=commonjs'],{input:script,encoding:'utf8'});assert.equal(result.status,0,result.stderr);
 assert.ok(!/\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\s*\(/.test(script),'no model or network operations in prototype');
 assert.ok(!/(?:src|href)=["']https?:\/\//i.test(html),'no external resources');
 const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(ids.length,new Set(ids).size,'unique DOM IDs');
 for(const [,id] of script.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(id),'referenced DOM ID '+id);
 for(const [,id] of html.matchAll(/(?:data-close|for|aria-labelledby)="([^"]+)"/g))assert.ok(ids.includes(id),'linked control '+id);
 const instruction=contract.match(/```memory-instruction\n([\s\S]*?)\n```/)?.[1];
 const wrapper=contract.match(/```memory-wrapper\n([\s\S]*?)\n```/)?.[1];assert.ok(instruction&&wrapper);
 const colors=[['text','#263441','#ffffff'],['hint','#536475','#ffffff'],['primary','#ffffff','#2669c9'],['fee','#3f5266','#f1f5fa']];
 const lum=h=>{const rgb=h.match(/[a-f\d]{2}/gi).map(c=>parseInt(c,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};
 const contrast=Object.fromEntries(colors.map(([k,a,b])=>{const x=lum(a),y=lum(b),ratio=(Math.max(x,y)+.05)/(Math.min(x,y)+.05);assert.ok(ratio>=4.5,k+' contrast');return [k,Number(ratio.toFixed(2))];}));
 return {scope:'B0 static candidate, not production tests',files:Object.fromEntries(names.map(n=>[n,{sha256:sha(files[n]),bytes:files[n].length}])),instructionSha256:sha(instruction),wrapperSha256:sha(wrapper),checks:{scriptSyntax:'pass',domReferences:'pass',noNetworkCalls:'pass',externalResources:'none',contrast},browser:'not-run'};
}
if(process.argv.includes('--check')){
 const report=await inspect();
 if(process.argv.includes('--verify-delivery')){const s=JSON.parse(await readFile(new URL('STATUS.json',root),'utf8'));assert.deepEqual(s.delivery.files,report.files);assert.equal(s.delivery.instructionSha256,report.instructionSha256);assert.equal(s.delivery.wrapperSha256,report.wrapperSha256);report.deliveryVerified=true;}
 console.log(JSON.stringify(report,null,2));
}else{
 const host='127.0.0.1',port=18489;
 const routes=new Map([['/',['index.html','text/html']],['/index.html',['index.html','text/html']],['/prototype.svg',['prototype.svg','image/svg+xml']],['/CONTRACT.md',['CONTRACT.md','text/plain']],['/STATUS.json',['STATUS.json','application/json']]]);
 const server=createServer(async(req,res)=>{try{
  if(req.headers.host!==host+':'+port){res.writeHead(403);return res.end('loopback host only');}
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{Allow:'GET, HEAD'});return res.end();}
  const path=new URL(req.url,'http://'+host+':'+port).pathname;
  const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"};
  if(path==='/api/status'){res.writeHead(200,{...headers,'Content-Type':'application/json; charset=utf-8'});return res.end(req.method==='HEAD'?'':JSON.stringify({batch:'M3-B0',mode:'static-prototype',providerEnabled:false,modelDispatches:0,persistence:'browser-memory-only'}));}
  const entry=routes.get(path);if(!entry){res.writeHead(404);return res.end();}
  const bytes=await readFile(new URL(entry[0],root));res.writeHead(200,{...headers,'Content-Type':entry[1]+'; charset=utf-8'});res.end(req.method==='HEAD'?undefined:bytes);
 }catch{res.writeHead(500);res.end('preview read failed');}});
 server.on('error',e=>{console.error(e.code);process.exitCode=1;});
 server.listen(port,host,()=>console.log('M3 B0 prototype http://'+host+':'+port+' · no Provider'));
}
