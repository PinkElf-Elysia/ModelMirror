// Display copies only: never persist this projection or send it to a model.
import {parseFragment, serialize} from 'parse5';
import {cardPresentation} from './card-presentation.mjs';

export function harryPotterPresentation(raw) {
  const tree = parseFragment(cardPresentation(raw, 'archive').html);
  const walk = node => {
    if (node.tagName === 'details') node.attrs = node.attrs.filter(attr => attr.name !== 'open');
    for (const child of node.childNodes || []) walk(child);
  };
  walk(tree);
  return serialize(tree);
}

export function harryPotterResponse(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  const projected = {...value};
  if (Array.isArray(value.turns)) projected.turns = value.turns.map(turn =>
    typeof turn.raw === 'string' ? {...turn, html: harryPotterPresentation(turn.raw)} : turn);
  if (value.session) projected.session = harryPotterResponse(value.session);
  return projected;
}
