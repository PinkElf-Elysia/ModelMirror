import {keys} from '../../authoring/contracts.mjs';
import {validateModelChoice,modelChoiceInput,PARAMETER_SELECTION_FORMAT} from '../../authoring/model-control.mjs';
import {PARAMETER_CONFIG,PARAMETER_PROFILE,validateParameterProfile} from '../../authoring/generation-settings.mjs';
import {check,copy,digest} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
// Capability and price evidence comes only from the trusted composition.
export class Parameters {
 constructor({models=[fixtureModel()],control=null,mode='fixture'}){this.models=copy(models);this.control=control;this.mode=mode;}
 async capabilities(){return Promise.all(this.models.map(async m=>{if(!m.choice)return {id:m.id,label:m.label,capacity:m.contextTokens,revision:digest(m),fields:{maxOutputTokens:{type:'integer',minimum:1,maximum:m.contextTokens-1,default:{mode:'value',value:m.outputTokens}}},evidence:'offline-fixture'};if(!m.choice.format.endsWith('/2')){await this.control.refresh();this.control.assertCurrent(m.choice);return {id:m.id,label:m.label,capacity:m.contextTokens,revision:digest(m.choice.entry),fields:{},evidence:'legacy-fixed'};}const c=await this.control.parameterCatalog(),entry=c.models.find(e=>e.selectionId===m.choice.entry.selectionId);check(entry,'MODEL_UNAVAILABLE');return {id:m.id,label:m.label,capacity:entry.contextLength,revision:entry.capabilityRevision??digest(entry),fields:entry.parameterEvidence==='provider-declared'&&entry.parameterPolicy?.kind==='editable'?copy(entry.parameterPolicy.fields):{},evidence:entry.parameterEvidence};}));}
 async resolve(intent){keys(intent,['modelId','values']);const base=this.models.find(m=>m.id===intent.modelId);check(base,'MODEL_UNAVAILABLE');
  const profile={format:PARAMETER_PROFILE,purpose:'story',modelId:base.choice?.entry.modelId??base.id,parameters:{format:PARAMETER_CONFIG,values:copy(intent.values)}};validateParameterProfile(profile);
  if(!base.choice){check(this.mode==='fixture','MODEL_REQUIRED');keys(intent.values,Object.keys(intent.values).filter(k=>k==='maxOutputTokens'));const setting=intent.values.maxOutputTokens,outputTokens=setting?.mode==='explicit'?setting.value:base.outputTokens;check(Number.isSafeInteger(outputTokens)&&outputTokens>0&&outputTokens<base.contextTokens,'PARAMETER_RANGE');return {...copy(base),outputTokens};}
  check(this.control,'CONTROL_REQUIRED');let choice;
  if(!Object.keys(intent.values).length){choice=copy(base.choice);if(choice.format.endsWith('/2'))await this.control.parameterCatalog();else await this.control.refresh();this.control.assertCurrent(choice);await this.control.validate(choice);}
  else {const c=await this.control.parameterCatalog(),e=c.models.find(e=>e.selectionId===base.choice.entry.selectionId);check(e?.parameterEvidence==='provider-declared'&&e.parameterPolicy?.kind==='editable','PARAMETER_NOT_EDITABLE');choice=await this.control.selectParameters({format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile});}
  validateModelChoice(choice);check(base.price,'PRICE_REQUIRED');return {...copy(base),choice,contextTokens:choice.entry.contextLength,outputTokens:choice.wireParameters.max_tokens};
 }
 async validate(model){if(!model.choice){check(this.mode==='fixture','MODEL_REQUIRED');return;}if(model.choice.format.endsWith('/2'))await this.control.parameterCatalog();else await this.control.refresh();this.control.assertCurrent(model.choice);await this.control.validate(model.choice);}
}
