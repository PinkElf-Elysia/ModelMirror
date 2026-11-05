export const panelMessages = {
 PANEL_SKELETON_SHAPE:'初始数组中的每一项需符合首项的字段与类型；开放类型可使用 null 作为首项。',
 PANEL_UPDATE_MISSING:'本轮没有返回变量数据，保留上一份状态。',
 PANEL_UPDATE_FORMAT:'本轮变量格式不完整，保留上一份状态。',
 PANEL_DATA_TYPE:'本轮变量的类型与数据骨架不一致，保留上一份状态。',
 PANEL_DATA_FIELD_MISSING:'本轮变量缺少骨架字段，保留上一份状态。',
 PANEL_TEMPLATE_SYNTAX:'面板模板语法有误，请检查未闭合的表达式或区块。',
 PANEL_TEMPLATE_UNSUPPORTED:'模板使用了未支持的 helper、局部模板或原型属性。',
 PANEL_TEMPLATE_RENDER:'面板暂未渲染，请核对字段和模板。',
 PANEL_TEMPLATE_TIMEOUT:'面板渲染超时，请减少嵌套循环。',
 PANEL_TEMPLATE_CAPACITY:'面板超过渲染容量，请简化模板。',
 PANEL_TEMPLATE_REQUIRED:'请填写面板皮肤。',
 PANEL_OBJECT_REQUIRED:'数据骨架应为 JSON 对象，例如 {"地点":"码头"}。',
 AUTHORING_JSON_SYNTAX:'JSON 格式有误，请检查引号、逗号和括号。',
 AUTHORING_JSON_DUPLICATE_KEY:'JSON 中存在重复字段，请核对后重试。',
 PANEL_DATA_COMPLEX:'数据层级或字段过多，请简化骨架。',
 PANEL_DATA_CAPACITY:'变量数据超过 64 KiB，上一份状态已保留。',
};
export const panelProblem = code => panelMessages[code] || '变量面板需要核对（'+(code||'未知错误')+'），原始内容已保留。';
export function mountVariableFrame(host,prepared,{notice=()=>{}}={}) {
 const frame=document.createElement('iframe');frame.title='变量面板';frame.className='variable-frame';frame.sandbox='allow-scripts';frame.referrerPolicy='no-referrer';frame.setAttribute('allow',"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'");
 const summary=document.createElement('p'),state=prepared.manifest.variablePanel;
 summary.className='variable-state';summary.setAttribute('role','status');
 summary.textContent=state.diagnostic?panelProblem(state.diagnostic):({initial:'初始状态',applied:'状态已更新',unchanged:'状态无变化'}[state.status]||'状态已载入');
 const raw=document.createElement('details');raw.className='variable-data';const label=document.createElement('summary');label.textContent='查看状态数据';const pre=document.createElement('pre');pre.textContent=JSON.stringify(prepared.snapshot.data,null,2);raw.append(label,pre);
 const onMessage=async event=>{
  const m=event.data;if(event.source!==frame.contentWindow||m?.protocol!=='modelmirror.rpg.presentation/1'||m.token!==prepared.token)return;
  if(m.type==='height'&&Number.isFinite(m.payload))frame.style.height=Math.max(120,Math.min(6000,m.payload))+'px';
  if(m.type==='panel-copy'&&Number.isSafeInteger(m.payload?.id)&&typeof m.payload.text==='string'&&m.payload.text.length<=65536){
   let ok=false;try{await navigator.clipboard.writeText(m.payload.text);ok=true;notice('已复制。');}catch{notice('未能复制，请展开状态数据后手动复制。');}
   if(frame.isConnected)frame.contentWindow.postMessage({protocol:m.protocol,token:m.token,type:'panel-copied',payload:{id:m.payload.id,ok}},'*');
  }
 };
 window.addEventListener('message',onMessage);frame.src=prepared.frameUrl;host.replaceChildren(summary,frame,raw);
 return ()=>{window.removeEventListener('message',onMessage);frame.remove();};
}
