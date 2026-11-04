import {validateModelPluginReceipt} from './model-plugin.mjs';
import {canonical, parseJson, keys, isId, isHash, sha256, fail} from './contracts.mjs';

export const MODEL_CATALOG_FORMAT = 'modelmirror.rpg.offline-model-catalog/1';
export const MODEL_CHOICE_FORMAT = 'modelmirror.rpg.offline-model-choice/1';
export const CONTROL_CATALOG_FORMAT = 'modelmirror.rpg.control-model-catalog/1';
export const CONTROL_CHOICE_FORMAT = 'modelmirror.rpg.control-model-choice/1';
const controlSource = 'control-plane-cache';
export const MODEL_PURPOSES = Object.freeze(['story','summary','memory']);
export const MODEL_CHANGE_LIMIT = 1000;
const source = 'offline-fixture';
const digest = value => sha256(canonical(value));
const same = (a,b) => canonical(a) === canonical(b);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const text = (value,max) => typeof value === 'string' && value.isWellFormed() && value.trim().length > 0 && value.length <= max;
const identity = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(value);
const limits = {temperature:[0,2],topP:[0,1],maxOutputTokens:[1,1000000]};
const wireNames = {temperature:['temperature'],topP:['top_p'],maxOutputTokens:['max_tokens','max_completion_tokens']};
const copy = value => { canonical(value); return structuredClone(value); };
const records = new WeakSet();

function policyValue(value) {
  keys(value,['kind','fields','exclusive']);
  if (!['fixed','editable'].includes(value.kind)) fail('MODEL_CAPABILITY_INVALID');
  keys(value.fields,Object.keys(limits).filter(k => Object.hasOwn(value.fields ?? {},k)));
  if (!Object.hasOwn(value.fields,'maxOutputTokens')) fail('MODEL_OUTPUT_CAPABILITY_REQUIRED');
  for (const [key,field] of Object.entries(value.fields)) {
    keys(field,['minimum','maximum','defaultValue','required','wireName']);
    if (typeof field.required !== 'boolean' || !wireNames[key].includes(field.wireName) ||
        [field.minimum,field.maximum,field.defaultValue].some(v => typeof v !== 'number' || !Number.isFinite(v) || v < limits[key][0] || v > limits[key][1]) ||
        field.minimum > field.maximum || field.defaultValue < field.minimum || field.defaultValue > field.maximum) fail('MODEL_CAPABILITY_INVALID');
    if (key === 'maxOutputTokens' && (!field.required || [field.minimum,field.maximum,field.defaultValue].some(v => !Number.isSafeInteger(v)))) fail('MODEL_CAPABILITY_INVALID');
    if (value.kind === 'fixed' && (!field.required || field.minimum !== field.maximum)) fail('MODEL_CAPABILITY_INVALID');
  }
  if (!Array.isArray(value.exclusive) || value.exclusive.length > 3) fail('MODEL_CAPABILITY_INVALID');
  const groups = new Set();
  for (const group of value.exclusive) {
    if (!Array.isArray(group) || group.length < 2 || group.length > 3 || new Set(group).size !== group.length || group.some(k => !Object.hasOwn(value.fields,k))) fail('MODEL_CAPABILITY_INVALID');
    const id = group.slice().sort().join(','); if (groups.has(id)) fail('MODEL_CAPABILITY_INVALID'); groups.add(id);
    if (group.filter(k => value.fields[k].required).length > 1) fail('MODEL_CAPABILITY_INVALID');
  }
}
function entryValue(entry) {
  keys(entry,['selectionId','selectionRevision','modelId','name','available','contextLength','maxOutputTokens','parameterPolicy',...(Object.hasOwn(entry ?? {},'parameterEvidence')?['parameterEvidence']:[])]);
  if (Object.hasOwn(entry,'parameterEvidence') && !['provider-declared','legacy-fixed','unknown','invalid'].includes(entry.parameterEvidence)) fail('MODEL_CAPABILITY_INVALID');
  if (!identity(entry.selectionId) || !isHash(entry.selectionRevision) || !text(entry.modelId,200) || !text(entry.name,200) || typeof entry.available !== 'boolean') fail('MODEL_CATALOG_INVALID');
  for (const [key,max] of [['contextLength',100000000],['maxOutputTokens',1000000]]) {
    if (!(entry[key] === null || (Number.isSafeInteger(entry[key]) && entry[key] > 0 && entry[key] <= max))) fail('MODEL_CATALOG_INVALID');
  }
  if (entry.parameterPolicy !== null) policyValue(entry.parameterPolicy);
  return entry;
}
function unavailable(entry) {
  if (!entry.available) return 'MODEL_SELECTION_UNAVAILABLE';
  if (entry.contextLength === null || entry.maxOutputTokens === null || entry.parameterPolicy === null) return 'MODEL_CAPABILITY_UNKNOWN';
  if (entry.parameterPolicy.fields.maxOutputTokens.maximum > entry.maxOutputTokens) return 'MODEL_CAPABILITY_UNKNOWN';
  if (entry.parameterPolicy.fields.maxOutputTokens.minimum >= entry.contextLength) return 'MODEL_CONTEXT_CAPACITY';
  return null;
}
function parametersValue(entry, parameters) {
  const reason = unavailable(entry); if (reason) fail(reason);
  keys(parameters,Object.keys(limits).filter(k => Object.hasOwn(parameters ?? {},k)));
  const fields = entry.parameterPolicy.fields;
  for (const [key,value] of Object.entries(parameters)) {
    if (!Object.hasOwn(fields,key)) fail('MODEL_PARAMETER_UNSUPPORTED','/parameters/'+key);
    const field = fields[key];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < field.minimum || value > field.maximum || (key === 'maxOutputTokens' && !Number.isSafeInteger(value))) fail('MODEL_PARAMETER_RANGE','/parameters/'+key);
  }
  for (const [key,field] of Object.entries(fields)) if (field.required && !Object.hasOwn(parameters,key)) fail('MODEL_PARAMETER_REQUIRED','/parameters/'+key);
  for (const group of entry.parameterPolicy.exclusive) if (group.filter(k => Object.hasOwn(parameters,k)).length > 1) fail('MODEL_PARAMETER_CONFLICT');
  return Object.fromEntries(Object.entries(parameters).map(([key,value]) => [fields[key].wireName,value]));
}
function catalogValue(value) {
  const data = copy(value); keys(data,['catalogId','models']);
  if (!isId(data.catalogId) || !Array.isArray(data.models) || data.models.length > 1000 || Buffer.byteLength(canonical(data)) > 4*1024*1024) fail('MODEL_CATALOG_INVALID');
  const ids = new Set();
  for (const entry of data.models) { entryValue(entry); if (ids.has(entry.selectionId)) fail('MODEL_SELECTION_DUPLICATE'); ids.add(entry.selectionId); }
  const body = {format:MODEL_CATALOG_FORMAT,source,...data};
  return {...body,revision:digest(body)};
}
export function validateModelChoice(value) {
  canonical(value); keys(value,['format','source','catalogId','catalogRevision','entry','parameters','wireParameters','sha256']);
  const {sha256:hash,...body} = value;
  const originValid = (value.format === MODEL_CHOICE_FORMAT && value.source === source) || (value.format === CONTROL_CHOICE_FORMAT && value.source === controlSource && Object.hasOwn(value.entry ?? {},'parameterEvidence'));
  if (!originValid || !isId(value.catalogId) || !isHash(value.catalogRevision) || hash !== digest(body)) fail('MODEL_CHOICE_CORRUPT');
  entryValue(value.entry);
  if (!same(parametersValue(value.entry,value.parameters),value.wireParameters)) fail('MODEL_CHOICE_CORRUPT');
  return value;
}
export function modelCapacity(choice,messages) {
  validateModelChoice(choice);
  if (!Array.isArray(messages) || messages.some(m => !m || !['system','user','assistant'].includes(m.role) || typeof m.content !== 'string' || !m.content.isWellFormed())) fail('MODEL_MESSAGES_INVALID');
  // Same conservative admission rule as the existing bridge, not an exact tokenizer.
  const inputEstimate = messages.reduce((sum,m) => sum+Buffer.byteLength(m.content,'utf8')+32,256);
  const outputReserved = choice.parameters.maxOutputTokens, contextLength = choice.entry.contextLength;
  if (inputEstimate+outputReserved > contextLength) fail('MODEL_CONTEXT_CAPACITY');
  return {method:'utf8-bytes-plus-overhead-v1',inputEstimate,outputReserved,contextLength};
}
export function requireOfflineModelControl(value) { if (!records.has(value)) fail('MODEL_CONTROL_HOST_REQUIRED'); return value; }
// Host-only fixture cache. This class never fetches a catalog, certifies a model,
// configures a route or opens a provider connection. It is not a real control plane.
export class OfflineModelControl {
  #catalog;
  constructor(catalog) { this.#catalog=catalogValue(catalog); records.add(this); Object.freeze(this); }
  replace(catalog) { this.#catalog=catalogValue(catalog); }
  catalog() { return structuredClone(this.#catalog); }
  list() { return {...this.catalog(),models:this.#catalog.models.map(e => ({...structuredClone(e),eligible:unavailable(e) === null,reason:unavailable(e)}))}; }
  select(input) {
    const value = copy(input); keys(value,['catalogRevision','selectionId','selectionRevision','parameters']);
    if (value.catalogRevision !== this.#catalog.revision) fail('MODEL_CATALOG_CHANGED');
    const entry = this.#catalog.models.find(e => e.selectionId === value.selectionId);
    if (!entry || entry.selectionRevision !== value.selectionRevision) fail('MODEL_SELECTION_CHANGED');
    const wireParameters = parametersValue(entry,value.parameters);
    const body = {format:MODEL_CHOICE_FORMAT,source,catalogId:this.#catalog.catalogId,catalogRevision:this.#catalog.revision,entry:structuredClone(entry),parameters:value.parameters,wireParameters};
    return {...body,sha256:digest(body)};
  }
  assertCurrent(choice) {
    validateModelChoice(choice);
    const entry = this.#catalog.models.find(e => e.selectionId === choice.entry.selectionId);
    if (choice.source !== source || choice.catalogId !== this.#catalog.catalogId || !entry || !same(entry,choice.entry)) fail('MODEL_SELECTION_CHANGED');
    const reason = unavailable(entry); if (reason) fail(reason);
    return true;
  }
}

// Host-only transport to two fixed, read-only control-plane endpoints. No provider
// completion, catalog discovery, qualification, budget or automatic retry API.
export class ControlPlaneModelControl {
  #origin; #token; #timeout; #ttl; #catalog=null; #expires=0; #refreshing=null; #epoch=0;
  constructor(options) {
    keys(options,['origin','serviceToken',...(Object.hasOwn(options ?? {},'timeoutMs')?['timeoutMs']:[]),...(Object.hasOwn(options ?? {},'ttlMs')?['ttlMs']:[])]);
    const {origin,serviceToken,timeoutMs=5000,ttlMs=30000}=options;
    // Literal loopback only: no DNS, credentials, query, path or redirects.
    if (typeof origin !== 'string' || !/^http:\/\/(127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}$/.test(origin)) fail('MODEL_CONTROL_ENDPOINT_INVALID');
    try { const url=new URL(origin); if (Number(origin.slice(origin.lastIndexOf(':')+1))>65535 || url.origin!==origin) fail('MODEL_CONTROL_ENDPOINT_INVALID'); } catch { fail('MODEL_CONTROL_ENDPOINT_INVALID'); }
    if (typeof serviceToken !== 'string' || !/^[\x21-\x7e]{32,512}$/.test(serviceToken)) fail('MODEL_CONTROL_CREDENTIAL_REQUIRED');
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs<10 || timeoutMs>10000 || !Number.isSafeInteger(ttlMs) || ttlMs<10 || ttlMs>30000) fail('MODEL_CONTROL_CONFIG_INVALID');
    this.#origin=origin;this.#token=serviceToken;this.#timeout=timeoutMs;this.#ttl=ttlMs;records.add(this);Object.freeze(this);
  }
  async #request(path,value) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),this.#timeout);
    let response;
    try {
      response=await fetch(this.#origin+'/api/rpg/v1/authoring/'+path,{method:value===undefined?'GET':'POST',redirect:'error',signal:controller.signal,
        headers:{authorization:'Bearer '+this.#token,accept:'application/json',...(value===undefined?{}:{'content-type':'application/json'})},
        ...(value===undefined?{}:{body:canonical(value)})});
      if (!response.ok) fail(response.status===404?'MODEL_CONTROL_UNSUPPORTED':response.status===401||response.status===403?'MODEL_CONTROL_UNAUTHORIZED':response.status===409?'MODEL_CONTROL_CHANGED':response.status===422?'MODEL_PARAMETERS_REJECTED':'MODEL_CONTROL_UNAVAILABLE');
      if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type')??'')) fail('MODEL_CONTROL_RESPONSE_INVALID');
      const length=response.headers.get('content-length'); if(length && (!/^[0-9]+$/.test(length) || Number(length)>4*1024*1024)) fail('MODEL_CONTROL_RESPONSE_LIMIT');
      const reader=response.body?.getReader(); if(!reader) fail('MODEL_CONTROL_RESPONSE_INVALID');
      let size=0;const chunks=[];
      try { while(true) { const {value:chunk,done}=await reader.read();if(done)break;size+=chunk.byteLength;if(size>4*1024*1024)fail('MODEL_CONTROL_RESPONSE_LIMIT');chunks.push(chunk); } }
      finally { await reader.cancel().catch(()=>{});reader.releaseLock(); }
      const text=new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks));
      if(text.includes(this.#token)) fail('MODEL_CONTROL_CREDENTIAL_ECHO');
      return parseJson(text,4*1024*1024);
    } catch(error) {
      const safe=new Set(['MODEL_CONTROL_UNSUPPORTED','MODEL_CONTROL_UNAUTHORIZED','MODEL_CONTROL_CHANGED','MODEL_PARAMETERS_REJECTED','MODEL_CONTROL_UNAVAILABLE','MODEL_CONTROL_RESPONSE_INVALID','MODEL_CONTROL_RESPONSE_LIMIT','MODEL_CONTROL_CREDENTIAL_ECHO']);
      // Never forward raw upstream bodies, URLs, credentials or exception causes.
      if(safe.has(error.code))fail(error.code);
      fail(controller.signal.aborted?'MODEL_CONTROL_TIMEOUT':'MODEL_CONTROL_RESPONSE_INVALID');
    } finally {clearTimeout(timer);controller.abort();if(response?.body&&!response.body.locked)await response.body.cancel().catch(()=>{});}
  }
  async refresh() {
    if(this.#refreshing)return this.#refreshing;
    this.#catalog=null;this.#expires=0;this.#epoch++;
    this.#refreshing=(async()=>{
      const value=await this.#request('models');
      try {
        keys(value,['format','source','catalogId','models','revision']);
        if(value.format!==CONTROL_CATALOG_FORMAT||value.source!==controlSource||!isHash(value.revision))fail('MODEL_CONTROL_RESPONSE_INVALID');
        // Opaque revision is minted by Python; do not reinterpret JSON float hashing.
        catalogValue({catalogId:value.catalogId,models:value.models});
        for(const e of value.models) {
          if(!Object.hasOwn(e,'parameterEvidence'))fail('MODEL_CONTROL_RESPONSE_INVALID');
          if((['unknown','invalid'].includes(e.parameterEvidence)&&(e.parameterPolicy!==null||e.available)) ||
             (e.parameterEvidence==='legacy-fixed'&&e.parameterPolicy?.kind!=='fixed') ||
             (e.parameterEvidence==='provider-declared'&&e.parameterPolicy===null))fail('MODEL_CONTROL_RESPONSE_INVALID');
        }
        this.#catalog=copy(value);this.#expires=performance.now()+this.#ttl;return this.list();
      }catch{this.#catalog=null;this.#expires=0;fail('MODEL_CONTROL_RESPONSE_INVALID');}
    })();
    try{return await this.#refreshing;}finally{this.#refreshing=null;}
  }
  #current() {if(!this.#catalog||this.#refreshing&&this.#expires===0||performance.now()>=this.#expires){this.#catalog=null;fail('MODEL_CONTROL_REFRESH_REQUIRED');}return this.#catalog;}
  catalog(){return structuredClone(this.#current());}
  list(){const c=this.#current();return {...structuredClone(c),models:c.models.map(e=>({...structuredClone(e),eligible:unavailable(e)===null,reason:unavailable(e)}))};}
  select(input) {
    const value=copy(input);keys(value,['catalogRevision','selectionId','selectionRevision','parameters']);const c=this.#current();
    if(value.catalogRevision!==c.revision)fail('MODEL_CATALOG_CHANGED');
    const entry=c.models.find(e=>e.selectionId===value.selectionId);if(!entry||entry.selectionRevision!==value.selectionRevision)fail('MODEL_SELECTION_CHANGED');
    const body={format:CONTROL_CHOICE_FORMAT,source:controlSource,catalogId:c.catalogId,catalogRevision:c.revision,entry:structuredClone(entry),parameters:value.parameters,wireParameters:parametersValue(entry,value.parameters)};
    return {...body,sha256:digest(body)};
  }
  assertCurrent(choice) {
    validateModelChoice(choice);let c;try{c=this.#current();}catch(error){if(error.code==='MODEL_CONTROL_REFRESH_REQUIRED')fail('MODEL_SELECTION_UNAVAILABLE');throw error;}const entry=c.models.find(e=>e.selectionId===choice.entry.selectionId);
    if(choice.source!==controlSource||choice.catalogId!==c.catalogId||!entry||!same(entry,choice.entry))fail('MODEL_SELECTION_CHANGED');
    const reason=unavailable(entry);if(reason)fail(reason);return true;
  }
  async validate(choice) {
    choice=copy(choice);this.assertCurrent(choice);const c=this.#current(),epoch=this.#epoch;
    try {
      const result=await this.#request('parameters/validate',{catalogRevision:c.revision,selectionId:choice.entry.selectionId,selectionRevision:choice.entry.selectionRevision,parameters:choice.parameters});
      keys(result,['format','catalogRevision','selectionId','selectionRevision','wireParameters','dispatched']);
      if(result.format!=='modelmirror.rpg.parameter-check/1'||result.dispatched!==false||result.catalogRevision!==c.revision||result.selectionId!==choice.entry.selectionId||result.selectionRevision!==choice.entry.selectionRevision||!same(result.wireParameters,choice.wireParameters))fail('MODEL_CONTROL_RESPONSE_INVALID');
      if(epoch!==this.#epoch)fail('MODEL_CATALOG_CHANGED');this.assertCurrent(choice);return copy(result);
    }catch(error){if(epoch===this.#epoch){this.#catalog=null;this.#expires=0;}if(['MODEL_CONTROL_CHANGED','MODEL_CONTROL_UNAUTHORIZED','MODEL_CONTROL_TIMEOUT','MODEL_CONTROL_UNSUPPORTED','MODEL_PARAMETERS_REJECTED','MODEL_CATALOG_CHANGED','MODEL_CONTROL_REFRESH_REQUIRED','MODEL_CONTROL_UNAVAILABLE'].includes(error.code))fail(error.code);fail('MODEL_CONTROL_RESPONSE_INVALID');}
  }
}

// A recorded /api/rpg/v1/models response has fixed values, not editable bounds.
// The caller must supply only the documented projection (no private _binding).
// This conversion produces offline fixture data and confers no live qualification.
export function fixedRpgCatalog(catalogId,value) {
  value=copy(value); keys(value,['models']);
  if (!Array.isArray(value.models) || value.models.length>1000) fail('MODEL_CATALOG_INVALID');
  const models=value.models.map(item=>{
    keys(item,['selectionId','selectionRevision','model','name','available','contextLength','maxOutputTokens','parameters',...(Object.hasOwn(item,'pricing')?['pricing']:[])]);
    keys(item.parameters,['temperature','top_p','max_tokens'].filter(k=>Object.hasOwn(item.parameters ?? {},k)));
    if (!Object.hasOwn(item.parameters,'max_tokens')) fail('MODEL_OUTPUT_CAPABILITY_REQUIRED');
    const fieldMap={temperature:'temperature',top_p:'topP',max_tokens:'maxOutputTokens'};
    const fields=Object.fromEntries(Object.entries(item.parameters).map(([key,n])=>[fieldMap[key],{minimum:n,maximum:n,defaultValue:n,required:true,wireName:key}]));
    return {selectionId:item.selectionId,selectionRevision:item.selectionRevision,modelId:item.model,name:item.name,available:item.available,
      contextLength:item.contextLength,maxOutputTokens:item.maxOutputTokens,parameterPolicy:{kind:'fixed',fields,exclusive:[]}};
  });
  const out={catalogId,models};catalogValue(out);return out;
}

export function emptyModelState() { return {revision:0,current:{story:null,summary:null,memory:null},changes:[]}; }
export function validateModelIntent(value) {
  canonical(value); keys(value,['sessionId','operationId','expectedRevision','expectedModelRevision','purpose','choice']);
  if (!isId(value.operationId) || !integer(value.expectedRevision) || !integer(value.expectedModelRevision) || !MODEL_PURPOSES.includes(value.purpose)) fail('MODEL_SETTING_INVALID');
  if (value.choice !== null) {
    keys(value.choice,['catalogRevision','selectionId','selectionRevision','parameters']);
    if (!isHash(value.choice.catalogRevision) || !identity(value.choice.selectionId) || !isHash(value.choice.selectionRevision)) fail('MODEL_SETTING_INVALID');
  }
  return structuredClone(value);
}
export function modelChoiceAt(state,revision,purpose) {
  if (!integer(revision) || revision>state.revision || !MODEL_PURPOSES.includes(purpose)) fail('MODEL_STATE_CORRUPT');
  let choice=null;
  for (const change of state.changes.slice(0,revision)) if (change.intent.purpose===purpose) choice=change.choice;
  return choice;
}
export function validateModelState(value,sessionId,sessionRevision,resourceHash=null) {
  keys(value,['revision','current','changes']);keys(value.current,MODEL_PURPOSES);
  if (!integer(value.revision) || !Array.isArray(value.changes) || value.changes.length>MODEL_CHANGE_LIMIT || value.revision!==value.changes.length) fail('MODEL_STATE_CORRUPT');
  const ids=new Set();let previous=-1;
  for (const [i,change] of value.changes.entries()) {
    keys(change,['revision','intent','intentHash','choice',...(resourceHash&&Object.hasOwn(change,'pluginReceipt')?['pluginReceipt']:[])]);validateModelIntent(change.intent);
    if(Object.hasOwn(change,'pluginReceipt'))validateModelPluginReceipt(change.pluginReceipt,change,resourceHash);
    const intent=change.intent;
    if (change.revision!==i+1 || intent.sessionId!==sessionId || intent.expectedModelRevision!==i || intent.expectedRevision<=previous || intent.expectedRevision>=sessionRevision ||
        change.intentHash!==digest(intent) || ids.has(intent.operationId)) fail('MODEL_STATE_CORRUPT');
    ids.add(intent.operationId);previous=intent.expectedRevision;
    if (intent.choice===null) { if(change.choice!==null)fail('MODEL_STATE_CORRUPT'); }
    else {
      validateModelChoice(change.choice);
      const c=change.choice;
      if (!same(intent.choice,{catalogRevision:c.catalogRevision,selectionId:c.entry.selectionId,selectionRevision:c.entry.selectionRevision,parameters:c.parameters})) fail('MODEL_STATE_CORRUPT');
    }
  }
  for (const purpose of MODEL_PURPOSES) if (!same(value.current[purpose],modelChoiceAt(value,value.revision,purpose))) fail('MODEL_STATE_CORRUPT');
}
