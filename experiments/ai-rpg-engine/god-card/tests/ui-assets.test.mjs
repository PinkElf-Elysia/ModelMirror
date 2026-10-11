import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {godUi,godDocumentNavigation} from '../lib/ui.mjs';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {commentaryAssets} from '../../card-replica/lib/card-presentation.mjs';

test('God HTTP serves every reviewed local commentary image and rejects unknown paths',async()=>{
 const root=fileURLToPath(new URL('../../card-replica/public/',import.meta.url));
 const server=http.createServer(async(req,res)=>{try{if(!await godUi(req.url.slice(1),res,root))res.writeHead(404).end();}catch{res.writeHead(500).end();}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try {
  const base='http://127.0.0.1:'+server.address().port+'/';
  for(const asset of Object.values(commentaryAssets)){
   const response=await fetch(new URL(asset,base));
   assert.equal(response.status,200,asset);assert.match(response.headers.get('content-type'),/^image\/(jpeg|png|webp)$/);
   assert.match(response.headers.get('content-security-policy'),/img-src 'self' data:/);
   assert.ok((await response.arrayBuffer()).byteLength>0);
  }
  for(const route of ['assets/arbitrary.jpg','assets/god-commentary-v1.webp?tracking=x','assets/..%2fsecret.jpg'])assert.equal((await fetch(base+route)).status,404);
 }finally{await new Promise(resolve=>server.close(resolve));}
});

test('public document navigation exposes only the static shell, never API data or writes',async()=>{
 const root=fileURLToPath(new URL('../../.rpg04-work/god-card/',import.meta.url));
 await mkdir(root,{recursive:true});const dist=await mkdtemp(join(root,'navigation-'));
 await writeFile(join(dist,'god.html'),'<html><body>public shell</body></html>');
 let protectedRequests=0;
 const server=http.createServer(async(req,res)=>{
  if(await godDocumentNavigation(req,res,dist))return;
  protectedRequests++;res.writeHead(403,{'Content-Type':'application/json'}).end('{"error":"ORIGIN_REJECTED"}');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 const request=(path,method='GET',dest='document',mode='navigate')=>new Promise((resolve,reject)=>{
  const req=http.request(base+path,{method,headers:{Origin:'https://external.example','Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':mode,'Sec-Fetch-Dest':dest}},res=>{let body='';res.on('data',b=>body+=b);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body}));});req.on('error',reject);req.end();
 });
 try{
  const shell=await request('/rpg-app/god/?session=untrusted');assert.equal(shell.status,200);assert.equal(shell.body,'<html><body>public shell</body></html>');assert.match(shell.headers['content-security-policy'],/connect-src 'self'/);assert.equal(shell.headers['access-control-allow-origin'],undefined);
  for(const path of ['/rpg-app/god/api/status','/rpg-app/god/api/sessions/private','/rpg-app/api/plugins','/rpg-app/god/resources/prompts.json'])assert.equal((await request(path)).status,403);
  assert.equal((await request('/rpg-app/god/','POST')).status,403);
  assert.equal((await request('/rpg-app/god/','GET','iframe')).status,403);
  assert.equal((await request('/rpg-app/god/','GET','empty','cors')).status,403);
  assert.equal(protectedRequests,7);
 }finally{await new Promise(resolve=>server.close(resolve));}
});
