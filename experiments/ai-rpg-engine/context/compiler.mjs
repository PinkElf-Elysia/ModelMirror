import { canonicalJson, computeGenerationInputSha256 } from "../runtime/contracts.mjs";
import { TURN_EXCHANGE_SCHEMA } from "../src/index.mjs";
import { validateContextInput, validatePreparedTurn } from "./contracts.mjs";
import { CONTEXT_FORMATS } from "./schemas.mjs";
import { selectContextLore } from "./selection.mjs";

const fail = (code, path = "") => ({ valid: false, diagnostics: [{ phase: "context", severity: "error", code, path }] });
const canonical = value => { const r = canonicalJson(value); if (!r.valid) throw new Error(); return r.value; };
const digest = (value, hash) => { const d = hash(canonical(value)); if (typeof d !== "string" || !/^[a-f0-9]{64}$/.test(d)) throw new Error(); return d; };
const compare = (a,b) => a < b ? -1 : a > b ? 1 : 0;
function bytes(text) {
  let size = 0;
  for (let i=0;i<text.length;i++) {
    const c=text.charCodeAt(i);
    if(c<128) size++; else if(c<2048) size+=2;
    else if(c>=0xd800 && c<=0xdbff && i+1<text.length && text.charCodeAt(i+1)>=0xdc00 && text.charCodeAt(i+1)<=0xdfff) { size+=4;i++; }
    else size+=3;
  }
  return size;
}
const message = (role, data) => ({ role, content: canonical(data) });
const measure = messages => messages.reduce((n,m)=>n+bytes(m.content),0);
const fitsMessages = messages => messages.length<=80 && messages.every(m=>m.content.length<=65536) && messages.reduce((n,m)=>n+m.content.length,0)<=262144;
function baseData(input) {
  const card=input.cardPackage, scene=input.profile.scenes.find(s=>s.id===input.sceneRef), resources=card.resources;
  const opening=resources.openings.find(r=>r.id===scene.openingRef);
  const secretIds=new Set(resources.worldbookEntries.filter(r=>r.visibility==="host").map(r=>r.id));
  if([...scene.requiredResourceRefs,...input.resourceRefs].some(id=>secretIds.has(id))) return fail("CONTEXT_HOST_RESOURCE_REQUIRED");
  const wanted=new Set([scene.worldRef,scene.openingRef,...scene.requiredResourceRefs,...input.resourceRefs,...opening.styleRefs,...opening.informationModuleRefs]);
  const addRefs=value=>{
    if(Array.isArray(value)) { for(const v of value) addRefs(v); }
    else if(value && typeof value==="object") {
      if(value.source==="package" && typeof value.resourceRef==="string") wanted.add(value.resourceRef);
      for(const v of Object.values(value)) addRefs(v);
    }
  };
  addRefs(input.playerSetup);
  const selected={};
  for(const [kind,entries] of Object.entries(resources)) {
    if(kind==="worldbookEntries") continue;
    selected[kind]=entries.filter(r=>wanted.has(r.id) || kind==="commands").map(r=>{
      const out=JSON.parse(canonical(r));
      if(kind==="openings") out.worldbookRefs=out.worldbookRefs.filter(ref=>!secretIds.has(ref));
      return out;
    });
  }
  return {valid:true,value:{kind:"current_data",cardPackageRef:{id:card.package.id,version:card.package.version},player:input.playerSetup,state:input.session.state,stateFields:card.stateFields,resources:selected},sourceRefs:[...new Set(Object.values(selected).flat().flatMap(r=>r.sourceRefs))].sort(compare)};
}

/** Synchronous compilation only. The trusted host supplies the template and hash port, never the card. */
export function compileContext(input, { hash, hostTemplate } = {}) {
  try {
    const checked=validateContextInput(input,{hash}); if(!checked.valid) return checked;
    if(!hostTemplate || typeof hostTemplate.content!=="string" || !hostTemplate.content.length || Object.keys(hostTemplate).some(k=>!["id","version","content"].includes(k))) return fail("CONTEXT_TRUSTED_HOST_REQUIRED");
    const binding={id:hostTemplate.id,version:hostTemplate.version,sha256:digest(hostTemplate,hash)};
    if(canonical(binding)!==canonical(input.profile.hostTemplate)) return fail("CONTEXT_TEMPLATE_BINDING");
    const base=baseData(input); if(!base.valid) return base;
    const selection=selectContextLore(input); if(!selection.valid) return selection;
    const budget=input.profile.budget;
    const fixed=[
      {role:"system",content:hostTemplate.content},
      message("user",base.value),
      message("user",{kind:"output_contract",schema:TURN_EXCHANGE_SCHEMA}),
      message("user",{kind:"current_turn",exchangeId:input.exchangeId,cardPackageRef:input.playerSetup.cardPackageRef,input:input.input})
    ];
    const required=measure(fixed);
    if(!fitsMessages(fixed) || required+fixed.length*16>budget.inputLimit) return fail("CONTEXT_REQUIRED_BUDGET");
    const loreMessages=[], historyMessages=[];
    const decisions=JSON.parse(canonical(selection.value.loreDecisions));
    const rules=new Map(input.profile.loreRules.map(r=>[r.entryRef,r]));
    const entries=selection.value.selectedEntries;
    const requiredIds=new Set(input.profile.scenes.find(s=>s.id===input.sceneRef).requiredResourceRefs);
    const isRequired=entry=>rules.get(entry.id)?.required || requiredIds.has(entry.id);
    let lore=0,history=0;
    const canFit=(extra)=> fitsMessages([...fixed,...loreMessages,...historyMessages,...extra]) && required+lore+history+measure(extra)+(fixed.length+loreMessages.length+historyMessages.length+extra.length)*16<=budget.inputLimit;
    const turnMessages=turn=>[
      message("user",{kind:"committed_input",exchangeId:turn.exchange.exchangeId,input:turn.exchange.input}),
      message("assistant",{kind:"committed_narrative",exchangeId:turn.exchange.exchangeId,narrative:turn.exchange.proposal.narrative,acceptedStateProposals:turn.exchange.proposal.stateProposals.filter(p=>turn.acceptedStateFields.includes(p.fieldRef))})
    ];
    // Required lore and the newest complete turn must both fit, before optional material.
    for(const entry of entries.filter(isRequired)) {
      const m=message("user",{kind:"worldbook_data",entry});
      if(lore+measure([m])>budget.loreLimit || !canFit([m])) return fail("CONTEXT_REQUIRED_LORE_BUDGET");
      loreMessages.push(m);lore+=measure([m]);
    }
    const turns=input.session.turns, included=[];
    if(turns.length) {
      const t=turns[turns.length-1],ms=turnMessages(t);
      if(measure(ms)>budget.historyLimit || !canFit(ms)) return fail("CONTEXT_LATEST_HISTORY_BUDGET");
      historyMessages.push(...ms);history+=measure(ms);included.push(t.exchange.exchangeId);
    }
    const selectedIds=new Set(entries.filter(isRequired).map(e=>e.id));
    for(const entry of entries.filter(e=>!isRequired(e))) {
      const m=message("user",{kind:"worldbook_data",entry}),size=measure([m]);
      if(lore+size>budget.loreLimit || !canFit([m])) {
        const d=decisions.find(d=>d.entryRef===entry.id); if(d) d.disposition="budget_excluded";
      } else {loreMessages.push(m);lore+=size;selectedIds.add(entry.id);}
    }
    for(let i=turns.length-2;i>=0;i--) {
      const ms=turnMessages(turns[i]),size=measure(ms);
      if(history+size>budget.historyLimit || !canFit(ms)) break;
      historyMessages.unshift(...ms);history+=size;included.unshift(turns[i].exchange.exchangeId);
    }
    const messages=[fixed[0],fixed[1],fixed[2],...loreMessages,...historyMessages,fixed[3]];
    const bindings={sessionId:input.session.sessionId,revision:input.session.revision,generationId:input.generationId,exchangeId:input.exchangeId,cardPackageSha256:digest(input.cardPackage,hash),playerSetupSha256:digest(input.playerSetup,hash),profileSha256:digest(input.profile,hash),hostTemplateSha256:binding.sha256};
    const request={sessionId:input.session.sessionId,expectedRevision:input.expectedRevision,generationId:input.generationId,exchangeId:input.exchangeId,input:input.input,modelId:input.modelId,settings:input.settings,messages};
    const generationHash=computeGenerationInputSha256(request,input.session,hash);if(!generationHash.valid)return generationHash;
    const overhead=messages.length*16;
    const receipt={format:CONTEXT_FORMATS.receipt,formatVersion:"0.1.0",bindings,evidenceKind:"offline",compilerVersion:"0.4.0",messagesSha256:digest(messages,hash),generationInputSha256:generationHash.value,measurement:{method:"utf8_bytes_with_overhead",accuracy:"estimate",counterRef:null,input:required+lore+history+overhead,required,lore,history,overhead,inputLimit:budget.inputLimit,outputLimit:input.settings.maxTokens},loreDecisions:decisions,history:{includedExchangeIds:included,excludedTurnCount:turns.length-included.length},sourceRefs:[...new Set([...base.sourceRefs,...entries.filter(e=>selectedIds.has(e.id)).flatMap(e=>e.sourceRefs)])].sort(compare)};
    const prepared={format:CONTEXT_FORMATS.preparedTurn,formatVersion:"0.1.0",bindings,request,receipt};
    const final=validatePreparedTurn(prepared,{input,hash,hostTemplate:binding});if(!final.valid)return final;
    return {valid:true,diagnostics:[],value:JSON.parse(canonical(prepared))};
  } catch { return fail("CONTEXT_COMPILE_FAILED"); }
}
