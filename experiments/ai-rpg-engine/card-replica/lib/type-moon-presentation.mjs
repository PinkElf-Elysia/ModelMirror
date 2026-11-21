import {cardDisplay} from './card-presentation.mjs';

export function typeMoonResponse(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  const projected = {...value};
  if (Array.isArray(value.turns)) projected.turns = value.turns.map(turn =>
    typeof turn.raw === 'string' ? {...turn, html: cardDisplay(turn.raw,'type-moon')} : turn);
  if (value.session) projected.session = typeMoonResponse(value.session);
  return projected;
}
