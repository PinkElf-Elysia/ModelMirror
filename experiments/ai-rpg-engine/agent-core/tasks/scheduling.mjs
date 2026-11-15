import {need} from '../contracts.mjs';
// Shared bounded scheduling; no workflow interpreter or autonomous task creation.
export class TaskScheduling{
 constructor(){this.busy=new Set();this.activeJobs=0;this.maxJobs=0;this.waiters=[];}
 async exclusive(run,fn){need(!this.busy.has(run),'R7_RUN_BUSY');this.busy.add(run);try{return await fn();}finally{this.busy.delete(run);}}
 async slot(fn){if(this.activeJobs>=2)await new Promise(ok=>this.waiters.push(ok));else this.activeJobs++;
  this.maxJobs=Math.max(this.maxJobs,this.activeJobs);try{return await fn();}finally{const next=this.waiters.shift();if(next)next();else this.activeJobs--;}}
}
