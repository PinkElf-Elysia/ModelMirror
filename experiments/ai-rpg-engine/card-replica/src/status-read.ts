// Join overlapping status reads so a slow reply is not perpetually superseded
// by the polling interval. A different session starts a separate read.
export function createStatusRead<T>() {
 let active: {key:string;promise:Promise<T>} | null = null;
 return {
  run(key:string, read:()=>Promise<T>):Promise<T> {
   if(active?.key===key)return active.promise;
   const promise=Promise.resolve().then(read);
   active={key,promise};
   const clear=()=>{if(active?.promise===promise)active=null;};
   void promise.then(clear,clear);
   return promise;
  },
  clear(){active=null;}
 };
}
