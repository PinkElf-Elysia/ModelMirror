import {parseFragment,serialize} from 'parse5';
import {cardPresentation} from '../card-replica/lib/card-presentation.mjs';

const templateTags = new Set('text msg npc n t blk r k v item ti nm'.split(' '));
const markup = new Set('details summary div span p br b strong em i ul ol li hr h2 h3 h4 table thead tbody tr td th pre code blockquote'.split(' '));
const allowedClasses = new Set('top-status-bar canshu hero-bar hero-btn hero-modal modal-box action-box action-panel'.split(' '));
const omit = new Set('script style iframe object embed svg math template textarea form button select img audio video think thinking'.split(' '));

export function genshinPresentation(raw) {
  if (!/<(?:msg|npc|text|blk|item)\b|class=["'](?:hero-bar|top-status-bar)/i.test(raw)) {
    return cardPresentation(raw,'archive').html;
  }
  function project(node,depth) {
    if (depth>160) throw new RangeError('PRESENTATION_DEPTH_LIMIT');
    if (node.nodeName==='#text') return [node];
    if (omit.has(node.tagName) || node.namespaceURI!=='http://www.w3.org/1999/xhtml') return [];
    node.childNodes=(node.childNodes||[]).flatMap(child=>project(child,depth+1));
    if (!templateTags.has(node.tagName) && !markup.has(node.tagName)) return node.childNodes;
    node.attrs=node.attrs.flatMap(({name,value})=>{
      if (name==='c' && ['msg','npc'].includes(node.tagName) && /^[\p{L}\p{N}· ._-]{1,64}$/u.test(value)) return [{name,value}];
      if (name!=='class') return [];
      const classes=value.split(/\s+/).filter(token=>allowedClasses.has(token));
      return classes.length?[{name,value:classes.join(' ')}]:[];
    });
    return [node];
  }
  const fragment=parseFragment(raw);
  fragment.childNodes=fragment.childNodes.flatMap(n=>project(n,0));
  return '<div class="genshin-story">'+serialize(fragment)+'</div>';
}
