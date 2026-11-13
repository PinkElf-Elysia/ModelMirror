// P0 interaction prototype. Fixed fixtures only; no API or provider calls.
const $=(s,r=document)=>r.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=v=>structuredClone(v),key='modelmirror:rpg-generation-p0:v1';
const purposes={story:'剧情',summary:'摘要',memory:'记忆'};
const fieldNames={temperature:'温度',topP:'Top P',maxOutputTokens:'最大输出长度',topK:'Top K',frequencyPenalty:'频率惩罚',presencePenalty:'存在惩罚',stop:'停止词',seed:'Seed',thinkingEnabled:'启用思考',thinkingEffort:'思考档位',thinkingBudget:'思考预算'};
const numeric=(min,max,step,def)=>({type:'number',min,max,step,def});
const basic={temperature:numeric(0,2,.05,.7),topP:numeric(0,1,.05,.8),maxOutputTokens:numeric(256,32768,256,8192)};
const models=[
{id:'gemini',name:'Gemini 3.8 Flash',family:'Gemini',note:'均衡叙事 · 示例能力',fields:{...basic,topK:numeric(1,100,1,40),stop:{type:'list',def:[]},seed:numeric(0,999999,1,0),thinkingEnabled:{type:'boolean',def:false},thinkingEffort:{type:'enum',values:['low','medium','high'],def:'medium'},thinkingBudget:numeric(0,16384,128,4096)}},
{id:'opus',name:'Claude Opus 4.6',family:'Claude',note:'细节与人物 · 示例能力',exclusive:true,fields:{...basic,temperature:numeric(0,1,.05,.7),stop:{type:'list',def:[]},thinkingEnabled:{type:'boolean',def:false},thinkingEffort:{type:'enum',values:['low','medium','high'],def:'medium'},thinkingBudget:numeric(1024,16384,128,4096)}},
{id:'deepseek',name:'DeepSeek V4.1 Flash',family:'DeepSeek',note:'快速试写 · 示例能力',fields:{...basic,frequencyPenalty:numeric(-2,2,.1,0),presencePenalty:numeric(-2,2,.1,0),stop:{type:'list',def:[]},thinkingEnabled:{type:'boolean',def:false}}},
{id:'unknown',name:'待确认能力的模型',family:'其他',note:'需先由控制面确认，不能选择',fields:{},unavailable:true}];
const model=id=>models.find(x=>x.id===id)||models[0];
const baseConfig=p=>({model:'gemini',explicit:p==='story'?{temperature:.7,topP:.8,maxOutputTokens:8192}:{temperature:.2,topP:.8,maxOutputTokens:16384}});
const initialInput='我把旧船票递给守灯人，询问信封上的潮汐印记意味着什么。';
const fresh=()=>({version:1,mode:'creator',scenario:'success',m2:false,m3:true,backfill:false,plugins:{model:true,parameters:true,comparison:true},configs:{creator:{story:baseConfig('story'),summary:baseConfig('summary'),memory:baseConfig('memory')},chat:{story:baseConfig('story'),summary:baseConfig('summary'),memory:baseConfig('memory')}},presets:[],recommendations:{},drafts:{creator:initialInput,chat:initialInput},history:{creator:[],chat:[]},batches:{creator:[],chat:[]}});
let state;try{state=JSON.parse(localStorage.getItem(key));if(state?.version!==1||!state.configs?.creator||!state.batches?.chat)state=fresh();}catch{state=fresh();}
let view='main',selected=0,pair=1,sideBySide=true,raw=new Set(),timers=new Set(),activeSetup=null,param=null,viewingIndex=null,toastTimer;
const stack=[];
function save(){try{localStorage.setItem(key,JSON.stringify(state));}catch{toast('本页存储空间不足，刷新后可能无法恢复。');}}
function toast(t){const el=$('#toast');el.textContent=t;el.style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.style.display='none',3800);}
function latest(){return state.batches[state.mode].at(-1);}
function settled(b){return b&&b.pre!=='unknown'&&b.candidates.every(c=>['done','failed','stopped'].includes(c.status))&&!['running','unknown'].includes(b.post);}
function blocking(){const b=latest();return b&&(!settled(b)||(!b.adopted&&!b.discarded));}
function config(){return state.mode==='chat'&&!state.plugins.parameters?{...baseConfig('story'),model:state.plugins.model?state.configs.chat.story.model:'gemini'}:state.mode==='chat'&&!state.plugins.model?{...clone(state.configs.chat.story),model:'gemini'}:state.configs[state.mode].story;}
function brief(c){return Object.entries(c.explicit).map(([k,v])=>fieldNames[k]+' '+(Array.isArray(v)?v.join(' / '):v)).join(' · ')||'使用模型默认值';}
function button(label,action,attrs='',cls=''){return '<button class="'+cls+'" data-action="'+action+'" '+attrs+'>'+label+'</button>';}
function chip(s){const names={queued:'排队中',running:'生成中',done:'已完成',failed:'生成失败',unknown:'结果待核对',stopped:'未派发'};return '<span class="tag '+(['running','queued','unknown'].includes(s)?'waiting':s==='failed'?'error':'')+'">'+(names[s]||s)+'</span>';}
function sceneBody(index=0){
 const text=[
 ['守灯人没有接过船票。他先擦去铜灯罩上的水汽，让一束淡金色的光落在那枚印记上。','“这不是邮戳，”他说，“是退潮时才能认出的路标。”','窗外传来两短一长的汽笛声。他从抽屉里找出一张旧海图，把折角压在你的船票旁。灯塔以北的浅滩上，有一处被墨水涂掉的名字。'],
 ['守灯人的指尖停在潮汐印记旁。纸张已经很旧，边缘却留着一圈新鲜的盐渍。','“拿这封信来的人，通常都错过了最后一班船。”','他推开窗，海雾顺着窗台漫进来。远处码头的灯依次熄灭，只剩一盏仍在闪烁。“等第三次汽笛。到那时候，你再决定要不要走。”'],
 ['那枚印记在灯光下显出蓝绿色的纹理。守灯人从衣袋里摸出一枚同样颜色的玻璃片，轻放在桌上。','“有人替你付过船费了。”','他不肯说出那个人的名字，只将玻璃片推到你的面前。窗外的潮水正缓慢退去，露出一条通往旧码头的石阶。'],
 ['守灯人把船票翻到背面，露出一行被海水浸淡的小字。','“先认清你要去的地方，再问印记的意义。”','他把笔递给你。海图上两条航线一明一暗，你尚未落笔，窗外便响起了一声悠长的汽笛。']][index%4];
 return '<div class="story">'+text.map((p,i)=>'<p'+(i===1?' class="dialogue"':'')+'>'+p+'</p>').join('')+'<details><summary>场景记录 · 灯塔</summary><p>时间：傍晚<br>地点：旧灯塔<br>线索：潮汐印记</p></details></div>';
}
function recordHtml(){return state.batches[state.mode].map((b,i)=>'<div class="record-row"><div><strong>对比 '+(i+1)+' · '+b.candidates.length+' 份候选</strong><p>'+(b.adopted?'已采用 '+b.adopted:b.discarded?'都未采用':b.candidates.filter(c=>c.status==='done').length+' 份已完成，等待选择')+' · '+(settled(b)?'任务已收束':'仍有任务待收束')+'</p></div>'+button('查看对比','view-batch','data-index="'+i+'"')+'</div>').join('');}
function render(){
 document.body.dataset.mode=state.mode;document.querySelectorAll('.mode-tabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode)));
 $('#scenario').value=state.scenario;$('#m2').checked=state.m2;$('#m3').checked=state.m3;$('#backfill').checked=state.backfill;
 if(view==='comparison'){renderComparison();return;}
 const chat=state.mode==='chat',cfg=config(),blocked=blocking(),b=latest(),recommendation=state.recommendations.story;
 const editor='<aside class="editing-side"><div class="section-nav"><strong>基础设置</strong><span>世界书设置</span><span>其他设置</span></div><span class="eyebrow">YOUR STORY</span><h2 class="work-title">雾港来信</h2><p class="description">一封没有寄件人的信，把你带回了离开多年的港口。</p><div class="divider"></div><div class="row spread"><h3>作品摘要</h3><span class="tag">本地草稿</span></div><div class="document-fragment"><h3>主提示词 <small>system</small></h3><p>以细腻、克制的叙事呈现雾港。推进人物与环境的变化，保留玩家的决定权。</p></div><div class="document-fragment"><h3>开局页面 <small>HTML / CSS</small></h3><div class="code-lines">&lt;section class="harbor"&gt;<br>  &lt;h1&gt;雾港来信&lt;/h1&gt;<br>  &lt;p&gt;潮水会记住每一个名字。&lt;/p&gt;<br>&lt;/section&gt;</div></div><div class="document-fragment"><h3>作品推荐配置</h3><p>'+(recommendation?esc(model(recommendation.model).name)+'<br>'+esc(brief(recommendation)):'尚未设置。调试满意后，可从参数面板明确保存。')+'</p></div><p class="notice-small">此处为背景样例，本批验收模型、参数和对比交互。</p></aside>';
 const composer='<div class="composer"><label class="eyebrow" for="story-input">你的下一步</label><textarea id="story-input" aria-label="剧情输入" placeholder="写下角色接下来的行动…">'+esc(state.drafts[state.mode])+'</textarea><div class="composer-footer"><small>'+(blocked?'本轮任务尚未结束':'Shift + Enter 换行')+'</small><div class="row">'+((!chat||state.plugins.comparison)?button(chat?'本轮对比':'对比生成','setup',blocked?'disabled':''):'')+button('发送','send',blocked?'disabled':'','primary')+'</div></div></div><div class="effective"><span>'+esc(model(cfg.model).name)+' · '+esc(brief(cfg))+'</span></div>';
 const content='<div class="scene"><div class="scene-banner"><span class="eyebrow">CHAPTER 01 · THE OLD LIGHTHOUSE</span><h2>潮汐之间</h2><p>傍晚 / 旧灯塔 / 第 '+(state.history[state.mode].length+1)+' 回合</p></div>'+(state.history[state.mode].length?state.history[state.mode].map(x=>sceneBody(x.index)).join(''):'<div class="story"><p>你推开灯塔底层的木门。铜铃轻响，空气里混着海盐与旧纸的气味。</p><p class="dialogue">“你终于来了。”守灯人抬起头，目光落在你手里的信封上。</p><p>他身后的窗边，潮汐钟比正常时间慢了整整一刻。</p></div>')+'</div><div class="records">'+recordHtml()+'</div>'+(blocked?'<div class="inline-notice">'+(b?.adopted?'已采用 '+b.adopted+'。其余候选或后台任务收束后即可继续。':'请先处理本轮对比结果，再继续发送。')+'</div>':'')+composer;
 $('#app').innerHTML='<main class="shell '+(chat?'chat-shell':'')+'"><header class="workspace-header"><div class="brand"><div class="brandmark">M</div><div><div class="breadcrumbs">RPG / '+(chat?'雾港来信':'创作工作台')+'</div><h1>'+(chat?'雾港来信':'雾港来信 · 创作工作台')+'</h1></div></div><div class="preview-actions">'+(chat?button('会话设置','plugins')+((state.plugins.parameters||state.plugins.model)?button('模型与参数','params'):''):'<span class="context-note">当前调试会话 · 不影响已发布作品</span>')+'</div></header>'+(chat?'<div class="chat-body">'+content+'</div>':'<div class="creator-layout">'+editor+'<section class="preview-side"><div class="row spread preview-heading"><h2>调试与预览</h2><div class="preview-actions">'+button(esc(model(cfg.model).name)+' ⌄','params','','config-chip')+button('模型与参数','params')+'</div></div>'+content+'</section></div>')+'</main>';
}
function modal(title,body,footer,cls='',onClose=null){
 const el=document.createElement('dialog');el.className=cls;el.setAttribute('aria-label',title);
 el.innerHTML='<div class="dialog-layout"><header class="dialog-head"><div><h2>'+esc(title)+'</h2></div>'+button('×','close','aria-label="关闭'+esc(title)+'"','icon ghost')+'</header><div class="dialog-content">'+body+'</div><footer class="dialog-foot">'+footer+'</footer></div>';
 $('#overlays').append(el);const item={el,onClose};stack.push(item);el.addEventListener('cancel',e=>{e.preventDefault();requestClose(item);});el.showModal();return item;
}
function close(item=stack.at(-1)){if(!item)return;const i=stack.indexOf(item);if(i<0)return;stack.splice(i,1);item.el.close();item.el.remove();}
function requestClose(item){if(item.onClose)item.onClose();else close(item);}
function confirmDialog(title,message,fn,accept='确认'){
 const d=modal(title,'<p>'+esc(message)+'</p>','<span></span><div class="row">'+button('取消','close')+button(accept,'confirm','','primary')+'</div>','compact');
 $('[data-action=confirm]',d.el).addEventListener('click',()=>{close(d);fn();});
}
function errors(cfg){
 const m=model(cfg.model),out=[];
 for(const [k,v]of Object.entries(cfg.explicit)){const f=m.fields[k];if(!f){out.push({key:k,text:fieldNames[k]+' 不在当前模型能力中；原值已保留。'});continue;}
 if(f.type==='number'&&(!Number.isFinite(v)||v<f.min||v>f.max||(f.step>=1&&!Number.isInteger(v))))out.push({key:k,text:fieldNames[k]+' 应在 '+f.min+'–'+f.max+' 之间'+(f.step>=1?'且为整数':'')+'。'});
 if(f.type==='list'&&(!Array.isArray(v)||v.length>4||v.some(x=>!x||x.length>80)))out.push({key:k,text:'停止词最多四项，每项 1–80 字符。'});}
 if(m.exclusive&&'temperature'in cfg.explicit&&'topP'in cfg.explicit)out.push({key:'topP',text:'示例能力要求温度与 Top P 二选一，请将其中一项恢复默认。'});
 if(cfg.explicit.thinkingEnabled===true){const max=cfg.explicit.maxOutputTokens??m.fields.maxOutputTokens.def,budget=cfg.explicit.thinkingBudget??m.fields.thinkingBudget?.def;if(budget!==undefined&&budget>=max)out.push({key:'thinkingBudget',text:'思考预算需要小于最大输出长度。'});}
 else if('thinkingBudget'in cfg.explicit||'thinkingEffort'in cfg.explicit)out.push({key:'thinkingBudget',text:'设置思考档位或预算前，请显式启用思考。'});
 return out;
}
function paramCfg(){return param.drafts[param.purpose];}
function ordered(v){return Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;}
function dirty(){return JSON.stringify(ordered(param.drafts))!==JSON.stringify(ordered(param.initial));}
function openParams(candidateIndex=null){
 const target=candidateIndex!==null,drafts=target?{story:clone(activeSetup[candidateIndex])}:clone(state.configs[state.mode]);
 param={purpose:'story',drafts,initial:clone(drafts),candidateIndex,advanced:false,item:null};
 param.item=modal(target?'候选参数':'模型与参数','','','parameters',()=>{if(dirty())confirmDialog('放弃未应用的修改？','已应用的配置会保留。当前面板中的未应用修改将丢弃。',()=>{close(param.item);param=null;},'放弃修改');else{close(param.item);param=null;}});
 renderParams();
}
function renderParams(){
 const cfg=paramCfg(),m=model(cfg.model),target=param.candidateIndex!==null,layout=$('.dialog-layout',param.item.el);
 $('.purpose-tabs',layout)?.remove();
 if(!target){const t=document.createElement('nav');t.className='purpose-tabs';t.setAttribute('aria-label','参数用途');t.innerHTML=Object.entries(purposes).map(([id,label])=>button(label,'purpose','data-purpose="'+id+'" aria-pressed="'+(param.purpose===id)+'"',param.purpose===id?'active':'')).join('');$('.dialog-head',layout).after(t);}
 const canChoose=state.mode==='creator'||target||state.plugins.model;
 $('.dialog-content',layout).innerHTML='<p class="purpose-note">'+(target?'本批候选专用，采用后不改变会话默认配置。':'仅影响当前'+(state.mode==='creator'?'调试':'游玩')+'会话的'+purposes[param.purpose]+'。三个用途分别保存。')+'</p>'+button('<span><strong>'+esc(m.name)+'</strong><small>'+esc(m.note)+'</small></span><span>⌄</span>','pick-model',canChoose?'':'disabled','model-choose')+(!canChoose?'<p class="notice-small">更换默认模型需先启用模型选择插件。</p>':'')+'<div class="preset-row">'+button('选择预设 ⌄','presets')+button('另存为预设','save-preset','','text-button')+'</div><div id="param-errors"></div>'+Object.keys(basic).filter(k=>m.fields[k]).map(k=>fieldHtml(k,m.fields[k],cfg)).join('')+'<details class="advanced" '+(param.advanced?'open':'')+'><summary>高级参数 <small>按所选模型能力显示</small></summary>'+Object.entries(m.fields).filter(([k])=>!(k in basic)).map(([k,f])=>fieldHtml(k,f,cfg)).join('')+'</details>'+(state.mode==='creator'&&!target?'<div class="divider"></div>'+button('设为作品推荐配置','recommend','','text-button')+'<p class="notice-small">只保存推荐值，既有会话保持原配置。</p>':'')+'<p class="notice-small">P0 使用示例能力，正式范围与默认策略以控制面为准。</p>';
 $('.dialog-foot',layout).innerHTML='<small id="param-dirty">尚未修改</small><div class="row">'+button('取消','close')+button(target?'应用到候选':'应用到当前会话','apply-params','','primary')+'</div>';
 $('.advanced',layout).addEventListener('toggle',e=>{if(param)param.advanced=e.target.open;});updateParamValidation();
}

function fieldHtml(k,f,cfg){
 const explicit=Object.hasOwn(cfg.explicit,k),v=explicit?cfg.explicit[k]:f.def,id='param-'+k;
 const head='<div class="param-row" data-field="'+k+'"><div class="param-label"><label for="'+id+'">'+fieldNames[k]+'</label><label class="default-toggle"><input type="checkbox" data-default="'+k+'" '+(!explicit?'checked':'')+'>使用默认值</label></div>';
 let body='';
 if(f.type==='number')body='<div class="param-inputs"><input aria-label="'+fieldNames[k]+'滑杆" data-range="'+k+'" type="range" min="'+f.min+'" max="'+f.max+'" step="'+f.step+'" value="'+v+'" '+(!explicit?'disabled':'')+'><input id="'+id+'" aria-label="'+fieldNames[k]+'" data-number="'+k+'" type="number" min="'+f.min+'" max="'+f.max+'" step="'+f.step+'" value="'+v+'" '+(!explicit?'disabled':'')+'></div><div class="param-help"><span>'+f.min+' — '+f.max+'</span><span>'+(k==='maxOutputTokens'||k==='thinkingBudget'?'token':'')+'</span></div>';
 if(f.type==='boolean')body='<label class="row"><input id="'+id+'" data-boolean="'+k+'" type="checkbox" '+(v?'checked':'')+' '+(!explicit?'disabled':'')+'>在回复前进行思考</label>';
 if(f.type==='enum')body='<select id="'+id+'" data-enum="'+k+'" '+(!explicit?'disabled':'')+'>'+f.values.map(x=>'<option '+(x===v?'selected':'')+' value="'+x+'">'+({low:'低',medium:'中',high:'高'}[x]||x)+'</option>').join('')+'</select>';
 if(f.type==='list')body='<div class="stop-tags">'+(v||[]).map((s,i)=>'<span>'+esc(s)+button('×','remove-stop','data-index="'+i+'" aria-label="删除停止词 '+esc(s)+'" '+(!explicit?'disabled':''))+'</span>').join('')+'</div><div class="stop-input"><input id="'+id+'" aria-label="新停止词" placeholder="输入停止词，按 Enter 添加" maxlength="80" '+(!explicit?'disabled':'')+'>'+button('添加','add-stop',(!explicit||v.length>=4)?'disabled':'')+'</div><small>最多 4 项；每项保留原始文本。</small>';
 return head+body+'</div>';
}
function updateParamValidation(){
 const issues=errors(paramCfg()),root=param.item.el,noPermission=state.mode==='chat'&&param.candidateIndex===null&&!state.plugins.parameters;
 $('#param-errors',root).innerHTML=(noPermission?'<div class="inline-notice">生成参数插件已停用，覆盖配置暂不生效。请先在会话设置中启用。</div>':'')+issues.map(x=>'<div class="issue-row">'+esc(x.text)+'<br>'+button('将 '+fieldNames[x.key]+' 恢复默认','resolve','data-key="'+x.key+'"')+'</div>').join('');
 $('[data-action=apply-params]',root).disabled=issues.length>0||!dirty()||noPermission;
 $('#param-dirty',root).textContent=dirty()?'有未应用修改':'已是当前配置';
}
function openModels(){
 const d=modal('选择模型','<input class="search-input" id="model-search" aria-label="搜索模型" placeholder="搜索模型名称…"><div class="filter-tabs" role="group" aria-label="模型分类">'+['全部','Gemini','Claude','DeepSeek'].map(f=>button(f,'filter-model','data-filter="'+f+'"',f==='全部'?'active':'')).join('')+'</div><div id="model-list"></div>','<small>示例目录 · 不代表当前资格与能力</small>'+button('取消','close'),'compact');d.filter='全部';
 function fill(){const q=$('#model-search',d.el).value.toLowerCase(),list=models.filter(m=>(d.filter==='全部'||m.family===d.filter)&&m.name.toLowerCase().includes(q));$('#model-list',d.el).innerHTML=list.map(m=>button('<span><strong>'+esc(m.name)+'</strong><small>'+m.note+'</small></span><span class="selected">'+(m.id===paramCfg().model?'✓':m.unavailable?'待确认':'')+'</span>','choose-model','data-id="'+m.id+'" '+(m.unavailable?'disabled':''),'model-item')).join('')||'<div class="empty">没有找到符合条件的模型</div>';}
 $('#model-search',d.el).addEventListener('input',fill);d.fill=fill;fill();
}
function applyParams(){
 if(errors(paramCfg()).length)return;
 if(param.candidateIndex!==null){activeSetup[param.candidateIndex]=clone(paramCfg());close(param.item);param=null;renderSetup();toast('已应用到本批候选。');return;}
 state.configs[state.mode][param.purpose]=clone(paramCfg());param.initial[param.purpose]=clone(paramCfg());save();render();renderParams();toast('已更新当前会话的'+purposes[param.purpose]+'配置。');
}
function presets(){return modal('参数预设','<div class="preset-list">'+(state.presets.length?state.presets.map((p,i)=>button('<span><strong>'+esc(p.name)+'</strong><small>'+purposes[p.purpose]+' · '+esc(model(p.config.model).name)+'</small></span>','load-preset','data-index="'+i+'"','model-item')).join(''):'<p class="empty">还没有预设。可以将当前配置另存一份。</p>')+'</div>','<small>预设不会自动派发请求</small>'+button('返回','close'),'compact');}
function savePreset(){
 if(errors(paramCfg()).length){toast('请先处理参数冲突。');return;}
 const d=modal('另存为预设','<label class="form-label" for="preset-name">预设名称</label><input id="preset-name" class="text-field" maxlength="40" placeholder="例如：克制叙事"><p class="notice-small">保存 '+purposes[param.purpose]+' / '+esc(model(paramCfg().model).name)+' 的配置。</p>','<span></span><div class="row">'+button('取消','close')+button('保存预设','confirm-preset','','primary')+'</div>','compact');
 $('[data-action=confirm-preset]',d.el).addEventListener('click',()=>{const name=$('#preset-name',d.el).value.trim();if(!name){$('#preset-name',d.el).focus();return;}state.presets.push({name,purpose:param.purpose,config:clone(paramCfg())});save();close(d);toast('预设已保存在本页示例中。');});
}
function openSetup(){
 if(blocking()){toast('请先处理上一批对比。');return;}
 activeSetup=[clone(config()),clone(config())];activeSetup[1].explicit.temperature=Math.min(1,model(activeSetup[1].model).fields.temperature?.max??1);
 const d=modal('本轮对比','','','setup');d.kind='setup';renderSetup();
}
function renderSetup(){
 const d=stack.find(x=>x.kind==='setup');if(!d)return;
 const n=activeSetup.length,pre=state.m2&&state.backfill?2:0,post=(state.m2?2:0)+(state.m3?1:0),max=n+pre+post;
 $('.dialog-content',d.el).innerHTML='<p class="purpose-note">一份输入，多种写法。选择后，只有一份进入正式剧情。</p><div class="quote-input">'+esc(state.drafts[state.mode]||'尚未输入剧情')+'</div>'+activeSetup.map((cfg,i)=>'<div class="candidate-setup"><span class="letter">'+String.fromCharCode(65+i)+'</span><div class="grow"><h3>'+esc(model(cfg.model).name)+'</h3><p>'+esc(brief(cfg))+'</p>'+(errors(cfg).length?'<p class="error-text">参数存在冲突，请编辑处理</p>':'')+'</div><div class="candidate-tools">'+button('参数','candidate-edit','data-index="'+i+'"','edit')+button('↑','move','data-index="'+i+'" data-direction="-1" aria-label="上移候选 '+String.fromCharCode(65+i)+'" '+(i===0?'disabled':''),'icon')+button('↓','move','data-index="'+i+'" data-direction="1" aria-label="下移候选 '+String.fromCharCode(65+i)+'" '+(i===n-1?'disabled':''),'icon')+button('×','remove-candidate','data-index="'+i+'" aria-label="移除候选 '+String.fromCharCode(65+i)+'" '+(n<=2?'disabled':''),'icon')+'</div></div>').join('')+button(n<4?'+ 添加候选（'+n+'/4）':'已达 4 份候选上限','add-candidate',n>=4?'disabled':'','add-candidate')+
 '<div class="budget-card"><div class="budget-line"><span>候选生成 · 同时最多两路</span><span>'+n+' 次</span></div>'+(pre?'<div class="budget-line"><span>发送前补齐摘要（含可能压缩）</span><span>至多 2 次</span></div>':'')+(post?'<div class="budget-line"><span>采用后 '+[state.m2?'摘要（含可能压缩）':'',state.m3?'记忆整理':''].filter(Boolean).join(' + ')+'</span><span>至多 '+post+' 次</span></div>':'')+'<div class="budget-line"><strong>预计调用上限</strong><strong>'+max+' 次</strong></div><small>未派发部分释放。费用以回执为准；本页仅演示，不消耗额度。</small></div><div id="setup-error"></div>';
 $('.dialog-foot',d.el).innerHTML='<small>采用仅影响这一轮</small><div class="row">'+button('取消','close')+button('开始对比','start',activeSetup.some(x=>errors(x).length)||!state.drafts[state.mode].trim()?'disabled':'','primary')+'</div>';
}
function beginBatch(){
 const d=stack.find(x=>x.kind==='setup');if(!d)return;
 if(state.scenario==='budget'){$('#setup-error',d.el).innerHTML='<div class="inline-notice bad">本轮需要整批预留额度，当前示例余额不足。没有开始生成。请减少候选或取消本轮。</div>';return;}
 const b={id:Date.now(),input:state.drafts[state.mode],scenario:state.scenario,pre:state.m2&&state.backfill?'running':'done',m2:state.m2,m3:state.m3,post:'none',postStep:'',adopted:null,discarded:false,candidates:activeSetup.map((c,i)=>({label:String.fromCharCode(65+i),config:clone(c),status:'queued',index:i}))};
 state.batches[state.mode].push(b);state.drafts[state.mode]='';save();close(d);activeSetup=null;view='comparison';viewingIndex=state.batches[state.mode].length-1;selected=0;pair=1;sideBySide=state.mode==='creator';render();
 const mode=state.mode;if(b.pre==='running')schedule(()=>{b.pre='done';save();pump(b,mode);},3200);else pump(b,mode);
}
function schedule(fn,delay){const id=setTimeout(()=>{timers.delete(id);fn();},delay);timers.add(id);}
function pump(b,mode){
 const running=b.candidates.filter(c=>c.status==='running').length;
 b.candidates.filter(c=>c.status==='queued').slice(0,2-running).forEach(c=>{c.status='running';save();schedule(()=>{
 c.status=c.index===1&&b.scenario==='partial'?'failed':c.index===1&&b.scenario==='unknown'?'unknown':'done';
 if(c.status==='done'){c.raw=sceneBody(c.index);c.elapsed=c.index===0?'4.5 秒':'14 秒';}
 save();pump(b,mode);if(state.mode===mode)render();},c.index===0?4500:14000);});
 if(state.mode===mode)render();
}
function activeBatch(){return state.batches[state.mode][viewingIndex??state.batches[state.mode].length-1];}
function renderComparison(){
 const b=activeBatch();if(!b){view='main';render();return;}
 const chat=state.mode==='chat',single=!sideBySide,display=single?[b.candidates[selected]]:b.candidates.filter((c,i)=>i===selected||i===pair);
 $('#app').innerHTML='<main class="shell '+(chat?'chat-shell':'')+'"><section class="comparison"><header class="compare-top"><div><span class="eyebrow">GENERATION STUDIO</span><h1>对比本轮回复</h1><p>'+esc(b.input)+'</p></div><div class="row">'+button('返回'+(chat?'聊天':'创作'),'back')+button(sideBySide?'逐份阅读':'并排阅读','layout','','desktop-pair')+'</div></header>'+
 (b.pre==='running'?'<div class="inline-notice">正在补齐一次公共摘要，完成后冻结共同上下文。候选尚未派发。</div>':b.pre==='unknown'?'<div class="inline-notice bad">公共摘要结果待核对，尚未派发候选。请保留原记录。</div>':'')+
 (b.adoptionPending!==undefined?'<div class="inline-notice">采用结果待核对。请保留记录，核对前不能再次采用。 '+button('核对采用记录','reconcile-adoption')+'</div>':'')+(b.adopted?'<div class="adopted-bar">已采用 '+b.adopted+'，正式剧情只保存这一份。'+(b.post==='running'?' 正在'+esc(b.postStep)+'。':b.post==='unknown'?' 后台结果待核对。':settled(b)?'所有任务已收束，可以继续。':'其余候选仍需收束。')+'</div>':b.discarded?'<div class="inline-notice">本轮未采用，原输入已保留。仍在进行的请求继续收束。</div>':'')+
 '<nav class="comparison-nav" aria-label="候选分页">'+b.candidates.map((c,i)=>button('<b>'+c.label+'</b> '+chip(c.status),'select-candidate','data-index="'+i+'" aria-pressed="'+(i===selected)+'"',i===selected?'selected':'')).join('')+'</nav><div class="compare-grid '+(single?'single':'')+'">'+display.map(c=>candidateHtml(c,b)).join('')+'</div><footer class="compare-bottom"><small>只有采用的回复进入剧情。<br>费用：暂无费用数据</small><div class="row">'+button('结束其余候选','stop',settled(b)?'disabled':'')+button('都不采用','discard',b.adopted||b.discarded||b.adoptionPending!==undefined?'disabled':'')+'</div></footer></section></main>';
}
function candidateHtml(c,b){
 const canAdopt=c.status==='done'&&!b.adopted&&!b.discarded&&b.adoptionPending===undefined;
 return '<article class="candidate-card '+(c.index===selected?'mobile-selected':'')+'"><header><div class="row"><span class="letter">'+c.label+'</span><div><h3>'+esc(model(c.config.model).name)+'</h3><small>'+esc(brief(c.config))+'</small></div></div>'+chip(c.status)+'</header>'+
 (c.status==='done'?'<div class="read-switch">'+button('渲染结果','view-render','data-index="'+c.index+'"',raw.has(c.index)?'':'active')+button('原文','view-raw','data-index="'+c.index+'"',raw.has(c.index)?'active':'')+'</div>':'')+
 '<div class="candidate-content">'+(c.status==='done'?(raw.has(c.index)?'<pre>'+esc(c.raw)+'</pre>':sceneBody(c.index)):c.status==='unknown'?'<div class="pending-content"><strong>请求已派发，结果尚未确认</strong><span>保留原请求记录。核对结果不会重新生成。</span>'+button('核对原请求','reconcile','data-index="'+c.index+'"')+'</div>':c.status==='failed'?'<div class="pending-content"><strong>此份候选生成失败</strong><span>失败记录已保留。仍可采用其他完成的候选。</span></div>':c.status==='stopped'?'<div class="pending-content">已停止，未派发候选请求。</div>':'<div class="pending-content"><span>'+(c.status==='queued'?'等待可用生成通道…':'正在写下另一种可能…')+'</span><div class="loading-line"></div><div class="loading-line short"></div></div>')+'</div>'+
 '<footer class="candidate-foot"><details><summary>参数、用量与记录</summary><div>'+esc(brief(c.config))+'<br>公共上下文：同一份冻结样例<br>示例记录：'+b.id+' / '+c.label+'<br>实际用量：暂无真实调用数据</div></details><div class="row"><small>'+(c.elapsed?'演示耗时 '+c.elapsed:'等待结果')+'</small>'+button(b.adopted===c.label?'已采用':b.adopted?'未采用':'采用这份','adopt','data-index="'+c.index+'" '+(!canAdopt?'disabled':''),'primary')+'</div>'+(c.status==='done'?button('设为后续剧情配置','future','data-index="'+c.index+'"','text-button'):'')+'</footer></article>';
}
function adopt(index,confirmed=false){
 const b=activeBatch(),c=b.candidates[index];if(b.adopted||b.discarded||c.status!=='done')return;
 if(b.scenario==='adoption'&&!confirmed){b.adoptionPending=index;save();render();return;}delete b.adoptionPending;b.adopted=c.label;state.history[state.mode].push({index:c.index,batch:b.id,input:b.input});b.post=(b.m2||b.m3)?'running':'done';b.postStep='更新变量与 CG';save();render();
 const mode=state.mode;if(b.post==='running')schedule(()=>{b.postStep=b.m2?'更新摘要':'整理记忆';save();if(state.mode===mode)render();schedule(()=>{if(b.m2&&b.m3){b.postStep='整理记忆';save();if(state.mode===mode)render();schedule(()=>{b.post='done';save();if(state.mode===mode)render();},2200);}else{b.post='done';save();if(state.mode===mode)render();}},2200);},1200);
 toast('已采用 '+c.label+'，不重新生成剧情。');
}
function plugins(){modal('会话设置','<p class="purpose-note">这三项已在原型中安装。授权仅对当前示例会话生效。</p>'+[['model','模型选择','管理后续剧情的默认模型。'],['parameters','生成参数','分别调整剧情、摘要与记忆参数。'],['comparison','多模型对比','独立授权本轮候选，不改变默认配置。']].map(([id,name,desc])=>'<label class="plugin-row"><span><h3>'+name+'</h3><p>'+desc+'</p></span><input type="checkbox" data-plugin="'+id+'" '+(state.plugins[id]?'checked':'')+' aria-label="启用'+name+'"></label>').join('')+'<p class="notice-small">停用保留配置和对比记录；已开始的样例任务仍会收束。</p>','<span></span>'+button('完成','close'),'compact');}

document.addEventListener('click',e=>{
 const btn=e.target.closest('[data-action]');if(!btn||btn.disabled)return;const a=btn.dataset.action,idx=Number(btn.dataset.index);
 if(a==='close'){requestClose(stack.find(x=>x.el===btn.closest('dialog'))||stack.at(-1));return;}
 if(a==='mode'){state.mode=btn.dataset.mode;view='main';save();render();return;}
 if(a==='params')openParams();
 if(a==='purpose'){param.purpose=btn.dataset.purpose;renderParams();$('[data-purpose="'+param.purpose+'"]',param.item.el)?.focus();}
 if(a==='pick-model')openModels();
 if(a==='filter-model'){const d=stack.at(-1);d.filter=btn.dataset.filter;d.el.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===btn));d.fill();}
 if(a==='choose-model'){paramCfg().model=btn.dataset.id;close();renderParams();$('[data-action=pick-model]',param.item.el).focus();}
 if(a==='resolve'){delete paramCfg().explicit[btn.dataset.key];if(btn.dataset.key==='thinkingBudget'&&paramCfg().explicit.thinkingEnabled!==true)delete paramCfg().explicit.thinkingEffort;renderParams();}
 if(a==='apply-params')applyParams();
 if(a==='presets')presets();
 if(a==='save-preset')savePreset();
 if(a==='load-preset'){
  const p=state.presets[idx];if(p.purpose!==param.purpose){toast('此预设用于'+purposes[p.purpose]+'，请切换用途后应用。');return;}
  const apply=()=>{paramCfg().explicit=clone(p.config.explicit);close(stack.find(x=>x.el.getAttribute('aria-label')==='参数预设'));renderParams();toast('已载入预设，请核对后应用。');};
  if(p.config.model!==paramCfg().model)confirmDialog('跨模型应用预设？','保留当前模型，载入该预设的显式参数。不兼容项将列出，处理前不能应用。',apply,'载入并核对');else apply();
 }
 if(a==='recommend'){
  if(errors(paramCfg()).length){toast('请先处理参数冲突。');return;}
  const cfg=clone(paramCfg()),purpose=param.purpose;confirmDialog('设为作品推荐配置？','仅保存这一用途的推荐值；不会覆盖已有会话或改变授权与额度。',()=>{state.recommendations[purpose]=cfg;save();render();toast('已保存本页样例的'+purposes[purpose]+'推荐配置。');},'保存推荐');
 }
 if(a==='add-stop'){
  const input=$('#param-stop',param.item.el),v=input.value;if(!v.trim())return;const stops=paramCfg().explicit.stop;
  if(stops.length>=4||v.length>80){toast('停止词最多 4 项，每项最多 80 字符。');return;}if(stops.includes(v)){toast('这个停止词已存在。');return;}stops.push(v);renderParams();$('#param-stop',param.item.el).focus();
 }
 if(a==='remove-stop'){paramCfg().explicit.stop.splice(idx,1);renderParams();}
 if(a==='setup')openSetup();
 if(a==='candidate-edit')openParams(idx);
 if(a==='add-candidate'&&activeSetup.length<4){activeSetup.push(clone(activeSetup.at(-1)));renderSetup();}
 if(a==='remove-candidate'&&activeSetup.length>2){activeSetup.splice(idx,1);renderSetup();}
 if(a==='move'){const to=idx+Number(btn.dataset.direction);[activeSetup[idx],activeSetup[to]]=[activeSetup[to],activeSetup[idx]];renderSetup();}
 if(a==='start')beginBatch();
 if(a==='view-batch'){viewingIndex=idx;view='comparison';selected=0;pair=1;sideBySide=state.mode==='creator';render();}
 if(a==='back'){view='main';render();}
 if(a==='layout'){sideBySide=!sideBySide;render();}
 if(a==='select-candidate'){selected=idx;pair=idx===0?1:0;render();}
 if(a==='view-raw'){raw.add(idx);render();}
 if(a==='view-render'){raw.delete(idx);render();}
 if(a==='adopt')adopt(idx);if(a==='reconcile-adoption'){const b=activeBatch();if(b.adoptionPending!==undefined){adopt(b.adoptionPending,true);toast('样例核对返回已提交；只恢复这一份采用结果。');}}
 if(a==='reconcile')toast('已核对原样例记录，结果仍未确认。不会重新派发。');
 if(a==='stop')confirmDialog('结束其余候选？','停止尚未派发的候选。已派发请求继续收束；本例不支持立即取消供应商请求。',()=>{const b=activeBatch();b.candidates.filter(c=>c.status==='queued').forEach(c=>c.status='stopped');save();render();toast('队列已停止，已派发请求仍保留。');},'停止未派发候选');
 if(a==='discard')confirmDialog('本轮都不采用？','保留对比记录，正式剧情不前进。已派发任务继续收束，原输入回到草稿。',()=>{const b=activeBatch();if(b.adopted||b.discarded)return;b.discarded=true;if(!state.drafts[state.mode])state.drafts[state.mode]=b.input;else toast('当前草稿已改变。原输入仍可在对比记录中查看。');save();view='main';render();},'都不采用');
 if(a==='future'){
  const c=activeBatch().candidates[idx];if(state.mode==='chat'&&(!state.plugins.model||(!state.plugins.parameters&&Object.keys(c.config.explicit).length))){toast('需启用模型选择及生成参数权限，才能改变后续剧情配置。');return;}
  confirmDialog('更改后续剧情配置？','后续剧情使用 '+model(c.config.model).name+' 及这份参数。摘要、记忆与已派发请求保持原配置。',()=>{state.configs[state.mode].story=clone(c.config);save();toast('后续剧情配置已更新。');});
 }
 if(a==='plugins')plugins();
 if(a==='send'){if(blocking())return;if(!state.drafts[state.mode].trim()){toast('先写下你的行动。');return;}state.history[state.mode].push({index:0,input:state.drafts[state.mode]});state.drafts[state.mode]='';save();render();toast('已展示固定样例回复；没有模型调用。');}
 if(a==='reset')confirmDialog('重置本页示例？','只清除 P0 原型的临时配置与演示记录，不影响任何真实会话或账本。',()=>{timers.forEach(clearTimeout);timers.clear();stack.slice().reverse().forEach(close);state=fresh();view='main';param=null;save();render();toast('本页示例已重置。');},'重置示例');
});
document.addEventListener('input',e=>{
 const el=e.target;if(el.id==='story-input'){state.drafts[state.mode]=el.value;save();return;}
 const k=el.dataset.number||el.dataset.range;if(k&&param){paramCfg().explicit[k]=el.value===''?NaN:Number(el.value);const other=el.dataset.number?$('[data-range="'+k+'"]',param.item.el):$('[data-number="'+k+'"]',param.item.el);if(other)other.value=el.value;updateParamValidation();}
});
document.addEventListener('change',e=>{
 const el=e.target;if(['scenario','m2','m3','backfill'].includes(el.id)){state[el.id]=el.type==='checkbox'?el.checked:el.value;save();return;}
 if(el.dataset.default&&param){const k=el.dataset.default;if(el.checked)delete paramCfg().explicit[k];else paramCfg().explicit[k]=clone(model(paramCfg().model).fields[k].def);renderParams();$('[data-default="'+k+'"]',param.item.el).focus();}
 if(el.dataset.boolean&&param){paramCfg().explicit[el.dataset.boolean]=el.checked;updateParamValidation();}
 if(el.dataset.enum&&param){paramCfg().explicit[el.dataset.enum]=el.value;updateParamValidation();}
 if(el.dataset.plugin){state.plugins[el.dataset.plugin]=el.checked;save();render();toast(el.checked?'已启用当前会话授权。':'已停用，原配置与记录仍保留。');}
});
document.addEventListener('keydown',e=>{
 if(e.target.id==='param-stop'&&e.key==='Enter'&&!e.isComposing){e.preventDefault();$('[data-action=add-stop]',param.item.el).click();}
 if(e.target.id==='story-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('[data-action=send]')?.click();}
});
for(const list of Object.values(state.batches))for(const b of list){
 if(b.pre==='running')b.pre='unknown';b.candidates.forEach(c=>{if(c.status==='running')c.status='unknown';});
 if(b.post==='running')b.post='unknown';if(b.pre==='unknown')b.candidates.filter(c=>c.status==='queued').forEach(c=>c.status='stopped');
}
save();render();
