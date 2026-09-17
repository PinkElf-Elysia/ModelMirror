import { TURN_EXCHANGE_SCHEMA, validateCardPackage, validateTurnExchange } from '../src/index.mjs';
import { canonicalJson } from '../runtime/contracts.mjs';
import { createHash } from 'node:crypto';
const hashValue = value => {const encoded=canonicalJson(value);if(!encoded.valid)throw Error('STRUCTURED_JSON_INVALID');return createHash('sha256').update(encoded.value).digest('hex');};
const object = properties => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
const literal = value => ({ type: typeof value, enum: [value] });
const empty = () => ({ type: 'array', items: { type: 'string' }, maxItems: 0 });
const array = (branches, maxItems) => branches.length ? { type: 'array', items: branches.length === 1 ? branches[0] : { anyOf: branches }, maxItems } : empty();
const fail = code => { throw Object.assign(Error(code), { code }); };
// Only the known frozen contract is converted; never accept arbitrary caller schemas.
function convert(schema) {
 if (schema.const !== undefined) return literal(schema.const);
 if (schema.oneOf) {
  const disjoint=(a,b)=>a.type!==b.type && ![a.type,b.type].every(t=>['number','integer'].includes(t)) || a.type==='object' && b.type==='object' && Object.keys(a.properties).some(k=>a.required?.includes(k)&&b.required?.includes(k)&&a.properties[k].const!==undefined&&b.properties[k]?.const!==undefined&&a.properties[k].const!==b.properties[k].const);
  if(schema.oneOf.some((a,i)=>schema.oneOf.slice(i+1).some(b=>!disjoint(a,b))))fail('STRUCTURED_UNION_NOT_DISJOINT');
  return {anyOf:schema.oneOf.map(convert)};
 }
 if (schema.type === 'object') {
  const required = schema.required ?? [], optional = Object.keys(schema.properties).filter(k => !required.includes(k));
  if (optional.length > 3) fail('STRUCTURED_OPTIONAL_LIMIT');
  const branches = [];
  for (let mask=0;mask<2**optional.length;mask++) {
   const keys=Object.keys(schema.properties).filter(k => required.includes(k) || (mask & (1 << optional.indexOf(k))));
   branches.push(object(Object.fromEntries(keys.map(k=>[k,convert(schema.properties[k])]))));
  }
  return branches.length === 1 ? branches[0] : {anyOf:branches};
 }
 const output={};for(const [key,value] of Object.entries(schema)) {
  if (['$schema','$id'].includes(key)) continue;
  if (!['type','enum','minLength','maxLength','pattern','minimum','maximum','minItems','maxItems','items','uniqueItems'].includes(key)) fail('STRUCTURED_KEYWORD_UNSUPPORTED');
  // Uniqueness stays in the frozen validator; do not claim full semantic equivalence.
  if(key==='uniqueItems')continue;
  output[key]=key==='items'?convert(value):structuredClone(value);
 }
 return output;
}
export function compileStructuredSchema(card, request) {
 if(!validateCardPackage(card).valid)fail('STRUCTURED_CARD_INVALID');
 const probe={format:'modelmirror.ai-rpg.turn-exchange',formatVersion:'0.1.0',exchangeId:request?.exchangeId,cardPackageRef:{id:card.package.id,version:card.package.version},input:request?.input,proposal:{narrative:'schema input validation',suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]}};
 if(!validateTurnExchange(probe,card).valid)fail('STRUCTURED_REQUEST_INVALID');
 const schema=convert(TURN_EXCHANGE_SCHEMA);
 schema.properties.exchangeId=literal(request.exchangeId);
 schema.properties.cardPackageRef=object({id:literal(card.package.id),version:literal(card.package.version)});
 schema.properties.input=object(Object.fromEntries(Object.entries(request.input).map(([k,v])=>[k,literal(v)])));
 const proposal=schema.properties.proposal;
 const infoType=field=> {
  if(field.valueType==='list')return {type:'array',items:{type:'string',maxLength:8192},maxItems:1024};
  if(field.valueType==='text')return {type:'string',maxLength:65536};
  if(['number','boolean'].includes(field.valueType))return {type:field.valueType};
  return fail('STRUCTURED_INFORMATION_TYPE');
 };
 proposal.properties.informationModules=array(card.resources.informationModules.map(m=>object({moduleRef:literal(m.id),values:array(m.fields.map(f=>object({fieldRef:literal(f.id),value:infoType(f)})),128)})),128);
 const states=card.stateFields.filter(f=>f.modelMayPropose).flatMap(f=>{
  let value;
  if(f.valueType==='shortText')value={type:'string',maxLength:f.maxLength};
  else if(f.valueType==='enum')value={type:'string',enum:f.choices};
  else if(f.valueType==='boolean')value={type:'boolean'};
  else if(f.valueType==='integer')value={type:'integer',...(f.minimum===undefined?{}:{minimum:f.minimum}),...(f.maximum===undefined?{}:{maximum:f.maximum})};
  else return fail('STRUCTURED_STATE_TYPE');
  return [object({fieldRef:literal(f.id),proposedValue:value}),object({fieldRef:literal(f.id),proposedValue:value,rationale:{type:'string',maxLength:8192}})];
 });
 proposal.properties.stateProposals=request.input.kind==='query'?empty():array(states,1024);
 let nodes=0,properties=0,enums=0;function check(s,depth=0){if(depth>10 || ++nodes>5000)fail('STRUCTURED_SCHEMA_LIMIT');enums+=s.enum?.length??0;if(enums>1000)fail('STRUCTURED_SCHEMA_LIMIT');if(s.properties){properties+=Object.keys(s.properties).length;Object.values(s.properties).forEach(v=>check(v,depth+1));}if(s.items)check(s.items,depth+1);s.anyOf?.forEach(v=>check(v,depth));}check(schema);
 const bytes=new TextEncoder().encode(JSON.stringify(schema)).length;if(bytes>100000 || properties>5000)fail('STRUCTURED_SCHEMA_LIMIT');
 const responseFormat={type:'json_schema',json_schema:{name:'rpg05_turn',strict:true,schema}};
 return {responseFormat,sha256:hashValue(responseFormat),cardSha256:hashValue(card),compilerVersion:'rpg05-structured/1',localValidationRequired:true,localOnlyConstraints:['uniqueness','cross references','state permissions','complete frozen contract validation']};
}
