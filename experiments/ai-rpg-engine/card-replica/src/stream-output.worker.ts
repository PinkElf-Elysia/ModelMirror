import {streamPresentation} from '../lib/stream-presentation.mjs';
self.onmessage = (event: MessageEvent<{raw: string; card: string}>) => {
  try { self.postMessage({html: streamPresentation(event.data.raw, event.data.card)}); }
  catch { self.postMessage({error: true}); }
};
