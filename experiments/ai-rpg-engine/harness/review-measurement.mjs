
import http from 'node:http';import{readFile}from'node:fs/promises';import{join}from'node:path';import{fileURLToPath}from'node:url';import{displayDocument}from'../card-replica/lib/render.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),dir=join(root,'.rpg04-work/harness-real-20261113');
const ids=['B-purchase-repair-1','A-success-1','B-success-1','B-continuation-1'],titles=['修复后 · 购买叙事单次补测','历史：原流程 · 购买两个面包','历史：工具版 · 结算后的交易叙事','历史：工具版 · 购买后继续剧情'];
const esc=s=>String(s??'未知').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const failureLabel=code=>({PROVIDER_OUTPUT_TRUNCATED:'输出达到上限，正文截断',PROVIDER_EMPTY_OUTPUT:'只返回空白正文，未生成可用回复'}[code]||code||'生成失败');
const read=async id=>{try{return JSON.parse(await readFile(join(id==='B-purchase-repair-1'?join(root,'.rpg04-work/harness-repair-real-20261113'):dir,id+'-result.json'),'utf8'));}catch{return null;}};
const server=http.createServer(async(req,res)=>{try{
 const u=new URL(req.url,'http://127.0.0.1');const id=u.searchParams.get('id');
 res.setHeader('Cache-Control','no-store');
 if(req.method!=='GET'){res.writeHead(405);res.end();return;}
 if(['/reply','/source','/result'].includes(u.pathname)&&ids.includes(id)){
  const r=await read(id);if(!r){res.writeHead(404);res.end('结果尚未保存');return;}
  if(u.pathname==='/result'){res.setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(r,null,2));return;}
  if(u.pathname==='/source'){res.setHeader('Content-Type','text/plain; charset=utf-8');res.end(r.raw||'无完整回复');return;}
  res.setHeader('Content-Type','text/html; charset=utf-8');res.end(displayDocument(r.raw||'无完整回复'));return;
 }
 if(u.pathname!=='/'){res.writeHead(404);res.end();return;}
 const results=await Promise.all(ids.map(read)),reported=results.reduce((n,r)=>n+(r?.costMicros??0),0),known=results.filter(r=>r?.costMicros!=null).length;
 const sections=results.map((r,i)=>{const t=r?.measurement?.timing,u=r?.usage;return '<section id="'+ids[i]+'"><h2>'+titles[i]+'</h2><p>状态：'+esc(r?.status==='succeeded'?'完整回复':r?.status==='failed'?'未完成：'+(failureLabel(r.code)):r?.status||'待执行')+' · 输入 '+esc(u?.inputTokens)+' / 输出 '+esc(u?.outputTokens)+' token · 实报费用 '+(r?.costMicros!=null?'$'+(r.costMicros/1e6).toFixed(6):'未知')+'</p><p>首个正文观测 '+(t?.firstContentObservedMs!=null?(t.firstContentObservedMs/1000).toFixed(2)+' 秒':'未知')+' · 完成 '+(t?.completedMs!=null?(t.completedMs/1000).toFixed(2)+' 秒':'未知')+'</p><p><a href="/source?id='+ids[i]+'" target="_blank">查看保存原文</a> · <a href="/result?id='+ids[i]+'" target="_blank">计量与回执</a></p>'+(r?.raw?.trim()?'<iframe title="'+titles[i]+'" sandbox src="/reply?id='+ids[i]+'"></iframe>':'<p>'+ (r?'没有可展示的完整正文；原始回执和统计已保留。':'等待结果；此页不会调用模型。')+'</p>')+'</section>';}).join('');
 res.setHeader('Content-Type','text/html; charset=utf-8');
 res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>地球 OL 商店 · 真实回复验收</title><style>body{margin:0;background:#f2f5f8;color:#203044;font:16px/1.7 system-ui}main{max-width:1100px;margin:auto;padding:28px 20px}h1{font-size:28px}section,.notice{background:white;border:1px solid #d7e0e9;border-radius:14px;padding:22px;margin:20px 0}nav{display:flex;gap:18px;flex-wrap:wrap}a{color:#1763a5}iframe{width:100%;height:1050px;border:1px solid #dce4ec;background:white}p{margin:10px 0}.muted{color:#526174}@media(max-width:650px){main{padding:12px}section{padding:12px}iframe{height:850px}}</style><main><h1>地球 OL 商店 · 真实回复验收</h1><p class="muted">Gemini 3.8 Flash · 保存结果的离线回放，刷新不产生调用</p><div class="notice"><strong>本次补测：最多 1 次 / $0.25；历史测试：3 次 / $0.75</strong><p>已保存 '+results.filter(Boolean).length+' 份结果；累计已知实报费用 $'+(reported/1e6).toFixed(6)+'（'+known+'/4 份有计费回执）。</p><p>程序结算已核对：银行卡 100 → 88 元，面包 +2，店铺面包库存 2 → 0；重复提交未再次扣款。</p><p>历史续写使用冻结的购买成功历史，不冒充接续历史失败回复。本次修复补测独立记录，单次费用列在第一项。首字为宿主观测时间，含轮询和网关缓冲。</p><a href="http://127.0.0.1:18763/rpg-app/earth/?session=shop-start" target="_blank">打开可点击商店（另一个离线模拟预览，不调用模型）</a></div><nav>'+ids.map((id,i)=>'<a href="#'+id+'">'+titles[i]+'</a>').join('')+'</nav>'+sections+'</main></html>');
}catch{res.writeHead(500);res.end('回放暂不可用，请核对保存文件。');}});
server.listen(18764,'127.0.0.1',()=>console.log('Saved real replies: http://127.0.0.1:18764/'));
