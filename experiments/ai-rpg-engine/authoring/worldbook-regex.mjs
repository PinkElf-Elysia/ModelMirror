import {Worker, isMainThread, parentPort, workerData} from 'node:worker_threads';

const maximum = Object.freeze({jobs:2000, patterns:200, patternChars:2000, messages:41, payloadChars:2000000, comparisons:20000000});
const problem = (code, details={}) => Object.assign(new Error(code), {code,details});
function validate(jobs,messages) {
  if(!Array.isArray(jobs)||jobs.length>maximum.jobs||!Array.isArray(messages)||messages.length>maximum.messages)throw problem('AUTHORING_WORLDBOOK_REGEX_INPUT');
  let chars=0,cost=0;const ids=new Set();
  for(const text of messages){if(typeof text!=='string'||!text.isWellFormed())throw problem('AUTHORING_WORLDBOOK_REGEX_INPUT');chars+=text.length;}
  for(const job of jobs){
    if(!job||typeof job.id!=='string'||!job.id.length||job.id.length>96||ids.has(job.id)||!Array.isArray(job.patterns)||!job.patterns.length||job.patterns.length>maximum.patterns||!Array.isArray(job.messageIndexes)||job.messageIndexes.length>maximum.messages||new Set(job.messageIndexes).size!==job.messageIndexes.length||job.messageIndexes.some(i=>!Number.isSafeInteger(i)||i<0||i>=messages.length))throw problem('AUTHORING_WORLDBOOK_REGEX_INPUT');
    ids.add(job.id);
    for(const pattern of job.patterns){if(typeof pattern!=='string'||!pattern.isWellFormed()||[...pattern].length>maximum.patternChars)throw problem('AUTHORING_WORLDBOOK_REGEX_INPUT');chars+=pattern.length;}
    cost+=job.patterns.length*job.messageIndexes.reduce((n,i)=>n+messages[i].length+1,0);
  }
  if(chars>maximum.payloadChars||cost>maximum.comparisons)throw problem('AUTHORING_WORLDBOOK_REGEX_CAPACITY');
}

// The worker executes this fixed module. Patterns are data, never executable source.
if(!isMainThread) {
  try {
    const {jobs,messages}=workerData;validate(jobs,messages);
    const results=jobs.map(job=>({id:job.id,hits:job.patterns.map((pattern,keywordIndex)=>{
      let expression;try{expression=new RegExp(pattern,'iu');}catch{throw problem('AUTHORING_WORLDBOOK_REGEX_SYNTAX',{entryId:job.id,keywordIndex});}
      for(const messageIndex of job.messageIndexes){const match=expression.exec(messages[messageIndex]);if(match)return {keywordIndex,messageIndex,offset:match.index};}
      return null;
    })}));
    parentPort.postMessage({ok:true,results});
  }catch(error){parentPort.postMessage({ok:false,code:error.code||'AUTHORING_WORLDBOOK_REGEX_FAILED',details:error.details||{}});}
  parentPort.close();
}

export async function evaluateRegexBatch(jobs,messages,{timeoutMs=1000}={}) {
  validate(jobs,messages);
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>2000)throw problem('AUTHORING_WORLDBOOK_REGEX_TIMEOUT_CONFIG');
  if(!jobs.length)return [];
  return new Promise((resolve,reject)=>{
    let worker,settled=false,timer;
    const finish=async(error,value)=>{
      if(settled)return;settled=true;clearTimeout(timer);
      try{if(worker)await worker.terminate();}catch{error??=problem('AUTHORING_WORLDBOOK_REGEX_FAILED');}
      if(error)reject(error);else resolve(value);
    };
    try {
      worker=new Worker(new URL(import.meta.url),{workerData:{jobs,messages},env:{},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:64,maxYoungGenerationSizeMb:16,stackSizeMb:4}});
      timer=setTimeout(()=>finish(problem('AUTHORING_WORLDBOOK_REGEX_TIMEOUT')),timeoutMs);
      worker.once('message',message=>{if(message?.ok===true&&Array.isArray(message.results))void finish(null,message.results);else void finish(problem(message?.code||'AUTHORING_WORLDBOOK_REGEX_FAILED',message?.details));});
      worker.once('error',()=>void finish(problem('AUTHORING_WORLDBOOK_REGEX_FAILED')));
      worker.once('exit',()=>{if(!settled)void finish(problem('AUTHORING_WORLDBOOK_REGEX_FAILED'));});
    }catch{void finish(problem('AUTHORING_WORLDBOOK_REGEX_FAILED'));}
  });
}
