/** Presentation only: never write this result to history, receipts or context. */
export function messagePresentation(message){
 const raw=message.content;
 if(message.source.kind!=='model-result')return {text:raw,changed:false};
 // Narrowly repair prose paragraphs, not code/paths/URLs or structured output.
 // Single escapes and double-escaped backslashes are intentionally ambiguous.
 if(!/(?<!\\)(?:\\r\\n|\\n){2,}/.test(raw)||/`|~~~|https?:\/\/|\b[A-Za-z]:\\|\\\\/.test(raw))return {text:raw,changed:false};
 try{JSON.parse(raw);return {text:raw,changed:false};}catch{}
 if(/["']\\(?:r\\n|n)/.test(raw))return {text:raw,changed:false};
 const text=raw.replace(/(?<!\\)(?:\\r\\n|\\n)/g,'\n');
 return {text,changed:text!==raw};
}
export function renderMessageBody(document,row,message){
 const view=messagePresentation(message),body=document.createElement('p');body.textContent=view.text;row.append(body);
 if(!view.changed)return;
 const note=document.createElement('small');note.textContent='已按换行显示；原始响应未修改。';
 const toggle=document.createElement('button');toggle.type='button';toggle.textContent='查看原始响应';toggle.setAttribute('aria-pressed','false');
 let raw=false;toggle.onclick=()=>{raw=!raw;body.textContent=raw?message.content:view.text;toggle.textContent=raw?'按换行显示':'查看原始响应';toggle.setAttribute('aria-pressed',String(raw));note.textContent=raw?'正在显示原始响应。':'已按换行显示；原始响应未修改。';};
 row.append(note,toggle);
}
