import {digest,copy} from './contracts.mjs';
import {fixtureModel} from './sources.mjs';
import {characterDocument} from '../document.mjs';import {blankDocument} from '../../authoring/contracts.mjs';
export function source(){
 const doc=characterDocument(blankDocument());Object.assign(doc.character.card.data,{name:'旅人',description:'原创测试角色',first_mes:'你好',scenario:'茶亭'});
 const members=[{id:'player',kind:'player',name:'玩家',identity:{id:null,name:'玩家'},intervals:[{from:1,until:null}]},{id:'actor',kind:'character',name:'旅人',intervals:[{from:1,until:null}],model:fixtureModel()}];const b={members,scene:'茶亭'};
 const messages=['你好','想听故事','一个故事','继续','另一个故事'].map((content,i)=>({id:'m'+i,revisionId:'r'+i,speakerId:i%2?'player':'actor',content,visibility:{kind:'public'},addressedTo:[],provenance:{kind:i===0?'opening':i%2?'player-input':'model-result'},generation:i>0&&i%2===0?{context:[{role:'user',content:'想听故事'}],model:fixtureModel(),requestId:'old'+i}:null,slotId:'s'+i,boundary:copy(b)}));
 const s={kind:'single',id:'test-source',revision:4,runtimeHash:'old',pending:false,messages,initial:b,current:copy(b),derived:[{id:'known',ownerId:'actor',content:'derived-secret',dependencies:['r2'],prefixHash:null},{id:'unknown',ownerId:'actor',content:'manual-private',dependencies:null,prefixHash:null}],cards:{actor:{document:doc}},evidence:{},budget:{calls:4,microUsd:100000}};s.sourceHash=digest(s);return s;
}
