import {readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {join, resolve} from 'node:path';

const root = resolve('C:/tmp/mm-r10-live');
const read = async (path) => JSON.parse(await readFile(join(root, path), 'utf8'));
const resume = process.argv[2] === '--resume';
const folder = resume ? 'real-resume-01' : 'real-01';
const port = resume ? 18928 : 18927;
const real = await read(folder+'/review.json');
const mock = await read((resume?'mock-resume-01':'mock-02')+'/review.json');
const cleanup = await read(folder+'/cleanup.json');
const audit = await read(folder+'/dispatch-audit.json');
const gift = await read(folder+'/' + real.cases[0].id + '.json');
const names = [resume?'赠药续答':'赠药原话', '自服提案', '回执后的正文', '人物观察与查询', '基于查询的答复'];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const json = v => '<pre>' + esc(JSON.stringify(v, null, 2)) + '</pre>';
const cleanText = v => (v ?? '').replace(/<(think|cot)\b[^>]*>[\s\S]*?<\/\1>/gi,'').replace(/<!--[\s\S]*?-->/g,'').trim();

function view(r) {
  const rows = names.map((name,i) => {
    const call = r.calls.find(c => c.slot === i+1);
    if (!call) return '<tr><th>'+esc(name)+'</th><td colspan="3">未派发</td></tr>';
    const tools = call.message?.tool_calls ?? [];
    const body = cleanText(call.message?.content);
    return '<tr><th>'+esc(name)+'</th><td>'+esc(call.finish)+'</td><td>'+esc(call.usage?.inputTokens)+' / '+esc(call.usage?.outputTokens)+'</td><td>'+esc(call.accounting?.reportedCostMicros == null ? '未报告' : '$'+(call.accounting.reportedCostMicros/1e6).toFixed(6))+'</td></tr><tr><td colspan="4">'+
      (body?'<p>'+esc(body)+'</p>':'<p>本次回复没有正文。</p>')+
      (tools.length?json(tools.map(t=>({id:t.id,name:t.function.name,arguments:t.function.arguments}))):'')+'</td></tr>';
  }).join('');
  return '<table><thead><tr><th>请求</th><th>结束原因</th><th>输入 / 输出 token</th><th>回报费用</th></tr></thead><tbody>'+rows+'</tbody></table>'+
    r.cases.map((c,i)=>'<section><h3>'+['赠药存档','自服存档'][i]+'</h3><blockquote>'+esc(c.choices[0]?.text)+'</blockquote><p>养息丹 '+esc(c.quantity)+' 份 · 行动回执 '+esc(c.events.length)+' 张 · 人物答复 '+esc(c.speeches.length)+' 条</p>'+
      (c.events.length?json(c.events.map(e=>({eventId:e.eventId,result:e.result,changes:e.changes}))):'')+
      c.speeches.map(s=>'<p>'+esc(cleanText(s))+'</p>').join('')+'</section>').join('');
}
const source = resume ? await read('real-01/stage-1.json') : null;
const paid = resume ? await read(folder+'/stage-1.json') : null;
const proposal = Object.values(gift.world.proposals)[0]?.proposal;
const introduction = resume ? '<p>玩家明确表示送给周禾、自己不服用。Gemini 提出的动作为玩家自服 2 份，实测已在确认前停止。两份存档均未扣药。</p>'+
  '<details open><summary>复用的只读请求与本次结果</summary><p>原调用编号 '+esc(real.resumedFrom.callId)+'。该次派发未重发。下列查询结果来自原存档。</p>'+
  json(JSON.parse(paid.payload.messages.at(-1).content))+
  '<p>本次返回的提案</p>'+json({actor:proposal?.actor,action:proposal?.action,executionStatus:proposal?.executionStatus})+
  '<p>新增费用 $'+esc((real.calls[0].usage.reportedCostMicros/1e6).toFixed(6))+'；上次 $'+esc((source.result.usage.reportedCostMicros/1e6).toFixed(6))+'。</p></details>' :
  '<p>赠药原话触发只读查询。赠药请求仅有一次生成额度，查询后的续写未派发。玩家没有收到最终答复，也没有自服提案或库存扣减。</p>';
const html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>药圃 · 第十轮实测回放</title><style>body{max-width:900px;margin:auto;padding:2rem 1rem 5rem;background:#faf9f5;color:#212c29;font:16px/1.6 system-ui}h1,h2{line-height:1.3}h2{margin-top:2rem}table{width:100%;border-collapse:collapse;background:white}th,td{padding:.55rem;text-align:left;border-bottom:1px solid #ddd}pre,p,blockquote{overflow-wrap:anywhere;white-space:pre-wrap}pre{font-size:.86rem;background:#f2f4ef;padding:.7rem}blockquote{margin:0;padding-left:1rem;border-left:3px solid #5e8579}summary{cursor:pointer}section{margin:1rem 0}</style><main><h1>药圃 · 第十轮实测回放</h1><h2>Gemini 真实调用</h2><p>'+esc(real.model)+' · 已派发 '+audit.length+' 次</p>'+introduction+
    '<p>任务结果 '+esc(gift.tasks[0]?.error??gift.tasks[0]?.adoption)+' · 验收停止原因 '+esc(real.error)+' · 首次请求完成 '+esc((real.calls[0].timing.completedMs/1000).toFixed(2))+' 秒 · 正文首字无样本</p>'+view(real)+
    '<h2>同一版本的离线对照</h2><details><summary>展开模拟的五个请求、行动回执与人物答复</summary>'+view(mock)+'</details>'+
    '<h2>证据</h2><p>真实派发回执</p>'+json(real.calls.map(c=>({requestId:c.receipt?.requestId,status:c.receipt?.status,httpStatus:c.receipt?.httpStatus,attemptId:c.receipt?.attemptId,retries:c.receipt?.retries,sseHash:c.receipt?.sseHash})))+
    '<p>临时凭据清理</p>'+json(cleanup)+'<p>完整原始回复、工具消息、SSE 和存档已保存在本机 '+esc(join(root,folder))+'。</p></main></html>';
await writeFile(join(root,folder,'acceptance.html'), html, 'utf8');
createServer((req,res)=>{
  if (req.method!=='GET'||req.url!=='/'||req.headers.host!=='127.0.0.1:'+port) {res.writeHead(404); return res.end();}
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"});res.end(html);
}).listen(port,'127.0.0.1');
