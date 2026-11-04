import {addBlockedWord,removeBlockedWord,checkPlayerInput} from './input-assistance.mjs';

const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const messages={BLOCKED_WORDS_LIMIT:'最多1000个屏蔽词，未添加。',BLOCKED_WORD_INVALID:'屏蔽词最多200个 Unicode 字符，未添加；输入已保留。',PLAYER_INPUT_LIMIT:'玩家输入无效或超过120000字符，未通过检查。'};
const example=tag=>'<suggested_questions>\n'+['第一个建议','第二个建议','第三个建议'].map((text,i)=>(tag?'<'+tag+'>':'')+(i+1)+'. '+text+(tag?'</'+tag+'>':'')).join('\n')+'\n</suggested_questions>';

export function createInputAssistanceUI({changed,toast}){
  let data={blockedWords:[],suggestions:false},blockedRoot=null,suggestionsRoot=null,expanded=false,draft='',issue='',composing=false;
  function renderBlocked(root=blockedRoot){
    blockedRoot=root;if(!root)return;
    root.innerHTML='<section class="field assistance-blocked"><h3>⊘ 屏蔽词</h3><p class="hint">如果用户输入的内容包含屏蔽词，其无法回复。</p><div class="assistance-word-entry"><input aria-label="屏蔽词" placeholder="在此处填写触发关键词" value="'+esc(draft)+'"><button type="button" class="primary" data-word-add>添加</button></div><p class="assistance-error" role="alert"'+(issue?'':' hidden')+'>'+esc(issue)+'</p><div class="assistance-words">'+data.blockedWords.map((word,index)=>'<span class="assistance-word"><span>'+esc(word||'（空词，不参与匹配）')+'</span><button type="button" data-word-remove="'+index+'" aria-label="移除屏蔽词 '+(index+1)+'" title="移除所有完全相同的词">×</button></span>').join('')+'</div><p class="hint assistance-rule">区分大小写，保留空格。发送前检查当前玩家输入；可先点“检查输入”核对匹配。</p></section>';
    const input=root.querySelector('input');
    input.oninput=()=>{draft=input.value;};
    input.oncompositionstart=()=>{composing=true;};input.oncompositionend=()=>{composing=false;draft=input.value;};
    const add=()=>{const result=addBlockedWord(data.blockedWords,input.value);if(result.issue){issue=messages[result.issue];renderBlocked();blockedRoot.querySelector('input').focus();return;}if(input.value==='')return;data.blockedWords=result.words;draft='';issue='';changed();renderBlocked();blockedRoot.querySelector('input').focus();};
    input.onkeydown=event=>{if(event.key==='Enter'&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();add();}};
    root.querySelector('[data-word-add]').onclick=()=>{if(!composing)add();};
    root.querySelectorAll('[data-word-remove]').forEach(button=>button.onclick=()=>{data.blockedWords=removeBlockedWord(data.blockedWords,data.blockedWords[Number(button.dataset.wordRemove)]);issue='';changed();renderBlocked();blockedRoot.querySelector('input').focus();});
  }
  function renderSuggestions(root=suggestionsRoot){
    suggestionsRoot=root;if(!root)return;
    root.innerHTML='<section class="assistance-suggestions"><div class="field-row"><div><h3>▣ 下一步问题建议</h3><p class="hint">设置下一步问题建议可以让用户更好地对话。</p></div><label class="switch-label"><input type="checkbox" role="switch" aria-label="下一步问题建议"'+(data.suggestions?' checked':'')+'></label></div><p>匹配回复中的下一步建议，并渲染为按钮，供用户快速点击。<button type="button" class="text-button" aria-expanded="'+expanded+'" data-suggestions-help>'+ (expanded?'收起':'展开')+'</button></p><div class="assistance-examples"'+(expanded?'':' hidden')+'><p>选项整体需要用 &lt;suggested_questions&gt; 标签包裹，每块至少3项。点击只填入输入框。</p>'+['', 'p','li','span'].map(tag=>'<pre>'+esc(example(tag))+'</pre>').join('')+'<p>也支持 A. 至 E. 或短横线后一个空格。选项顶格书写，不插入空行；短标签须配对且不带属性。</p><p class="hint">本地约定：允许非空短建议；单次回复最多20项，每项2000字符。非法块保留并提示，超限不截取；代码示例不转为按钮。原始回复始终保留。</p></div></section>';
    root.querySelector('input').onchange=event=>{data.suggestions=event.target.checked;changed();};
    root.querySelector('[data-suggestions-help]').onclick=()=>{expanded=!expanded;renderSuggestions();suggestionsRoot.querySelector('[data-suggestions-help]').focus();};
  }
  function check(input){
    const result=checkPlayerInput(input,data.blockedWords);
    return {...result,message:result.issue?messages[result.issue]:result.allowed?'检查通过：当前原始输入未命中屏蔽词。尚未发送。':'命中屏蔽词：'+result.matches.map(({word})=>JSON.stringify(word)).join('、')+'。实际发送时应阻断；本次仅离线检查。'};
  }
  return {load(value){data={blockedWords:[...value.blockedWords],suggestions:value.suggestions};draft='';issue='';expanded=false;},read:()=>structuredClone(data),renderBlocked,renderSuggestions,check};
}
