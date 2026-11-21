import {parseFragment, serialize} from 'parse5';
import {cardPresentation} from './card-presentation.mjs';

const tags=new Set('details summary p div span b strong em i u s br hr ul ol li blockquote pre code'.split(' '));
const block=new Set('z step e pos f rl rj rg mc o fbar dm ds zh'.split(' '));
const inline=new Set('d n k v x y'.split(' '));
const drop=new Set('script style template svg math iframe audio video img link object form input button'.split(' '));
const panelCodes=new Set('k w v t p c m zb'.split(' '));
const htmlNS='http://www.w3.org/1999/xhtml';

export function starRailPresentation(raw) {
  if (!/<(?:details|z|o|fbar)[\s>]/i.test(raw)) return cardPresentation(raw,'archive').html;
  function clean(nodes,depth) {
    if (depth>128) return [];
    return nodes.flatMap(node=>{
      if (node.nodeName==='#text') return [{nodeName:'#text',value:node.value}];
      const tag=node.tagName;
      if (!tag || node.namespaceURI!==htmlNS || drop.has(tag)) return [];
      const children=clean(node.childNodes||[],depth+1);
      if (!tags.has(tag) && !block.has(tag) && !inline.has(tag)) return children;
      const name=block.has(tag)?'div':inline.has(tag)?'span':tag;
      const code=node.attrs.find(a=>a.name==='class')?.value;
      const cls=name!==tag?'sr-'+tag:tag==='details' && panelCodes.has(code)?'sr-panel-'+code:'';
      return [{nodeName:name,tagName:name,namespaceURI:htmlNS,attrs:cls?[{name:'class',value:cls}]:[],childNodes:children}];
    });
  }
  const fragment=parseFragment(raw);
  fragment.childNodes=clean(fragment.childNodes,0);
  return '<div class="star-rail-output">'+serialize(fragment)+'</div>';
}
