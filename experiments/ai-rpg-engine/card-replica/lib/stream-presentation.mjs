// Read-only browser projection. The same allowlist also renders saved replies.
import {cardPresentation, cardDisplay} from './card-presentation.mjs';
import {harryPotterPresentation} from './harry-potter-presentation.mjs';
export function streamPresentation(raw, card) {
  if (typeof raw !== 'string' || raw.length > 2097152) throw new RangeError('STREAM_SOURCE_LIMIT');
  if (card === 'harry-potter') return harryPotterPresentation(raw);
  const profile = card === 'earth' || card === 'god' || card === 'douluo' ? card : 'archive';
  // Use the throwing renderer first: a partial parse failure must not replace the
  // last readable frame with the saved-reply error message.
  const projected = cardPresentation(raw, profile);
  return profile === 'archive' ? projected.html : cardDisplay(raw, profile);
}
