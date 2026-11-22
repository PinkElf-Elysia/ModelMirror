const marker=/^(\{\{(?:user|char)\}\}):([ ]?)(.*)$/u;
const ambiguous=line=>/<START>|\{\{(?:char|user)\}\}:/iu.test(line)||/^[^\s:：]{1,32}[:：]/u.test(line);
function lineError(line,reason){return {line,reason};}
export function parseExamples(raw){
 const eol=raw.includes('\r\n')?'\r\n':'\n';
 if(/\r(?!\n)/.test(raw)||(eol==='\r\n'&&/(?<!\r)\n/.test(raw)))return lineError(1,'混合换行');
 const groups=[];if(!raw)return {groups,eol};
 let group=null,turn=null;
 for(const [i,line] of raw.split(eol).entries()){
  if(line==='<START>'){
   if(group&&!group.length)return lineError(i+1,'相邻分组之间缺少发言');
   group=[];groups.push(group);turn=null;continue;
  }
  const m=marker.exec(line);
  if(!group)return lineError(i+1,'缺少独占一行的 <START>');
  if(m){if(ambiguous(m[3]))return lineError(i+1,'正文含有说话人或分组标记');turn={role:m[1],space:m[2],body:m[3]};group.push(turn);}
  else if(turn&&!ambiguous(line))turn.body+='\n'+line;
  else return lineError(i+1,'说话人或分组标记有歧义');
 }
 if(!group.length)return lineError(raw.split(eol).length,'空分组');
 return {groups,eol};
}
export function serializeExamples(groups,eol){
 const lines=[];for(const group of groups){
  if(!group.length)throw Error('每组至少需要一条发言');
  lines.push('<START>');for(const t of group)lines.push(t.role+':'+t.space+t.body.replaceAll('\n',eol));
 }
 const raw=lines.join(eol),round=parseExamples(raw);
 if(!round.groups||JSON.stringify(round.groups)!==JSON.stringify(groups))throw Error('正文含有可能被解释为分组或说话人的标记，请用原文编辑。');
 return raw;
}
export function exampleDialog({dialog,read,write,confirm}){
 const doc=dialog.ownerDocument,el=(tag,label='')=>{const n=doc.createElement(tag);n.textContent=label;return n;};
 const find=id=>dialog.querySelector('#'+id);
 let original='',eol,groups,locked,pending=false;
 const move=(list,i,j)=>{[list[i],list[j]]=[list[j],list[i]];};
 function changed(){pending=true;find('sample-diff').hidden=true;find('sample-apply').disabled=true;}
 function control(box,label,fn){const b=el('button',label);b.type='button';b.disabled=locked;b.onclick=()=>{fn();changed();render();};box.append(b);}
 function render(){
  const box=find('sample-groups');box.replaceChildren();
  groups?.forEach((group,g)=>{
   const section=el('fieldset');section.append(el('legend','示例组 '+(g+1)));box.append(section);
   group.forEach((t,i)=>{
    const field=el('label','发言 '+(i+1)),who=el('select'),body=el('textarea');who.setAttribute('aria-label','发言者');
    for(const [v,n] of [['{{user}}','玩家'],['{{char}}','角色']]){const o=el('option',n);o.value=v;who.append(o);}
    who.value=t.role;who.disabled=body.readOnly=locked;body.value=t.body;body.rows=3;
    who.onchange=()=>{t.role=who.value;changed();};body.oninput=()=>{t.body=body.value;changed();};
    field.append(who,body);section.append(field);if(i)control(section,'上移发言 '+(i+1),()=>move(group,i,i-1));control(section,'删除发言 '+(i+1),()=>group.splice(i,1));
   });
   control(section,'添加发言',()=>group.push({role:'{{char}}',space:' ',body:''}));
   if(g)control(section,'上移示例组 '+(g+1),()=>move(groups,g,g-1));
   control(section,'删除示例组 '+(g+1),()=>groups.splice(g,1));
  });
  find('sample-add').disabled=locked||!groups;find('sample-review').disabled=locked||!groups;
 }
 const close=()=>{if(pending&&!confirm('放弃本次示例整理并保留原文？'))return;pending=false;dialog.close();};
 find('sample-cancel').onclick=close;dialog.oncancel=e=>{e.preventDefault();close();};
 find('sample-add').onclick=()=>{groups.push([{role:'{{user}}',space:' ',body:''}]);changed();render();};
 find('sample-restart').onclick=()=>{groups=[];find('sample-restart').hidden=true;changed();render();};
 find('sample-review').onclick=()=>{try{
  const next=serializeExamples(groups,eol);find('sample-after').textContent=next;find('sample-before').textContent=original;
  find('sample-diff').hidden=false;find('sample-apply').disabled=next===original;find('sample-status').textContent=next===original?'示例原文保持一致':'请核对两份原文，再采用整理结果。';
 }catch(e){find('sample-status').textContent=e.message;find('sample-apply').disabled=true;}};
 find('sample-apply').onclick=()=>{write(serializeExamples(groups,eol));pending=false;dialog.close();};
 return {get pending(){return pending;},open(readonly){
  original=read();const r=parseExamples(original);groups=r.groups;eol=r.eol??(original.includes('\r\n')?'\r\n':'\n');
  locked=readonly;pending=false;find('sample-restart').hidden=!!groups||locked;find('sample-status').textContent=groups?'':('第 '+r.line+' 行 · '+r.reason+'。原文可继续使用。');
  find('sample-apply').disabled=true;find('sample-diff').hidden=true;render();dialog.showModal();
 }};
}
