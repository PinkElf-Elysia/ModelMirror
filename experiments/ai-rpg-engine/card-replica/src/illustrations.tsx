import {useEffect,useRef,useState} from 'react';
import {Modal} from './modal';
import {api,archiveRoot,ApiError} from './session-client';
import './illustrations.css';

type Scene={turn:string|null;source:string;focus:'回合剧情'|'关键画面特写'|'选区'};
type Writer={model:string;enabled:boolean;remaining:number};
function sceneFor(output:HTMLElement|null|undefined,turn:string):Scene{
  const selection=window.getSelection();
  if(selection&&output?.contains(selection.anchorNode)&&output.contains(selection.focusNode)&&selection.toString().trim())
    return {turn,source:selection.toString().trim(),focus:'选区'};
  const closeup=output?.querySelector<HTMLElement>('.scene-wrapper:has(.flip-toggle:checked) .flip-card-back');
  const story=closeup??output;
  // A God turn may place the protagonist on the reverse face. Include both narrative
  // faces for a whole turn, even when CSS hides one; omit the surrounding game UI.
  const blocks=Array.from(story?.querySelectorAll<HTMLElement>('.rpg-narrative,.main-text')??[]);
  const lines=blocks.map(block=>{
    const copy=block.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('details,button,script,style,.status-details').forEach(el=>el.remove());
    copy.querySelectorAll('p,br,div').forEach(el=>el.append('\n'));
    return copy.textContent?.trim();
  });
  if(!lines.some(Boolean)){
    for(const p of story?.querySelectorAll<HTMLElement>('p')??[]){
      if(p.offsetHeight&&!p.closest('details')&&getComputedStyle(p).visibility==='visible')lines.push(p.innerText);
    }
  }
  const source=lines.filter(Boolean).join('\n').replace(/\n[\t ]*\n+/g,'\n').trim();
  return {turn,source,focus:closeup?'关键画面特写':'回合剧情'};
}
type Rate={billable:string;unit:string;cost_usd:number;variant?:string|null};
type Profile={model_id:string;display_name:string;pricing:Rate[];options:Record<string,string[]>};
type Job={id:string;turn:string|null;prompt:string;model:string;actualModel?:string;state:'pending'|'complete'|'unknown'|'failed';costUsd?:number|null;costKind?:string};
const labels:Record<string,string>={aspect_ratio:'画幅',resolution:'分辨率',quality:'质量',output_format:'格式',background:'背景'};
const status={pending:'正在生成…',complete:'已保存',unknown:'结果待核对，请勿重复提交。',failed:'请求被拒绝，请检查模型和参数。'};
const unit:Record<string,string>={token:'Token',image:'张',request:'次请求'};
const billable:Record<string,string>={input_text:'输入文本',input_image:'输入图片',input_reference:'参考图',output_image:'输出图片'};
function price(m:Profile|undefined,params:Record<string,string>){
  const rates=m?.pricing??[];
  const outputRates=rates.filter(r=>r.billable==='output_image');
  const variant=params.resolution?.toLowerCase();
  const matching=outputRates.filter(r=>!r.variant||r.variant.toLowerCase()===variant);
  if(variant && matching.length===1 && ['request','image'].includes(matching[0].unit) &&
     rates.every(r=>r.billable!=='input_text'||r.cost_usd===0))return '预计 '+matching[0].cost_usd+' 美元（1 张，'+params.resolution+'）';
  if(rates.length===1&&!rates[0].variant&&rates[0].billable==='output_image'&&['request','image'].includes(rates[0].unit))return '预计 '+rates[0].cost_usd+' 美元（1 张）';
  return rates.length?'总价待结算。目录单价：'+rates.map(r=>(billable[r.billable]||r.billable)+' '+r.cost_usd+' 美元/'+(unit[r.unit]??r.unit)+(r.variant?' · '+r.variant:'')).join('，'):'目录未提供价格，费用以实际结算为准。';
}
export function useIllustrations(id:string,enabled:boolean){
  const [jobs,setJobs]=useState<Job[]>([]),[dialog,setDialog]=useState<Scene|null>(null);
  const [error,setError]=useState(''),[version,setVersion]=useState(0);
  const base='sessions/'+id+'/illustrations/';
  useEffect(()=>{
    if(!enabled){setDialog(null);setJobs([]);setError('');return;}
    let live=true,timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try{const list:Job[]=await api(base+'jobs');if(!live)return;setJobs(list);setError('');
        if(list.some(j=>j.state==='pending'))timer=setTimeout(()=>void poll(),1500);
      }catch{if(live)setError('图片记录读取失败。');}
    };
    void poll();return ()=>{live=false;clearTimeout(timer);};
  },[base,enabled,version]);
  const refresh=()=>setVersion(x=>x+1);
  function gallery(turn:string|null){
    return jobs.filter(j=>j.turn===turn).map(j=><figure className="rpg-picture" key={j.id}>
      {j.state==='complete'&&<a href={archiveRoot()+'api/'+base+j.id} target="_blank" rel="noreferrer"><img loading="lazy" src={archiveRoot()+'api/'+base+j.id} alt={j.prompt}/></a>}
      <figcaption>{status[j.state]} · {j.actualModel||j.model}{j.costUsd!=null?' · '+(j.costKind==='actual'?'实际':'估算')+' '+j.costUsd+' 美元':''}</figcaption>
      <details><summary>画面描述</summary><p>{j.prompt}</p></details>
    </figure>);
  }
  return {
    toolbar:enabled&&<><button className="text-button" onClick={()=>setDialog({turn:null,source:'',focus:'回合剧情'})}>生成图片</button>{error&&<button className="text-button" onClick={refresh}>{error}重新读取</button>}</>,
    button:(turn:string)=>enabled&&<button className="text-button" onClick={e=>{
      setDialog(sceneFor(e.currentTarget.closest('article')?.querySelector<HTMLElement>('.model-output'),turn));
    }}>生成插画</button>,
    gallery:(turn:string|null)=>enabled&&gallery(turn),
    modal:enabled&&dialog&&<PictureDialog base={base} scene={dialog} close={()=>setDialog(null)} refresh={refresh}/>
  };
}
function PictureDialog({base,scene,close,refresh}:{base:string;scene:Scene;close:()=>void;refresh:()=>void}){
  const {turn,focus}=scene;
  const [source,setSource]=useState(scene.source),[writer,setWriter]=useState<Writer|null>(null);
  const [writing,setWriting]=useState(false),writeLock=useRef(false);
  const sourceLength=Array.from(source).length;
  const [models,setModels]=useState<Profile[]>([]),[model,setModel]=useState(''),[prompt,setPrompt]=useState('');
  const [params,setParams]=useState<Record<string,string>>({}),[catalog,setCatalog]=useState('正在读取可用模型…'),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),locked=useRef(false);
  useEffect(()=>{let live=true;api(base+'models').then((data:{status:string;profiles:Profile[];writer?:Writer|null})=>{if(!live)return;setWriter(data.writer??null);setModels(data.profiles);setModel(data.profiles[0]?.model_id||'');setCatalog(!data.profiles.length?'当前没有可用的文生图模型。':data.status==='stale'?'目录为缓存版本，生成时将重新校验。':'');}).catch(()=>{if(live)setCatalog('无法读取图片模型目录。');});return ()=>{live=false;};},[base]);
  const profile=models.find(m=>m.model_id===model);
  async function rewrite(){
    if(!turn||!writer?.enabled||!writer.remaining||writeLock.current||locked.current||!source.trim()||sourceLength>16000)return;
    writeLock.current=true;setWriting(true);setError('');
    try{
      const result=await api(base+'rewrite',{turn,source,focus,operationId:crypto.randomUUID()});
      setPrompt(result.prompt);
    }catch(e){
      const code=e instanceof ApiError?e.message:'';
      setError(code==='BUDGET_EXHAUSTED'?'改写额度已用完。':code==='REWRITE_MODEL_UNAVAILABLE'?'改写模型暂不可用。':
        '未取得可用的改写结果，已保留输入。请核对调用记录后再试。');
    }finally{
      try{const data=await api(base+'models');setWriter(data.writer??null);}catch{setWriter(null);}
      writeLock.current=false;setWriting(false);
    }
  }
  async function submit(){
    if(writeLock.current||locked.current||!profile||!prompt.trim()||Array.from(prompt).length>4000)return;
    locked.current=true;setBusy(true);setError('');
    try{
      await api(base+'jobs',{operationId:crypto.randomUUID(),turn,prompt:prompt.trim(),model,options:params});
      refresh();close();
    }catch(e){
      refresh();
      if(e instanceof ApiError&&e.status<500&&e.message!=='REQUEST_FAILED'){
        const reason:Record<string,string>={IMAGE_BUSY:'本会话已有图片正在生成。',IMAGE_MODEL_OR_OPTIONS_CHANGED:'模型或参数已变化，请重新打开窗口选择。',PLUGIN_NOT_AUTHORIZED:'本会话的生图授权已关闭。',PLUGIN_SESSION_BUSY:'请等待当前剧情保存后再生成。'};
        setError(reason[e.message]||'请求被拒绝，请核对模型与会话状态。');locked.current=false;
      }else setError('提交结果需要核对。请关闭窗口查看图片记录，系统不会自动重发。');
    }
    finally{setBusy(false);}
  }
  return <Modal title={turn?'制作本回合插画':'生成图片'} className="chat-dialog picture-dialog" close={()=>{if(!busy&&!writeLock.current)close();}}>
    <form onSubmit={e=>{e.preventDefault();void submit();}}>
      {turn&&<section>
        <label>取景原文 · {focus}<textarea rows={4} value={source} disabled={writing||busy} onChange={e=>setSource(e.target.value)}/></label>
        <small>{sourceLength}/16000 字{sourceLength>16000?' · 请缩短素材或选中关键镜头后重新打开':''}</small>
        <p>{writer?.enabled?'改写将把上方素材发送给 '+writer.model+'，单独调用一次文本模型。剩余 '+writer.remaining+' 次。':'宿主未启用文本改写，可直接填写下方提示词。'}</p>
        <button type="button" className="secondary" disabled={!writer?.enabled||!writer.remaining||!source.trim()||sourceLength>16000||writing||busy} onClick={rewrite}>{writing?'正在改写…':prompt?'重新改写并替换提示词':'改写提示词'}</button>
      </section>}
      <label>{turn?'绘图提示词':'画面描述'}<textarea disabled={writing||busy} autoFocus={!turn} rows={6} value={prompt} placeholder="描述人物、地点、光线和构图…" onChange={e=>setPrompt(e.target.value)}/></label>
      <small>{Array.from(prompt).length}/4000 字 · UTF-8 {new TextEncoder().encode(prompt).length} 字节</small>
      {turn&&<p>可直接填写或编辑绘图提示词。确认后，图片模型只接收这段提示词，成图保存在本回合下。</p>}
      <label>图片模型<select value={model} onChange={e=>{setModel(e.target.value);setParams({});}}>{models.map(m=><option key={m.model_id} value={m.model_id}>{m.display_name} · {m.model_id}</option>)}</select></label>
      {catalog&&<p role="status">{catalog}</p>}
      <div className="picture-options">{Object.entries(profile?.options??{}).map(([k,values])=><label key={k}>{labels[k]||k}<select value={params[k]||''} onChange={e=>{const next={...params};if(e.target.value)next[k]=e.target.value;else delete next[k];setParams(next);}}><option value="">模型默认</option>{values.map(v=><option key={v}>{v}</option>)}</select></label>)}</div>
      {profile&&<p>本次单独调用图片模型。{price(profile,params)}</p>}{error&&<p role="alert">{error}</p>}
      <footer><button type="button" className="secondary" disabled={busy||writing} onClick={close}>取消</button><button className="chat-primary" disabled={busy||writing||locked.current||!profile||!prompt.trim()||Array.from(prompt).length>4000}>{busy?'正在提交…':'确认生成 1 张'}</button></footer>
    </form></Modal>;
}
