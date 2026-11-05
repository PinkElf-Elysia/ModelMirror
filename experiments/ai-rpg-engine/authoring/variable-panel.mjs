import {checkPanelTemplate} from './variable-template-syntax.mjs';
import {canonical, parseJson, keys, sha256, fail} from './contracts.mjs';

export const PANEL_FORMAT = 'modelmirror.rpg.variable-panel/1';
export const PANEL_LIMITS = Object.freeze({dataBytes:65536, nodes:2000, depth:16, templateChars:200000, instructionChars:20000});
const clone = value => structuredClone(value);
const digest = value => sha256(canonical(value));
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, max) => typeof value === 'string' && value.isWellFormed() && [...value].length <= max;

export function checkPanelData(data) {
  const serialized = canonical(data);
  if (!object(data)) fail('PANEL_OBJECT_REQUIRED');
  if (Buffer.byteLength(serialized) > PANEL_LIMITS.dataBytes) fail('PANEL_DATA_CAPACITY');
  let nodes = 0;
  function visit(value, depth) {
    if (++nodes > PANEL_LIMITS.nodes || depth > PANEL_LIMITS.depth) fail('PANEL_DATA_COMPLEX');
    if (value && typeof value === 'object') Object.values(value).forEach(v => visit(v, depth + 1));
  }
  visit(data, 0); return data;
}
export function parsePanelData(source) { return checkPanelData(parseJson(source, PANEL_LIMITS.dataBytes)); }
export function validatePanelConfiguration(config) {
  keys(config, ['enabled','skeleton','template','instructions'], '/settings/variablePanel');
  if (typeof config.enabled !== 'boolean' || !text(config.skeleton,65536) || !text(config.template,PANEL_LIMITS.templateChars) || !text(config.instructions,PANEL_LIMITS.instructionChars)) fail('PANEL_CONFIG_INVALID');
  // Incomplete source remains editable in drafts. Publish/runtime check semantics.
  return config;
}
export function panelIssues(config) {
  if (!config?.enabled) return [];
  let initial;
  try { initial=parsePanelData(config.skeleton); } catch (e) { return [{code:e.code || 'PANEL_JSON_INVALID',path:'/settings/variablePanel/skeleton'}]; }
  try { shape(initial,initial); } catch { return [{code:'PANEL_SKELETON_SHAPE',path:'/settings/variablePanel/skeleton'}]; }
  try { checkPanelTemplate(config.template); } catch(e) { return [{code:e.code||'PANEL_TEMPLATE_SYNTAX',path:'/settings/variablePanel/template'}]; }
  return config.template.trim() ? [] : [{code:'PANEL_TEMPLATE_REQUIRED',path:'/settings/variablePanel/template'}];
}
export function panelDefinition(config) {
  if (!config?.enabled) return null;
  validatePanelConfiguration(config);
  const issues = panelIssues(config); if (issues.length) fail(issues[0].code, issues[0].path);
  return {format:PANEL_FORMAT, configurationHash:digest(config), initialData:parsePanelData(config.skeleton), instructions:config.instructions};
}
function shape(sample, next, path='') {
  if (sample === null) return;
  if (Array.isArray(sample)) {
    if (!Array.isArray(next)) fail('PANEL_DATA_TYPE',path);
    if (sample.length) next.forEach((v,i)=>shape(sample[0],v,path+'/'+i));
  } else if (object(sample)) {
    if (!object(next)) fail('PANEL_DATA_TYPE',path);
    for (const [key,value] of Object.entries(sample)) {
      if (!Object.hasOwn(next,key)) fail('PANEL_DATA_FIELD_MISSING',path+'/'+key);
      shape(value,next[key],path+'/'+key);
    }
  } else if (typeof next !== typeof sample) fail('PANEL_DATA_TYPE',path);
}
export function initialPanelSnapshot(definition) {
  checkPanelDefinition(definition);
  return {configurationHash:definition.configurationHash,revision:0,data:clone(definition.initialData),dataHash:digest(definition.initialData)};
}
export function checkPanelDefinition(value) {
  keys(value,['format','configurationHash','initialData','instructions']);
  if (value.format!==PANEL_FORMAT || !/^[a-f0-9]{64}$/.test(value.configurationHash) || !text(value.instructions,PANEL_LIMITS.instructionChars)) fail('PANEL_DEFINITION_INVALID');
  checkPanelData(value.initialData); return value;
}
export function panelSnapshot(session) {
  if (!session.variablePanel) return null;
  return clone(session.turns.at(-1)?.variablePanel?.snapshot ?? initialPanelSnapshot(session.variablePanel));
}
export function advancePanel(definition, previous, raw) {
  const sourceHash=sha256(raw), beforeHash=digest(previous);
  let status='missing',diagnostic='PANEL_UPDATE_MISSING',data=previous.data,revision=previous.revision;
  const opens=[...raw.matchAll(/<variable_panel>/g)], closes=[...raw.matchAll(/<\/variable_panel>/g)];
  if (opens.length || closes.length) {
    try {
      if (opens.length!==1 || closes.length!==1 || closes[0].index<opens[0].index) fail('PANEL_UPDATE_FORMAT');
      const value=parsePanelData(raw.slice(opens[0].index+16,closes[0].index));
      shape(definition.initialData,value); data=value;
      status=digest(value)===previous.dataHash?'unchanged':'applied'; diagnostic=null;
      if (status==='applied') revision++;
    } catch (error) { status='rejected';diagnostic=error.code || 'PANEL_UPDATE_FORMAT'; }
  }
  const snapshot={configurationHash:definition.configurationHash,revision,data:clone(data),dataHash:digest(data)};
  const body={status,diagnostic,sourceHash,beforeHash,snapshot};return {...body,sha256:digest(body)};
}
export function panelTurn(op) {
  const frozen=op.request.memoryContext.variablePanelSnapshot;
  return frozen ? {variablePanel:advancePanel(frozen.definition,frozen.state,op.receipt.result.text)} : {};
}
export function validatePanelTurns(session) {
  if (!session.variablePanel) {
    if(session.turns.some(t=>Object.hasOwn(t,'variablePanel'))) fail('PANEL_STATE_MISMATCH');
    return;
  }
  let previous=initialPanelSnapshot(session.variablePanel);
  for (const turn of session.turns) {
    const expected=advancePanel(session.variablePanel,previous,turn.assistant);
    if (canonical(expected)!==canonical(turn.variablePanel)) fail('PANEL_STATE_MISMATCH');
    previous=expected.snapshot;
  }
}
export function withPanelInput(input, frozen) {
  if (!frozen) return input;
  return input+'\n\n【变量面板】\n'+frozen.definition.instructions+
    '\n请在剧情之后输出且只输出一份 <variable_panel>完整 JSON 对象</variable_panel>。这是本轮状态快照，不是增量补丁。保留初始骨架的字段及类型；无变化时原样返回当前数据。不要输出脚本。\n当前状态：\n'+canonical(frozen.state.data)+'\n【变量面板结束】';
}
export function panelVisibleReply(raw, result) {
  if (!result || !['applied','unchanged'].includes(result.status)) return raw;
  return raw.replace(/<variable_panel>[\s\S]*?<\/variable_panel>/,'');
}
