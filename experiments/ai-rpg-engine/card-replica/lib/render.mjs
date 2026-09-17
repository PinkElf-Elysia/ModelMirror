import {parseFragment,serialize} from 'parse5';
const allowed=new Set('p br hr div span strong b em i u s blockquote pre code ul ol li details summary table thead tbody tfoot tr th td h1 h2 h3 h4 h5 h6 ruby rt rp'.split(' '));
const blocked=new Set('script style iframe object embed svg math template form input button textarea select link meta base audio video source img'.split(' '));
const styleKeys=new Set('color background-color border-color border-width border-style border-radius padding margin font-size font-weight text-align line-height display white-space'.split(' '));
export function safeHtml(raw){
  const root=parseFragment(raw);
  function clean(parent){
    parent.childNodes=(parent.childNodes||[]).flatMap(n=>{
      if(n.nodeName==='#text')return [n];
      if(n.nodeName==='#comment'||blocked.has(n.tagName))return [];
      clean(n);
      if(!allowed.has(n.tagName))return n.childNodes||[];
      n.attrs=(n.attrs||[]).flatMap(a=>{
        if(['colspan','rowspan'].includes(a.name)&&/^[1-9]\d?$/.test(a.value))return [a];
        if(a.name==='open'&&n.tagName==='details')return [a];
        if(a.name==='class'&&a.value==='main-text'&&n.tagName==='div')return [a];
        if(a.name==='style'){
          const value=a.value.split(';').map(v=>v.split(':')).filter(([k,v])=>styleKeys.has(k?.trim().toLowerCase())&&v&&/^[#\w\s.,%()-]+$/.test(v)&&!/[()\\]|url|expression|position|fixed|sticky|var/i.test(v)).map(([k,v])=>`${k.trim()}:${v.trim()}`).join(';');
          return value?[{name:'style',value}]:[];
        }
        return [];
      });return [n];
    });
    for(const n of parent.childNodes)n.parentNode=parent;
  }clean(root);return serialize(root);
}
export function displayDocument(raw){
 return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; form-action 'none'; base-uri 'none'"><style>body{margin:16px;color:#243344;font:16px/1.9 system-ui;overflow-wrap:anywhere}details{margin:16px 0;padding:14px;border:1px solid #c4d4eb;border-radius:12px;background:#eef4ff}summary{cursor:pointer;font-weight:600}table{width:100%;table-layout:fixed;border-collapse:collapse}td,th{padding:10px;border:1px solid #d4dbea;overflow-wrap:anywhere}pre{white-space:pre-wrap}p{margin:1em 0}</style></head><body>${safeHtml(raw)}</body></html>`;
}
