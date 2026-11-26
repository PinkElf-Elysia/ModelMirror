export function mountNarrationPanel({$,el,api,crypto,run,notice,apply,sync}) {
  const panel=$('narration-panel'),archive=$('round-history-panel'),input=$('narration-text');
  if(!panel || !archive)return {update(){}};
  let state=null,key='',cursor=null,epoch=0,intent=null;
  const status={ready:'待发言',waiting:'未发言',stopped:'已停止',completed:'已完成',blocked:'已阻止',changed:'成员或状态已变化',failed:'失败',unknown:'结果未知',dispatching:'等待回执',prepared:'已准备'};
  async function history(append=false){
    const generation=++epoch;
    if(!state || !archive.open)return;
    try{
      const result=await api('/api/groups/'+state.id+'/round-history'+(append?'?before='+encodeURIComponent(cursor):''));
      if(generation!==epoch || !archive.open)return;
      if(result.revision>state.revision || (!append && result.items[0]?.status!==state.round?.status)){
        await sync(state.id);
        if(generation!==epoch || !archive.open)return;
      }
      const list=$('round-archive');
      if(!append)list.replaceChildren();
      if(!result.items.length)list.append(el('p','还没有已启动的轮次。'));
      for(const r of result.items){
        const section=el('section');section.append(el('strong',status[r.status]??r.status));
        if(r.estimate)section.append(el('p','启动时的整轮上界 · '+r.estimate.calls+' 次 · US$'+(r.estimate.microUsd/1e6).toFixed(6)));
        if(r.input!==null)section.append(el('p','玩家 · '+r.input));
        for(const step of r.steps){
          const index=state.members.findIndex(m=>m.id===step.memberId);
          section.append(el('p',(state.members[index]?.name??'成员')+'（'+index+'） · '+(status[step.status]??step.status)+(step.content===null?'':'：'+step.content)));
        }
        list.append(section);
      }
      cursor=result.before;$('round-older').hidden=!cursor;
    }catch(error){if(generation===epoch)notice(error);}
  }
  archive.ontoggle=()=>history();
  $('round-older').onclick=()=>history(true);
  input.oninput=()=>{intent=null;};
  $('narration-form').onsubmit=event=>{
    event.preventDefault();
    if(!state || !input.value.trim())return;
    const id=state.id;
    intent??={operationId:crypto.randomUUID(),expectedRevision:state.revision,expectedNarrationRevision:state.narrationRevision,content:input.value};
    run(async()=>{try{const next=await api('/api/groups/'+id+'/narrate',intent);
      if(state?.id===id){apply(next);input.value='';intent=null;}
    }catch(error){if(/REVISION_CONFLICT/.test(error.message))intent=null;throw error;}});
  };
  return {update(next,enabled,busy){
    if(next?.id!==state?.id){++epoch;archive.open=false;panel.open=false;input.value='';intent=null;$('round-archive').replaceChildren();}
    state=next;archive.hidden=!next||!enabled;panel.hidden=archive.hidden||!Number.isInteger(next?.narrationRevision);
    const locked=busy || next?.requests.some(r=>['prepared','dispatching','unknown'].includes(r.status)) ||
      (next?.round && !['completed','stopped','failed','blocked','changed'].includes(next.round.status));
    input.disabled=$('narration-save').disabled=!!locked;
    const nextKey=[next?.id,next?.revision,next?.round?.id,next?.round?.status].join('/');
    if(key!==nextKey){key=nextKey;history();}
  }};
}
