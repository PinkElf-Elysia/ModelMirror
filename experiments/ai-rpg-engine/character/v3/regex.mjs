// Thompson NFA. No card input is passed to the native regular expression engine.
export const limits=Object.freeze({keys:8,pattern:256,depth:4,repeat:32,states:512,scan:16384,steps:1000000});
export class RuleError extends Error{}
export class StepLimit extends Error{}
const reject=reason=>{throw new RuleError(reason);};
const fold=c=>c>='A'&&c<='Z'?c.toLowerCase():c;
function compile(raw){
 if(typeof raw!=='string'||raw.length>2*limits.pattern+5)reject('regex-limit');
 if(raw[0]!=='/')reject('regex-unsupported');
 const end=raw.lastIndexOf('/');if(end<1)reject('regex-invalid');
 const flags=raw.slice(end+1);if(/[^imu]/.test(flags))reject('regex-unsupported');
 if(new Set(flags).size!==flags.length)reject('regex-invalid');
 const str=[...raw.slice(1,end)];if(str.length>limits.pattern)reject('regex-limit');
 let pos=0;const peek=()=>str[pos],take=()=>str[pos++],insensitive=flags.includes('i');
 function escaped(){
  const c=take();if(c===undefined)reject('regex-invalid');
  if(c==='n')return '\n';if(c==='r')return '\r';if(c==='t')return '\t';
  if(!'\\/.^$|()?*+[]{}-'.includes(c))reject('regex-unsupported');return c;
 }
 function charset(){
  const negative=peek()==='^';if(negative)take();const ranges=[];
  const read=()=>{const c=take();return c==='\\'?escaped():c;};
  while(pos<str.length&&peek()!==']'){
   const a=read();let b=a;
   if(peek()==='-'&&str[pos+1]!==']'){take();if(pos>=str.length)reject('regex-invalid');b=read();}
   if(a.codePointAt(0)>b.codePointAt(0))reject('regex-invalid');ranges.push([a.codePointAt(0),b.codePointAt(0)]);
  }
  if(take()!==']'||!ranges.length)reject('regex-invalid');
  const has=c=>ranges.some(([a,b])=>c>=a&&c<=b);
  return c=>{const n=c.codePointAt(0);return negative!==!!(has(n)||(insensitive&&((n>=65&&n<=90&&has(n+32))||(n>=97&&n<=122&&has(n-32)))));};
 }
 function atom(depth){
  const c=take();
  if(c==='('){if(depth===limits.depth)reject('regex-limit');if(peek()==='?')reject('regex-unsupported');const g=alternative(depth+1);if(take()!==')')reject('regex-invalid');return g;}
  if(c==='^'||c==='$')return {kind:c,empty:true};
  if(c==='[')return {kind:'char',test:charset()};
  if(c==='.')return {kind:'char',test:x=>!['\n','\r','\u2028','\u2029'].includes(x)};
  if('/*+?{}]'.includes(c))reject('regex-invalid');
  const v=c==='\\'?escaped():c;
  return {kind:'char',test:insensitive?x=>fold(x)===fold(v):x=>x===v};
 }
 function sequence(depth){
  const seq=[];
  while(pos<str.length&&peek()!=='|'&&peek()!==')'){
   let a=atom(depth),min,max;
   if(peek()==='{'){
    take();const number=()=>{let n='';while(pos<str.length&&str[pos]>='0'&&str[pos]<='9')n+=take();if(!n)reject('regex-invalid');return Number(n);};
    min=number();if(peek()===','){take();max=number();}else max=min;
    if(take()!=='}'||min>max)reject('regex-invalid');if(max>limits.repeat)reject('regex-limit');
   }else if(['?','+','*'].includes(peek())){const q=take();min=q==='+'?1:0;max=q==='?'?1:Infinity;}
   if(min!==undefined){if(a.kind==='^'||a.kind==='$')reject('regex-invalid');if(peek()==='?')reject('regex-unsupported');a={kind:'repeat',child:a,min,max,empty:min===0||a.empty};}
   seq.push(a);
  }
  return {kind:'seq',items:seq,empty:seq.every(a=>a.empty)};
 }
 function alternative(depth){const items=[sequence(depth)];while(peek()==='|'){take();items.push(sequence(depth));}return {kind:'alt',items,empty:items.some(a=>a.empty)};}
 const tree=alternative(0);if(pos!==str.length)reject('regex-invalid');if(tree.empty)reject('regex-empty');
 const states=[];
 function state(){if(states.length>=limits.states)reject('regex-limit');states.push([]);return states.length-1;}
 const edge=(a,b,kind='epsilon',test)=>states[a].push({to:b,kind,test});
 function emit(node,start,finish){
  if(node.kind==='seq'){let prev=start;node.items.forEach((v,i)=>{const next=i===node.items.length-1?finish:state();emit(v,prev,next);prev=next;});if(!node.items.length)edge(start,finish);}
  else if(node.kind==='alt'){for(const v of node.items){const first=state();edge(start,first);emit(v,first,finish);}}
  else if(node.kind==='repeat'){
   let prev=start;for(let i=0;i<node.min;i++){const next=state();emit(node.child,prev,next);prev=next;}
   if(node.max===Infinity){const from=state();edge(prev,from);emit(node.child,from,prev);}
   else for(let i=node.min;i<node.max;i++){edge(prev,finish);const next=state();emit(node.child,prev,next);prev=next;}
   edge(prev,finish);
  }else edge(start,finish,node.kind,node.test);
 }
 const start=state(),finish=state();emit(tree,start,finish);
 return {states,start,finish,multiline:flags.includes('m')};
}
export function compileKeys(keys){
 if(!keys.length)return {status:'regex-invalid'};if(keys.length>limits.keys)return {status:'regex-limit'};
 const programs=[];for(const [key,raw] of keys.entries()){try{programs.push(compile(raw));}catch(e){if(!(e instanceof RuleError))throw e;return {status:e.message,key};}}
 return {programs};
}
export const meter=()=>({remaining:limits.steps,cache:new WeakMap()});
export function match(program,chars,clock){
 const {states,start,finish,multiline}=program;
 const tick=()=>{if(clock.remaining<=0)throw new StepLimit();clock.remaining--;};
 let seeds=new Set();
 for(let i=0;i<=chars.length;i++){
  seeds.add(start);const queue=[...seeds],seen=new Set(queue),next=new Set();
  for(let k=0;k<queue.length;k++){
   tick();const n=queue[k];if(n===finish)return true;
   for(const edge of states[n]){
    tick();
    if(edge.kind==='char'){if(i<chars.length&&edge.test(chars[i]))next.add(edge.to);continue;}
    if(edge.kind==='^'&&i!==0&&!(multiline&&chars[i-1]==='\n'))continue;
    if(edge.kind==='$'&&i!==chars.length&&!(multiline&&chars[i]==='\n'))continue;
    if(!seen.has(edge.to)){seen.add(edge.to);queue.push(edge.to);}
   }
  }
  seeds=next;
 }
 return false;
}
