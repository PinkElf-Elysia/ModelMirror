import {Marked} from 'marked';
import {parseFragment} from 'parse5';
const parser=new Marked({gfm:true,breaks:true,renderer:{html:token=>escape(token.text)}});
const tags=new Set('p br strong em del ul ol li blockquote pre code hr h1 h2 h3 h4 h5 h6 table thead tbody tr th td a'.split(' '));
const escape=v=>v.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function safeHref(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
// Markdown grammar is existing MIT dependency. HTML input is escaped before
// parsing; a second allowlist strips attributes and active/media elements.
export function markdown(raw){
 if(typeof raw!=='string'||raw.length>200000)throw Error('DAILY_TEXT_INVALID');
 const tree=parseFragment(parser.parse(raw));
 function render(n){if(n.nodeName==='#text')return escape(n.value);const body=(n.childNodes??[]).map(render).join('');if(!tags.has(n.tagName))return body;const tag=n.tagName;let attrs='';if(tag==='a'){const href=safeHref(n.attrs.find(a=>a.name==='href')?.value);if(!href)return body;attrs=' href="'+escape(href)+'" target="_blank" rel="noopener noreferrer"';}return '<'+tag+attrs+'>'+body+(['br','hr'].includes(tag)?'':'</'+tag+'>');}
 return tree.childNodes.map(render).join('');
}
