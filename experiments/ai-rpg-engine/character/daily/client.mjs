// Browser helpers shared by both existing chat pages. No model calls here.
export function renderRich(document,row,message){
 const body=document.createElement('div');body.className='daily-rich';if(typeof message.html==='string')body.innerHTML=message.html;else body.textContent=message.content;row.append(body);
 const details=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='查看原文';raw.textContent=message.content;const copy=document.createElement('button');copy.type='button';copy.textContent='复制原文';copy.onclick=async()=>{try{await document.defaultView.navigator.clipboard.writeText(message.content);copy.textContent='已复制';}catch{copy.textContent='复制失败，请选择上方原文复制';}};details.append(summary,raw,copy);row.append(details);
}
export async function playerPicker(document,api,nameId){
 const name=document.getElementById(nameId);if(!name)return ()=>({id:null,name:'玩家'});
 const label=document.createElement('label');label.textContent='玩家档案';const select=document.createElement('select');select.id=nameId+'-profile';label.append(select);name.closest('label').before(label);
 const initial=document.createElement('option');initial.value='';initial.textContent='临时身份';select.append(initial);
 let profiles=[];try{const result=await api('/profiles');profiles=Array.isArray(result)?result:[];}catch{}
 for(const p of profiles){const o=document.createElement('option');o.value=p.profileId;o.textContent=p.name+' · 修订 '+(p.profileRevision+1);select.append(o);}
 const descriptionLabel=document.createElement('label');descriptionLabel.textContent='公开人物描述';const description=document.createElement('textarea');description.id=nameId+'-description';description.maxLength=8000;description.rows=3;description.placeholder='可选，只描述你的身份，不填写已经发生的行动。';descriptionLabel.append(description);label.after(descriptionLabel);
 select.onchange=()=>{const p=profiles.find(p=>p.profileId===select.value);name.readOnly=description.readOnly=!!p;if(p){name.value=p.name;description.value=p.description;}else{description.value='';}name.onchange?.();};description.onchange=()=>name.onchange?.();
 return ()=>{const p=profiles.find(p=>p.profileId===select.value);return p?{profileId:p.profileId,revision:p.profileRevision}:{name:name.value||'玩家',description:description.value,avatar:null};};
}
