import {canonical, keys, isId, isHash, sha256, fail} from './contracts.mjs';
import {SessionRecords} from './session-storage.mjs';
import {requireOfflineModelControl} from './model-control.mjs';

export const PARAMETER_CONFIG = 'modelmirror.rpg.parameter-config/2';
export const PARAMETER_PROFILE = 'modelmirror.rpg.parameter-profile/1';
export const PARAMETER_RECOMMENDATIONS = 'modelmirror.rpg.parameter-recommendations/1';
export const PARAMETER_PURPOSES = Object.freeze(['story','summary','memory']);
const FORMAT = 'modelmirror.rpg.parameter-settings-record/1';
const limits = {temperature:[0,2],topP:[0,1],maxOutputTokens:[1,1000000],topK:[0,1000000],frequencyPenalty:[-2,2],presencePenalty:[-2,2],seed:[-Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER],'thinking.budgetTokens':[0,1000000]};
const integers = new Set(['maxOutputTokens','topK','seed','thinking.budgetTokens']);
const fields = [...Object.keys(limits),'stop','thinking.enabled','thinking.effort'];
const efforts = ['none','minimal','low','medium','high','xhigh','max'];
const digest = v => sha256(canonical(v));
const same = (a,b) => canonical(a) === canonical(b);
const copy = v => {canonical(v);return structuredClone(v);};
const uint = v => Number.isSafeInteger(v) && v >= 0;
const text = (v,max) => typeof v === 'string' && v.isWellFormed() && v.trim().length > 0 && [...v].length <= max;
function purpose(v) {if(!PARAMETER_PURPOSES.includes(v))fail('PARAMETER_PURPOSE_INVALID');}
function session(v) {if(!isId(v))fail('PARAMETER_SESSION_INVALID');}
const recordId=(kind,targetId)=>kind==='session'?'session-'+sha256('generation-parameters:'+targetId):'presets';
function identifier(v) {if(!isId(v))fail('PARAMETER_ID_INVALID');}

// Portable intent only. Provider fields, grants, credentials and quota have no slots.
export function validateParameterProfile(profile) {
  canonical(profile);keys(profile,['format','purpose','modelId','parameters']);purpose(profile.purpose);
  if(profile.format!==PARAMETER_PROFILE||!text(profile.modelId,200))fail('PARAMETER_PROFILE_INVALID');
  keys(profile.parameters,['format','values']);
  if(profile.parameters.format!==PARAMETER_CONFIG)fail('PARAMETER_CONFIG_VERSION');
  const values=profile.parameters.values;keys(values,Object.keys(values??{}));
  for(const [id,setting] of Object.entries(values)) {
    if(!fields.includes(id))fail('PARAMETER_FIELD_UNKNOWN','/parameters/values/'+id);
    keys(setting,setting?.mode==='explicit'?['mode','value']:['mode']);
    if(!['default','explicit'].includes(setting.mode))fail('PARAMETER_MODE_INVALID');
    if(setting.mode==='default')continue;
    const v=setting.value;
    const valid=limits[id]?typeof v==='number'&&Number.isFinite(v)&&v>=limits[id][0]&&v<=limits[id][1]&&(!integers.has(id)||Number.isSafeInteger(v)):
      id==='thinking.enabled'?typeof v==='boolean':id==='thinking.effort'?efforts.includes(v):
      Array.isArray(v)&&v.length<=16&&new Set(v).size===v.length&&v.every(s=>typeof s==='string'&&s.isWellFormed()&&[...s].length>0&&[...s].length<=1024);
    if(!valid)fail('PARAMETER_VALUE_INVALID','/parameters/values/'+id);
  }
  return copy(profile);
}
export function validateRecommendations(value) {
  keys(value,['format',...PARAMETER_PURPOSES]);
  if(value.format!==PARAMETER_RECOMMENDATIONS)fail('PARAMETER_RECOMMENDATIONS_VERSION');
  for(const p of PARAMETER_PURPOSES)if(value[p]!==null){validateParameterProfile(value[p]);if(value[p].purpose!==p)fail('PARAMETER_PURPOSE_INVALID');}
  return copy(value);
}
function selectionValue(selection) {
  keys(selection,['catalogRevision','selectionId','selectionRevision','capabilityRevision']);
  if(!text(selection.selectionId,128)||!isHash(selection.catalogRevision)||!isHash(selection.selectionRevision)||!isHash(selection.capabilityRevision))fail('PARAMETER_SELECTION_INVALID');
}
// UI diagnostics only. A successful preview still requires authoritative preflight.
export function parameterIssues(profile,entry) {
  validateParameterProfile(profile);
  const issues=[];const add=(code,field=null)=>issues.push({code,field});
  if(profile.modelId!==entry.modelId)add('PARAMETER_MODEL_MISMATCH');
  if(!entry.available)add('MODEL_SELECTION_UNAVAILABLE');
  const policy=entry.parameterPolicy;
  if(!policy||!Array.isArray(policy.purposes)||!policy.fields){add('MODEL_CAPABILITY_UNKNOWN');return issues;}
  if(!policy.purposes.includes(profile.purpose))add('PARAMETER_PURPOSE_UNSUPPORTED');
  const effective={};
  for(const [id,setting] of Object.entries(profile.parameters.values))if(!Object.hasOwn(policy.fields,id))add('PARAMETER_UNSUPPORTED',id);
  for(const [id,field] of Object.entries(policy.fields)) {
    const setting=profile.parameters.values[id]??{mode:'default'};
    if(setting.mode==='default'&&field.default?.mode==='omit'){if(field.required)add('PARAMETER_REQUIRED',id);continue;}
    const v=setting.mode==='explicit'?setting.value:field.default?.value;effective[id]=v;
    const valid=field.type==='number'||field.type==='integer'?typeof v==='number'&&Number.isFinite(v)&&v>=field.minimum&&v<=field.maximum&&(field.type!=='integer'||Number.isSafeInteger(v)):
      field.type==='boolean'?typeof v==='boolean':field.type==='enum'?Array.isArray(field.options)&&field.options.includes(v):
      field.type==='text-list'?Array.isArray(v)&&v.length>=field.minItems&&v.length<=field.maxItems&&new Set(v).size===v.length&&v.every(s=>typeof s==='string'&&s.isWellFormed()&&[...s].length>0&&[...s].length<=field.maxItemLength):false;
    if(!valid||policy.kind==='fixed'&&!same(v,field.default.value))add('PARAMETER_RANGE',id);
  }
  for(const group of policy.exclusive??[])if(group.filter(k=>Object.hasOwn(effective,k)).length>1)add('PARAMETER_CONFLICT',group.join(','));
  const matches=c=>c.op==='set'?Object.hasOwn(effective,c.field):c.op==='unset'?!Object.hasOwn(effective,c.field):c.op==='eq'&&Object.hasOwn(effective,c.field)&&same(effective[c.field],c.value);
  for(const rule of policy.requires??[])if(matches(rule.if)&&!matches(rule.then))add('PARAMETER_DEPENDENCY',rule.then.field);
  for(const [left,right] of policy.lessThan??[])if(Object.hasOwn(effective,left)&&(!Object.hasOwn(effective,right)||!(effective[left]<effective[right])))add('PARAMETER_DEPENDENCY',left);
  if(Object.hasOwn(effective,'thinking.effort')&&Object.hasOwn(effective,'thinking.budgetTokens'))add('PARAMETER_CONFLICT','thinking.effort');
  if(effective['thinking.enabled']===false&&['thinking.effort','thinking.budgetTokens'].some(k=>Object.hasOwn(effective,k)))add('PARAMETER_CONFLICT','thinking.enabled');
  return issues;
}
export function validateParameterResolution(resolution,profile,capabilityRevision) {
  keys(resolution,['format','capabilityRevision','purpose','effectiveParameters','sources','wireParameters','sha256']);
  if(resolution.format!=='modelmirror.rpg.parameter-resolution/2'||resolution.capabilityRevision!==capabilityRevision||resolution.purpose!==profile.purpose||!isHash(resolution.sha256))fail('MODEL_CONTROL_RESPONSE_INVALID');
  const effective=resolution.effectiveParameters,sources=resolution.sources,wire=resolution.wireParameters;
  keys(effective,Object.keys(effective??{}));keys(sources,Object.keys(sources??{}));keys(wire,Object.keys(wire??{}));
  validateParameterProfile({...profile,parameters:{format:PARAMETER_CONFIG,values:Object.fromEntries(Object.entries(effective).map(([k,v])=>[k,{mode:'explicit',value:v}]))}});
  if(!Number.isSafeInteger(effective.maxOutputTokens)||effective.maxOutputTokens<1)fail('MODEL_CONTROL_RESPONSE_INVALID');
  for(const [id,s] of Object.entries(sources)) {
    if(!fields.includes(id))fail('MODEL_CONTROL_RESPONSE_INVALID');keys(s,['mode','resolution']);
    const setting=profile.parameters.values[id]??{mode:'default'};
    if(s.mode!==setting.mode||s.resolution!==(s.mode==='explicit'?'explicit':Object.hasOwn(effective,id)?'declared-default':'omitted'))fail('MODEL_CONTROL_RESPONSE_INVALID');
    if(setting.mode==='explicit'&&(!Object.hasOwn(effective,id)||!same(effective[id],setting.value)))fail('MODEL_CONTROL_RESPONSE_INVALID');
  }
  if([...Object.keys(profile.parameters.values),...Object.keys(effective)].some(k=>!Object.hasOwn(sources,k)))fail('MODEL_CONTROL_RESPONSE_INVALID');
  // The receipt is trusted only from the host transport, never a client input.
  // Still reject unexpected wire paths instead of persisting arbitrary provider data.
  const mapped={temperature:'temperature',topP:'top_p',topK:'top_k',frequencyPenalty:'frequency_penalty',presencePenalty:'presence_penalty',seed:'seed',stop:'stop'};
  const expected={};for(const [id,v] of Object.entries(effective)) {
    if(mapped[id])expected[mapped[id]]=v;
    else if(id==='maxOutputTokens')expected[Object.hasOwn(wire,'max_completion_tokens')?'max_completion_tokens':'max_tokens']=v;
    else if(id==='thinking.effort'&&Object.hasOwn(wire,'reasoning_effort'))expected.reasoning_effort=v;
    else {expected.reasoning??={};expected.reasoning[id==='thinking.enabled'?'enabled':id==='thinking.effort'?'effort':'max_tokens']=v;}
  }
  if(!same(expected,wire))fail('MODEL_CONTROL_RESPONSE_INVALID');
  return copy(resolution);
}
const empty = (kind,targetId) => ({format:FORMAT,kind,targetId,revision:0,current:kind==='session'?Object.fromEntries(PARAMETER_PURPOSES.map(p=>[p,{revision:0,configuration:null}])):[],operations:[]});
function configuration(value) {
  keys(value,['profile','selection','resolution']);validateParameterProfile(value.profile);selectionValue(value.selection);
  validateParameterResolution(value.resolution,value.profile,value.selection.capabilityRevision);
}
function stateValue(value) {
  keys(value,['format','kind','targetId','revision','current','operations']);
  if(value.format!==FORMAT||!['session','presets'].includes(value.kind)||!uint(value.revision)||!Array.isArray(value.operations)||value.operations.length>1000||value.revision!==value.operations.length)fail('PARAMETER_STORE_CORRUPT');
  if(value.kind==='session')session(value.targetId);else if(value.targetId!=='presets')fail('PARAMETER_STORE_CORRUPT');
  const rebuilt=empty(value.kind,value.targetId),ids=new Set();
  for(const [i,op] of value.operations.entries()) {
    keys(op,['operationId','requestHash','revision','result']);identifier(op.operationId);
    if(!isHash(op.requestHash)||op.revision!==i+1||ids.has(op.operationId))fail('PARAMETER_STORE_CORRUPT');ids.add(op.operationId);
    const r=op.result;
    if(value.kind==='session') {
      keys(r,['purpose','purposeRevision','configuration']);purpose(r.purpose);
      if(r.purposeRevision!==rebuilt.current[r.purpose].revision+1)fail('PARAMETER_STORE_CORRUPT');
      if(r.configuration!==null){configuration(r.configuration);if(r.configuration.profile.purpose!==r.purpose)fail('PARAMETER_STORE_CORRUPT');}
      rebuilt.current[r.purpose]={revision:r.purposeRevision,configuration:copy(r.configuration)};
    } else {
      keys(r,['id','name','revision','configuration']);identifier(r.id);if(!text(r.name,80))fail('PARAMETER_STORE_CORRUPT');configuration(r.configuration);
      const index=rebuilt.current.findIndex(p=>p.id===r.id),prior=rebuilt.current[index];
      if(r.revision!==(prior?.revision??0)+1)fail('PARAMETER_STORE_CORRUPT');
      if(index<0)rebuilt.current.push(copy(r));else rebuilt.current[index]=copy(r);
      if(rebuilt.current.length>100)fail('PARAMETER_STORE_CORRUPT');
    }
  }
  if(!same(rebuilt.current,value.current))fail('PARAMETER_STORE_CORRUPT');
  return value;
}

// Host-only parameter storage. Session binding, busy checks, plugin permission and
// formal context CAS belong to the calling session host (P2); this is not a send API.
export class ParameterSettingsStore {
  #records;#control;#queue=Promise.resolve();#closed=false;
  constructor(records,control){this.#records=records;this.#control=control;}
  static async open({directory,control,recoverDeadOwner=false,storageFault=async()=>{}}) {
    requireOfflineModelControl(control);if(typeof control.checkParameters!=='function')fail('PARAMETER_CONTROL_V2_REQUIRED');
    return new ParameterSettingsStore(await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'generation-parameters-v1'}),control);
  }
  #run(action){if(this.#closed)return Promise.reject(Object.assign(new Error('PARAMETER_STORE_CLOSED'),{code:'PARAMETER_STORE_CLOSED'}));const job=this.#queue.then(action);this.#queue=job.catch(()=>{});return job;}
  async #load(kind,targetId){const id=recordId(kind,targetId);const record=await this.#records.get(id,stateValue);if(record&&(record.data.kind!==kind||record.data.targetId!==targetId))fail('PARAMETER_STORE_CORRUPT');return record??{hash:null,data:empty(kind,targetId)};}
  readSession(sessionId){session(sessionId);return this.#run(async()=>copy((await this.#load('session',sessionId)).data));}
  listPresets(){return this.#run(async()=>copy((await this.#load('presets','presets')).data));}
  async inspect(profile,selection){profile=validateParameterProfile(profile);selection=copy(selection);selectionValue(selection);return this.#control.checkParameters({profile,selection});}
  async #checked(profile,selection){const checked=await this.inspect(profile,selection);if(!checked.valid)fail('PARAMETER_PREFLIGHT_REJECTED','',{issues:checked.issues});return {profile:copy(profile),selection:copy(selection),resolution:checked.resolution};}
  #request(input,extra){const v=copy(input);keys(v,['operationId','expectedRevision',...extra]);identifier(v.operationId);if(!uint(v.expectedRevision))fail('PARAMETER_REVISION_INVALID');return v;}
  async #mutate(kind,targetId,input,build) {
    const record=await this.#load(kind,targetId),state=record.data,requestHash=digest(input),prior=state.operations.find(o=>o.operationId===input.operationId);
    if(prior){if(prior.requestHash!==requestHash)fail('PARAMETER_OPERATION_COLLISION');return {...copy(prior),replayed:true};}
    if(state.revision!==input.expectedRevision)fail('PARAMETER_REVISION_CONFLICT');
    if(state.operations.length>=1000)fail('PARAMETER_OPERATION_CAPACITY');
    const result=await build(state),op={operationId:input.operationId,requestHash,revision:state.revision+1,result};
    state.revision=op.revision;state.operations.push(op);stateValue(state);
    await this.#records.put(recordId(kind,targetId),record.hash,state);return {...copy(op),replayed:false};
  }
  configure(input) {
    const v=this.#request(input,['sessionId','expectedPurposeRevision','purpose','profile','selection']);session(v.sessionId);purpose(v.purpose);
    if(!uint(v.expectedPurposeRevision)||(v.profile===null)!==(v.selection===null))fail('PARAMETER_SETTING_INVALID');
    if(v.profile!==null){validateParameterProfile(v.profile);selectionValue(v.selection);if(v.profile.purpose!==v.purpose)fail('PARAMETER_PURPOSE_INVALID');}
    return this.#run(()=>this.#mutate('session',v.sessionId,v,async s=>{
      const p=s.current[v.purpose];if(p.revision!==v.expectedPurposeRevision)fail('PARAMETER_PURPOSE_REVISION_CONFLICT');
      const value=v.profile===null?null:await this.#checked(v.profile,v.selection);
      s.current[v.purpose]={revision:p.revision+1,configuration:value};return {purpose:v.purpose,purposeRevision:p.revision+1,configuration:value};
    }));
  }
  savePreset(input) {
    const v=this.#request(input,['id','name','expectedPresetRevision','profile','selection']);identifier(v.id);
    if(!text(v.name,80)||!uint(v.expectedPresetRevision))fail('PARAMETER_PRESET_INVALID');validateParameterProfile(v.profile);selectionValue(v.selection);
    return this.#run(()=>this.#mutate('presets','presets',v,async s=>{
      const index=s.current.findIndex(p=>p.id===v.id),prior=s.current[index];
      if((prior?.revision??0)!==v.expectedPresetRevision)fail('PARAMETER_PRESET_REVISION_CONFLICT');
      if(prior&&(prior.configuration.profile.purpose!==v.profile.purpose||prior.configuration.profile.modelId!==v.profile.modelId))fail('PARAMETER_PRESET_IDENTITY_CONFLICT');
      if(index<0&&s.current.length>=100)fail('PARAMETER_PRESET_CAPACITY');
      const preset={id:v.id,name:v.name,revision:(prior?.revision??0)+1,configuration:await this.#checked(v.profile,v.selection)};
      if(index<0)s.current.push(preset);else s.current[index]=preset;return preset;
    }));
  }
  applyPreset(input) {
    const v=this.#request(input,['sessionId','expectedPurposeRevision','purpose','presetId','expectedPresetRevision','selection']);session(v.sessionId);purpose(v.purpose);identifier(v.presetId);selectionValue(v.selection);
    if(!uint(v.expectedPurposeRevision)||!uint(v.expectedPresetRevision))fail('PARAMETER_REVISION_INVALID');
    return this.#run(()=>this.#mutate('session',v.sessionId,v,async s=>{
      const p=s.current[v.purpose];if(p.revision!==v.expectedPurposeRevision)fail('PARAMETER_PURPOSE_REVISION_CONFLICT');
      const preset=(await this.#load('presets','presets')).data.current.find(p=>p.id===v.presetId);
      if(!preset||preset.revision!==v.expectedPresetRevision)fail('PARAMETER_PRESET_REVISION_CONFLICT');
      if(preset.configuration.profile.purpose!==v.purpose)fail('PARAMETER_PURPOSE_INVALID');
      const value=await this.#checked(preset.configuration.profile,v.selection);
      s.current[v.purpose]={revision:p.revision+1,configuration:value};return {purpose:v.purpose,purposeRevision:p.revision+1,configuration:value};
    }));
  }
  // Explicit caller action; this does not save a work or apply anything to sessions.
  recommendations(sessionId){session(sessionId);return this.#run(async()=>{const s=(await this.#load('session',sessionId)).data;return validateRecommendations({format:PARAMETER_RECOMMENDATIONS,...Object.fromEntries(PARAMETER_PURPOSES.map(p=>[p,copy(s.current[p].configuration?.profile??null)]))});});}
  recover(kind,targetId,operationId){if(kind==='session')session(targetId);else if(kind!=='presets'||targetId!=='presets')fail('PARAMETER_ID_INVALID');identifier(operationId);return this.#run(async()=>{const recovery=await this.#records.reconcile(recordId(kind,targetId));const state=(await this.#load(kind,targetId)).data,op=state.operations.find(o=>o.operationId===operationId);return {status:op?'committed':'not_found',operation:copy(op??null),storage:recovery};});}
  async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
