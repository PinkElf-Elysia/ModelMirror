// Read-only replay of already saved replies. No credentials, no Provider, no writes to source sessions.
import http from 'node:http';
import {readFile,writeFile,mkdir,stat} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {start} from '../../studio/server.mjs';
const repo=fileURLToPath(new URL('../../../../',import.meta.url));
const root=join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/r2');
const port=18514, sources={
 god:join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real/data/god/d717d78a-dbdd-4256-80ef-780d3ce2abb2.json'),
 earth:process.argv[2],
};
if(!sources.earth)throw Error('Provide the explicit saved Earth session file as the sole argument.');
const receipt={readOnly:true,providerCalls:0,sessions:{}};
for(const [card,path] of Object.entries(sources)){
 const bytes=await readFile(path),s=JSON.parse(bytes);
 if(!/^[a-zA-Z0-9-]+$/.test(s.id)||!Array.isArray(s.turns)||!s.turns.length)throw Error('INVALID_REPLAY_SESSION');
 const dir=join(root,'data',card);await mkdir(dir,{recursive:true});
 const dest=join(dir,s.id+'.json');
 try{await writeFile(dest,bytes,{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;if(!bytes.equals(await readFile(dest)))throw Error('REPLAY_COPY_CHANGED');}
 receipt.sessions[card]={id:s.id,source:resolve(path),sha256:createHash('sha256').update(bytes).digest('hex'),turns:s.turns.length};
}
const app=await start({port:0,directory:join(root,'data'),cardDist:join(root,'dist'),godCard:true,godPlugins:true,memoryPalace:true,enabled:false,limit:0,origins:[`http://127.0.0.1:${port}`]});
const backend=app.server.address().port;
const client=join(repo,'client/dist');
const links=Object.entries(receipt.sessions).map(([card,s])=>`<li><a href="/rpg-app/${card}/?session=${s.id}">${card==='god'?'上帝卡':'地球 OL'}：${s.turns} 回合已保存原文</a>${card==='earth'?'（进入后点“历史”，打开“林遥”）':''}</li>`).join('');
const server=http.createServer(async(req,res)=>{
 res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');
 if(req.method!=='GET'){res.writeHead(403,{'Content-Type':'application/json'});res.end('{"error":"READ_ONLY_REPLAY"}');return;}
 try{
  const path=new URL(req.url,'http://preview').pathname;
  if(path==='/'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end('<!doctype html><meta name="viewport" content="width=device-width"><title>R2 两卡展示验收</title><main style="max-width:760px;margin:40px auto;padding:20px;font:16px/1.8 system-ui"><h1>R2 两卡展示验收</h1><p>读取已保存回复的独立副本；不调用模型，所有写操作被拒绝。旧版本会话不迁移，显示版本不兼容是预期保护。</p><ul>'+links+'</ul><p>原文、宏观/微观、弹幕、图片展开与折叠可在这里检查。原验收入口和数据保持不变。</p></main>');return;}
  if(path.startsWith('/rpg-app/')){
   const r=http.request({hostname:'127.0.0.1',port:backend,path:req.url,method:'GET',headers:req.headers},up=>{
    const status=/^\/rpg-app\/(god|earth)\/api\/status$/.test(path);
    const html=(up.headers['content-type']||'').includes('text/html');
    if(!status&&!html){res.writeHead(up.statusCode,up.headers);up.pipe(res);return;}
    const chunks=[];up.on('data',b=>chunks.push(b));up.on('end',()=>{
     let body=Buffer.concat(chunks).toString('utf8');
     if(status&&up.statusCode===200){const value=JSON.parse(body);const budget={enabled:false,limit:0,used:0,remaining:0};body=JSON.stringify({...value,readOnlyReplay:true,providerEnabled:false,budget,offlineBudget:budget});}
     if(html)body=body.replace(/<body([^>]*)>/i,'<body$1><aside style="padding:10px 16px;background:#fff4cc;color:#392b00;font:14px/1.5 system-ui"><strong>只读回放 · 不调用模型</strong> · <a href="/">两卡验收入口</a><br>这是已保存原文的副本，所有写操作已关闭；原验收入口、会话及额度不变。</aside>');
     const headers={...up.headers};delete headers['content-length'];delete headers['transfer-encoding'];res.writeHead(up.statusCode,headers);res.end(body);
    });
   });
   r.on('error',()=>res.writeHead(502).end());r.end();return;
  }
  const file=resolve(client,path.replace(/^\/+/,''));
  if(!file.startsWith(client+'\\')&&!file.startsWith(client+'/')){res.writeHead(404).end();return;}
  let bytes,type;try{if(!extname(file)||(await stat(file)).isDirectory())throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(client,'index.html'));type='.html';}
  res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream')});res.end(bytes);
 }catch{res.writeHead(500).end('Replay unavailable');}
});
await new Promise((yes,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',yes);});
await writeFile(join(root,'replay-receipt.json'),JSON.stringify({...receipt,pid:process.pid,backend,port},null,2));
console.log(`R2 read-only replay http://127.0.0.1:${port}/`);
