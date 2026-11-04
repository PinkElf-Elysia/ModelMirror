import {readFile} from 'node:fs/promises';
const resource = name => readFile(new URL(name,import.meta.url),'utf8');
export async function renderSetup(){
  const [template,json]=await Promise.all([resource('./card.html'),resource('./resources.json')]);
  const catalog=JSON.parse(json);
  return template.replace(/__XINGJIAN_(worldDB|commonTalents)__/g,(_,key)=>JSON.stringify(catalog[key]).replaceAll('<','\u003c'));
}
