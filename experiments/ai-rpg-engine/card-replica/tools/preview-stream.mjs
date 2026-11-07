// Isolated, in-memory demonstration. No provider, credentials or saved sessions.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join, extname} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {build} from 'vite';
import {streamPresentation} from '../lib/stream-presentation.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const out=join(root,'../.rpg04-work/stream-preview');
const port=Number(process.argv[2]||19872);
if(!Number.isInteger(port)||port<1024||port>65535)throw Error('INVALID_PORT');
await build({root,configFile:false,base:'/',build:{outDir:out,emptyOutDir:false,rollupOptions:{input:join(root,'tests/stream-browser.html')}}});
const csp="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'self'; base-uri 'none'; form-action 'none'";
const sessions=new Map(),progress=new Map();
const plugin={id:'rpg.message-tools',name:'消息工具',version:'fixture',artifactSha256:'fixture',manifestSha256:'fixture',permissions:[],installed:true};
const cards=new Set(['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','blue-fish','douluo','dragon-raja','jinyong']);
const server=createServer(async(req,res)=>{
 const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
 try {
  const url=new URL(req.url,'http://127.0.0.1:'+port),path=url.pathname;
  if(path==='/'){res.writeHead(302,{Location:'/rpg-app/god/'});return res.end();}
  const match=path.match(/^\/rpg-app\/([^/]+)\/(.*)$/);
  if(match&&cards.has(match[1])){
   const card=match[1],route=match[2].replace(/^chat\//,'');
   if(!route){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':csp});return res.end(await readFile(join(out,'tests/stream-browser.html')));}
   if(route.startsWith('api/')){
    const tail=route.slice(4),id=tail.match(/^sessions\/([^/]+)/)?.[1]||'fixture',key=card+':'+id;
    if(!sessions.has(key))sessions.set(key,{id,name:'离线流式排版演示 · 不调用模型',characterText:'中性旅人',revision:0,mode:'real',turns:[],requests:{}});
    const s=sessions.get(key);
    if(tail==='status')return send(200,{budget:{remaining:999,used:0,reserved:0,limit:999}});
    if(tail==='plugins')return send(200,{revision:1,plugins:[plugin]});
    if(tail==='sessions')return send(200,[]);
    if(tail===`sessions/${id}`)return send(200,s);
    if(tail.endsWith('/plugins/rpg.message-tools'))return send(200,{enabled:true,installed:true,sessionRevision:s.revision,registryRevision:1});
    if(tail.endsWith('/message-tools/progress'))return send(200,{supported:true,text:progress.get(key+':'+url.searchParams.get('request'))||''});
    if(tail.endsWith('/cancel')){for(const r of Object.values(s.requests))if(r.status==='pending')r.status='cancelled';return send(200,s);}
    if(tail.endsWith('/send')&&req.method==='POST'){
     const chunks=[];let size=0;for await(const c of req){size+=c.length;if(size>2097152)return send(413,{error:'FIXTURE_INPUT_LIMIT'});chunks.push(c);}
     const envelope=JSON.parse(Buffer.concat(chunks).toString()),body=envelope.request||envelope;
     if(!body.requestId||Object.values(s.requests).some(r=>r.status==='pending'))return send(409,{error:'FIXTURE_PENDING'});
     const id=body.requestId,pk=key+':'+id;s.requests[id]={status:'pending'};
     const pieces=['<details open><summary>角色状态</summary>\n\n','**旅人**抵达港口，停在路牌前。\n\n','| 时间 | 地点 |\n| --- | --- |\n','| 清晨 | 港口 |\n| 午后 | 书院 |\n\n','```text\n行囊：旧信、地图\n','任务：寻找档案馆\n```\n','</details>\n\n','<details open><summary>周围环境</summary>\n\n','微风吹过街道。你可以在生成中展开或收起面板。\n','</details>\n\n','演示结束。这些内容来自固定样例，不会消耗模型额度。'];
     if(card==='douluo')pieces.push('<div class="live-container"><div class="topic">港口直播 · 中性样例</div><div class="danmu-area"><marquee><span>一路平安，别忘了地图。</span></marquee></div></div><div class="aa"><label><input class="bb" type="checkbox"><img src="https://invalid.example/avatar.png"></label><div class="cc"><div class="dd">旁白：先看路牌，再找书院。</div></div></div>');
     let raw='';for(const piece of pieces){await delay(900);if(s.requests[id].status==='cancelled')break;raw+=piece;progress.set(pk,raw);}
     if(s.requests[id].status!=='cancelled'){s.turns.push({requestId:id,input:body.input,raw,html:streamPresentation(raw,card),at:new Date().toISOString()});s.requests[id].status='complete';s.revision++;}
     progress.delete(pk);return send(200,s);
    }
    return send(404,{error:'OFFLINE_DEMO_ONLY'});
   }
  }
  if(path==='/rpg-app/api/plugins')return send(200,{revision:1,plugins:[plugin]});
  const asset=path.match(/^\/assets\/([\w.-]+)$/),config=path.match(/^\/rpg-app\/custom-config\/([\w.-]+)$/);
  const file=asset?join(out,'assets',asset[1]):config?join(root,'../plugins/custom-config/public',config[1]):null;
  if(file){const type={'.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.html':'text/html'}[extname(file)];if(!type)return send(404,{});res.writeHead(200,{'Content-Type':type+'; charset=utf-8'});return res.end(await readFile(file));}
  send(404,{});
 }catch{if(!res.headersSent)send(500,{error:'OFFLINE_PREVIEW_ERROR'});else res.end();}
});
server.listen(port,'127.0.0.1',()=>console.log('Offline preview: http://127.0.0.1:'+port+'/rpg-app/god/'));
