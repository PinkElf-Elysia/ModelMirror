import {useEffect,useState} from 'react';
import {ChatView} from './chat';
import {api} from './session-client';
export {api} from './session-client';
import {Modal} from './modal';
type Params={temperature:number;top_p:number;top_k:number;presence_penalty:number;frequency_penalty:number;max_tokens:number;context:number;thinking_budget:number};
export function ParameterDialog({params,save,close}:{params:Params;save:(p:Params)=>void;close:()=>void}){
 const [p,setP]=useState(params);
 const rows:[keyof Params,string,number,number,number][]=[['temperature','温度',0,2,.01],['top_p','TOP P',0,1,.01],['top_k','TOP K',0,1000,1],['presence_penalty','存在惩罚',-2,2,.01],['frequency_penalty','频率惩罚',-2,2,.01],['max_tokens','最大回复',1,32768,1],['context','上下文设置',1,131072,1],['thinking_budget','思考预算',0,32768,1]];
 return <Modal title="模型参数" className="platform-dialog" close={close}><div className="parameter-list">{rows.filter(([key])=>['temperature','top_p','max_tokens'].includes(key)).map(([key,label,min,max,step])=><label key={key}><span>{label}</span><input aria-label={label+'数值'} type="number" min={min} max={max} step={step} value={p[key]} onChange={e=>setP({...p,[key]:Number(e.target.value)})}/><input aria-label={label} type="range" min={min} max={max} step={step} value={p[key]} onChange={e=>setP({...p,[key]:Number(e.target.value)})}/></label>)}</div><p className="hint left">这些参数用于下一次创建的会话。模型可能有自己的输出长度限制。</p><div className="two"><button className="secondary" onClick={close}>取消</button><button className="primary" disabled={rows.some(([k,,min,max,step])=>!Number.isFinite(p[k])||p[k]<min||p[k]>max||(step===1&&!Number.isInteger(p[k])))} onClick={()=>save(p)}>保存</button></div></Modal>;
}
export function Help(){return <div className="help"><h3>开始一段故事</h3><p>直接进入聊天，写下开场或粘贴角色档案。角色页生成的资料也可以带入输入框修改；点击发送后才生成剧情。聊天记录自动保存，未发送的草稿留在当前浏览器。</p><h3>接着玩，或换个选择</h3><p>从故事馆或聊天左侧的会话列表打开存档。启用「分支存档」后，可以从某个回合创建新的故事，原对话会保留。</p><h3>模型与记忆</h3><p>在聊天右上角打开「插件」，启用需要的功能。模型在顶栏切换；自动总结 2.0 每轮生成小结，每积满 20 条合并为大总结。记忆宫殿负责整理人物、地点与经历，两者可同时使用。</p><p>总结与记忆会额外调用模型，费用在启用处说明。历史窗口只控制续写携带的近期内容，不删除记录；启用总结时由总结接管。</p><h3>留住这一幕</h3><p>启用场景插画后，在消息旁选择画面、编辑描述并生成图片。聊天记录插件可以导入或导出文本；消息工具可以复制、编辑输入和重新生成。</p><h3>遇到发送中断</h3><p>结果没有确认时，先点击「核对记录」。系统会查找原操作结果，避免重复生成。未发送的草稿仍留在当前浏览器。</p><p><a href="/help/play-rpg-cards" target="_top">查看完整使用帮助 ↗</a></p></div>;}

function Create({close,opened}:{close:()=>void;opened:(id:string)=>void}){
 const [list,setList]=useState<{id:string;name:string;turns:number;mode:string}[]>([]),[error,setError]=useState('');
 useEffect(()=>{let active=true;api('sessions').then(x=>active&&setList(x)).catch(e=>active&&setError(e.message));return()=>{active=false;};},[]);
 return <Modal title="会话历史" className="platform-dialog" close={close}>{error&&<p role="alert">{error}</p>}<div className="history-list">{list.map(s=><button key={s.id} className="option" onClick={()=>opened(s.id)}><span><strong>{s.name}</strong><small>{s.turns} 回合 · {s.mode==='real'?'真实模型':'离线预览'}</small></span><span>打开 →</span></button>)}{!list.length&&<p className="muted">还没有保存的会话。</p>}</div></Modal>;
}
export const Chat=Object.assign(ChatView,{Create});
