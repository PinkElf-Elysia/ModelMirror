export function mountRoundPanel({document,window,api,crypto,rules,refresh}) {
  const $=id=>document.getElementById(id);
  const section=$('round-controls'), message=$('round-message'), start=$('round-start');
  const resume=$('round-resume'), stop=$('round-stop'), note=$('round-note');
  let group=null, round=null, enabled=false, running=false, stopping=false, epoch=0;
  const states={ready:'等待继续',prepared:'已预留，尚未派发',dispatching:'正在等待回执',unknown:'结果未知，保留预留',
    failed:'请求失败，本轮结束',completed:'本轮已完成',stopped:'已停止后续发言',changed:'群状态已变化，请重新预览',blocked:'本轮已停止'};
  const reasons={GROUP_BUDGET_EXHAUSTED:'群预算不足',GROUP_TASK_BUDGET_EXHAUSTED:'共享额度不足',GROUP_PRICE_UNKNOWN:'价格上界未知',
    GROUP_CONTEXT_CAPACITY:'上下文容量不足',GROUP_REVISION_CONFLICT:'群修订已变化',GROUP_REQUEST_PENDING:'有请求待核对'};
  const terminal=r=>!r || ['failed','completed','stopped','changed','blocked'].includes(r.status);
  const path=(id,action)=>'/api/groups/'+id+'/round-'+action;
  function paint() {
    section.hidden=!enabled || !group;
    if(section.hidden)return;
    const plan=rules(), mentions=$('turn-mode').value==='mentions';
    $('round-message-label').hidden=mentions;
    const text=mentions?plan?.text:message.value;
    start.disabled=running || stopping || !terminal(round) || !plan || !text?.trim() ||
      group.requests.some(r=>['dispatching','unknown','prepared'].includes(r.status));
    resume.hidden=running || stopping || round?.status!=='ready';
    stop.hidden=terminal(round); stop.disabled=stopping;
    const labels=(round?.steps??[]).map(step=>{
      const cast=group.members.filter(m=>m.kind==='character'), i=cast.findIndex(m=>m.id===step.memberId);
      return (cast[i]?.name??'成员')+' · '+(i+1)+'（'+({waiting:'待回应',completed:'已回应',failed:'失败',unknown:'待核对',dispatching:'已派发',prepared:'已预留'}[step.status]??step.status)+'）';
    });
    $('round-state').textContent=round ? ((running && round.status==='ready'?'正在串行回应':states[round.status])+
      (reasons[round.reason]?' · '+reasons[round.reason]:'')+'：'+labels.join('、')) : '';
  }
  function pause() { epoch++;running=false;stopping=false;paint(); }
  // Refresh, navigation or unloading only discards the tab's permission to advance.
  for(const id of ['refresh','new','home','history'])$(id).addEventListener(id==='history'?'change':'click',pause,true);
  window.addEventListener('pagehide',pause);
  function update(view,available) {
    enabled=available;
    if(group?.id!==view?.id){pause();message.value='';note.textContent='';}
    group=view;round=view?.round??null;paint();
  }
  async function drive(begin) {
    if (running || !group || !enabled) return;
    const id=group.id,ticket=++epoch, plan=rules();
    const valid=()=>ticket===epoch && group?.id===id;
    running=true;note.textContent='';paint();
    try {
      if(begin){
        const result=await api(path(id,'start'),{operationId:crypto.randomUUID(),previousRoundId:round?.id??null,
          rules:plan,message:plan.mode==='mentions'?plan.text:message.value});
        if(!valid())return;
        round=result.round;paint();
        if(result.duplicate)return;
      }
      const owner=round.id;
      while(valid() && round?.id===owner && round.status==='ready'){
        const step=await api(path(id,'step'),{roundId:owner,index:round.next,expectedRevision:round.revision});
        if(!valid())break;
        round=step;
        await refresh(id);
        if(step.status!=='ready')break;
      }
    }catch(error){
      if(valid()) note.textContent=reasons[error.message]??'本轮已暂停，请查看最新状态。';
    }finally{
      if(ticket===epoch)running=false;
      if(group?.id===id) {
        try { await refresh(id); } catch {note.textContent='暂时无法读取原状态，请刷新核对。';}
      }
      paint();
    }
  }
  start.onclick=()=>drive(true); resume.onclick=()=>drive(false);
  stop.onclick=async()=>{
    const id=group.id,roundId=round.id;
    pause();stopping=true;paint();
    try { await api(path(id,'stop'),{roundId}); await refresh(id); }
    catch {if(group?.id===id)note.textContent='停止结果待核对，请刷新原群。';}
    finally {stopping=false;paint();}
  };
  message.oninput=paint;
  return {update,paint};
}
