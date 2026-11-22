import {check,copy} from '../revisions/contracts.mjs';

export const variablePolicy='scoped-text-conditions/1';
const validName=name=>typeof name==='string'&&/^[\p{L}_][\p{L}\p{N}_]{0,31}$/u.test(name)&&!['__proto__','constructor','prototype'].includes(name);
const validValue=value=>typeof value==='string'&&value.isWellFormed()&&value.length<=256&&!/[{}<>@\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(value);

export function forkVariables(parent,position,tip,enable){
  if(parent&&(tip||position>parent.enabledAfter)){
    return {enabledAfter:parent.enabledAfter,changes:copy(parent.changes.filter(c=>tip||c.after<position))};
  }
  return enable?{enabledAfter:position,changes:[]}:null;
}

export function variableView(track,actor){
  const shared=new Map(),personal=new Map();
  for(const {scope,memberId,key,value} of track.changes){
    if(scope==='private'&&memberId!==actor)continue;
    const map=scope==='shared'?shared:personal;
    if(value===null)map.delete(key);else map.set(key,value);
  }
  return {shared:Object.fromEntries(shared),private:Object.fromEntries(personal)};
}

export function setVariable(track,position,actors,{scope,memberId,key,value}){
  check(track,'VARIABLE_POLICY_DISABLED');
  check((scope==='shared'&&memberId===null)||(scope==='private'&&actors.includes(memberId)),'VARIABLE_SCOPE');
  check(validName(key)&&(value===null||validValue(value)),'VARIABLE_INPUT');
  const current=variableView(track,memberId)[scope];
  if((value===null&&!Object.hasOwn(current,key))||current[key]===value)return;
  check(value===null||Object.hasOwn(current,key)||Object.keys(current).length<32,'VARIABLE_SCOPE_LIMIT');
  check(track.changes.length<256,'VARIABLE_HISTORY_LIMIT');
  track.changes.push({after:position,scope,memberId,key,value});
}

// Only balanced, top-level tokens are instructions. Substituted text is never tokenized.
export function renderVariables(text,source,values,legacy){
  const parts=[],items=[],stack=[];
  let active=true,end=0,start=0,braces=0,nested=false,steps=0;
  function record(offset,token,value,reason){
    check(items.length<128,'MACRO_FIELD_LIMIT');
    items.push({source,offset,token,value,...(reason?{reason}:{})});
  }
  for(const match of text.matchAll(/\{\{|\}\}/g)){
    if(match[0]==='{{'){
      if(braces++===0){start=match.index;nested=false;}else nested=true;
      continue;
    }
    if(!braces||--braces)continue;
    if(active)parts.push(text.slice(end,start));
    end=match.index+2;
    const token=text.slice(start,end),body=token.slice(2,-2);
    if(!nested&&/^(?:var|if)(?:::|$)|^(?:else|\/if)$/i.test(body)){
      check(++steps<=256,'VARIABLE_FIELD_LIMIT');
      const [raw,scope,key,...args]=body.split('::'),op=raw.toLowerCase();
      if(op==='else'||op==='/if'){
        const frame=stack.at(-1);
        check(frame,'VARIABLE_CONDITION_SYNTAX');
        if(op==='else'){check(!frame.hadElse,'VARIABLE_CONDITION_SYNTAX');frame.hadElse=true;active=frame.parent&&!frame.match;}
        else {active=frame.parent;stack.pop();}
        continue;
      }
      check(['shared','private'].includes(scope)&&validName(key)&&args.length<=(op==='if'?1:0)&&(!args.length||validValue(args[0])),'VARIABLE_MACRO_SYNTAX');
      if(op==='if')check(stack.length<4,'VARIABLE_NESTING_LIMIT');
      const exists=active&&Object.hasOwn(values[scope],key);
      const value=exists?values[scope][key]:'';
      if(op==='var'){
        if(active){record(start,token,value,exists?'read':'missing');parts.push(value);}
      }else{
        const match=exists&&(args.length===0||args[0]===value);
        if(active){
          record(start,token,match,exists?'condition':'missing');
          items.at(-1).observed=exists?value:null;
        }
        stack.push({parent:active,match,hadElse:false});active=match;
      }
    }else if(active){
      if(legacy&&!nested&&/^\{\{(?:date\}\}|pick::)/i.test(token)){
        const result=legacy(token,start);
        for(const item of result.items)record(item.offset,item.token,item.value);
        parts.push(result.text);
      }else parts.push(token);
    }
  }
  check(!stack.length&&!(braces&&/^\{\{(?:var|if)(?:::|$)/i.test(text.slice(start))),'VARIABLE_CONDITION_SYNTAX');
  parts.push(text.slice(end));
  return {text:parts.join(''),items};
}
