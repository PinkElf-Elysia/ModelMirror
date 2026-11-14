import {parseFragment} from 'parse5';
const omitted=new Set(['script','style','template','noscript','iframe','svg','canvas','button','input','select','textarea','details','nav','audio','video','cot','think','thinking']);
const paragraphs=new Set(['p','div','section','article','h1','h2','h3','h4','li','tr','br']);
// Parse as inert data, never mount author markup or read HTML comments.
export function extractSpeechText(raw){
 if(typeof raw!=='string')return '';
 const source=raw.replace(/```(?:html)?\s*\n([\s\S]*?)```/gi,'$1');
 const tree=parseFragment(source),stories=[];
 const attr=(n,k)=>n.attrs?.find(a=>a.name===k)?.value;
 function hidden(n){return omitted.has(n.tagName)||n.attrs?.some(a=>a.name==='hidden'||a.name==='aria-hidden'&&a.value==='true')||/(?:display\s*:\s*none|visibility\s*:\s*hidden)/i.test(attr(n,'style')||'')||/(?:^|\s)(?:status-details|status-panel|danmaku|toolbar)(?:\s|$)/.test(attr(n,'class')||'');}
 function find(n){if(hidden(n))return;if(/(?:^|\s)(?:rpg-narrative|main-text)(?:\s|$)/.test(attr(n,'class')||'')){stories.push(n);return;}for(const c of n.childNodes||[])find(c);}
 function text(n){if(hidden(n))return '';if(n.nodeName==='#text')return n.value;if(n.nodeName==='#comment')return '';return (n.childNodes||[]).map(text).join('')+(paragraphs.has(n.tagName)?'\n':'');}
 find(tree);
 return (stories.length?stories:[tree]).map(text).join('\n').replace(/!\[[^\]]*\]\([^)]*\)/g,'').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/^\s{0,3}#{1,6}\s+/gm,'').replace(/\*\*|__/g,'').replace(/[\t ]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
}
