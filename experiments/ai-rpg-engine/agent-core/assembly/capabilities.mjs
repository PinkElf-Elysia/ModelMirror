import {clone,keys,need} from '../contracts.mjs';
// Receives an already scoped projection, never schema-specific paths or other sessions.
export function countRegistered(project){
 return {name:'count_registered',version:'1',effect:'compute',maxCalls:1,maxBytes:8192,
  tool:{type:'function',function:{name:'count_registered',description:'统计指定登记类别的物资数量；未知数量不猜成零，不改变状态。',parameters:{type:'object',additionalProperties:false,required:['category'],properties:{category:{type:'string',minLength:1,maxLength:80}}}}},
  validate(a){keys(a,['category']);need(typeof a.category==='string'&&a.category.length>0&&a.category.length<=80,'COUNT_CATEGORY');},
  run(r,a){const rows=clone(project(r.state)).filter(x=>x.category===a.category);let total=0;const unknown=[];
   for(const x of rows){if(x.quantity===null||x.quantity===undefined){unknown.push(x.id);continue;}
    need(Number.isSafeInteger(x.quantity)&&x.quantity>=0,'COUNT_QUANTITY');total+=x.quantity;need(Number.isSafeInteger(total),'COUNT_OVERFLOW');}
   return {stateRevision:r.stateRevision,category:a.category,matched:rows.length,knownTotal:total,unknown,
    coverage:rows.length===0?'no-match':unknown.length?'unset-quantities':'registered-category-complete',
    meaning:'仅当前授权投影中的登记条目；不代表全部世界物资，也不推断容器关系。',sourceIds:rows.map(x=>x.id)};
  }};
}
