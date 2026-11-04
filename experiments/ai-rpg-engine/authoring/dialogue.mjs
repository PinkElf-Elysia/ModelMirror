// Pure, provider-free authoring rules. Stored text is never trimmed or rewritten.
export const DIALOGUE_LIMITS = Object.freeze({openings:40, openingText:200000, presets:10, pairs:100, presetOpenings:100, title:30, question:1000, answer:10000, presetOpening:50, shortcuts:10, command:15, output:100});
export const openingOptions = dialogue => Object.hasOwn(dialogue,'openings') ? [...dialogue.openings] : dialogue.opening ? [dialogue.opening] : [];
export function withOpeningOptions(dialogue, options) {
  return {...structuredClone(dialogue), opening:'', openings:[...options]};
}
export const newPreset = id => ({id,title:'',messages:[{user:'',assistant:''}],openings:['']});
export const newShortcut = id => ({id,name:'',text:''});
export function moveOpening(options, from, to) {
  const result=[...options];
  if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||to<0||from>=result.length||to>=result.length)return result;
  result.splice(to,0,result.splice(from,1)[0]);return result;
}
export function presetIssues(preset, path='') {
  const issues=[];
  const check=(value,max,field)=>{if(!value.trim()||[...value].length>max)issues.push({code:'AUTHORING_DIALOGUE_INCOMPLETE',path:path+field});};
  check(preset.title,30,'/title');
  if(!preset.messages.length)issues.push({code:'AUTHORING_DIALOGUE_INCOMPLETE',path:path+'/messages'});
  preset.messages.forEach((pair,i)=>{check(pair.user,1000,`/messages/${i}/user`);check(pair.assistant,10000,`/messages/${i}/assistant`);});
  // Empty optional opening slots are draft UI state, not player choices.
  return issues;
}
export function shortcutIssues(shortcut,path='') {
  return [['name',15],['text',100]].filter(([key,max])=>!shortcut[key].trim()||[...shortcut[key]].length>max)
    .map(([key])=>({code:'AUTHORING_SHORTCUT_INCOMPLETE',path:path+'/'+key}));
}
export function dialogueIssues(enhancements) {
  const issues=[];
  enhancements.shortcuts.forEach((s,i)=>issues.push(...shortcutIssues(s,`/enhancements/shortcuts/${i}`)));
  if(enhancements.dialogue.mode==='presets')enhancements.dialogue.presets.forEach((p,i)=>issues.push(...presetIssues(p,`/enhancements/dialogue/presets/${i}`)));
  return issues;
}
/** Only explicit preset selection supplies example history; choices remain raw player input. */
export function dialoguePreview(enhancements,presetId=null) {
  const dialogue=enhancements.dialogue;
  const preset=dialogue.mode==='presets'?dialogue.presets.find(p=>p.id===presetId):null;
  const usable=preset&&!presetIssues(preset).length;
  return {
    mode:dialogue.mode, presetId:usable?preset.id:null,
    messages:usable?preset.messages.flatMap(pair=>[{role:'user',content:pair.user},{role:'assistant',content:pair.assistant}]):[],
    choices:(dialogue.mode==='opening'?openingOptions(dialogue):usable?preset.openings:[]).filter(text=>text.trim()),
    shortcuts:enhancements.shortcuts.filter(s=>!shortcutIssues(s).length).map(s=>({...s})),
  };
}
