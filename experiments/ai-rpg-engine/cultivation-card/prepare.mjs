import {assembleOpening,cultivationHttp as legacyHttp} from './host.mjs';
import {assembleWorldbook} from './worldbook.mjs';
import {sha} from '../plugins/catalog.mjs';

export async function cultivationHttp(options) {
  const {route,method,data,send}=options;
  if(route==='api/prepare' && method==='POST') {
    const [,opening]=await assembleOpening(data?.archive);
    return send(200,{prepared:true,systemHash:sha(assembleWorldbook({input:opening.content}).system),archiveHash:sha(opening.content)});
  }
  return legacyHttp(options);
}
