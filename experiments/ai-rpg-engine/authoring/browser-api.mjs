// Reading never creates an uncertain operation; writes retain their original
// journal and ID when transport or the acknowledgement is unavailable.
export function createAuthoringApi(root,request=globalThis.fetch){
 return async(path,body)=>{
  const reading=body===undefined,unknown=()=>({code:reading?'NETWORK_READ_FAILED':'NETWORK_RESULT_UNKNOWN'});
  let response,value;
  try{response=await request(root+path,reading?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body),cache:'no-store'});}
  catch{throw unknown();}
  try{value=await response.json();}catch{throw unknown();}
  if(!response.ok)throw {...(value?.error||unknown()),httpStatus:response.status};
  return value;
 };
}
