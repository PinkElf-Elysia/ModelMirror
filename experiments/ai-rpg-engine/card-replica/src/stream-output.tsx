import {memo, useEffect, useRef, useState} from 'react';
import {patchStreamHtml} from './stream-dom.mjs';

// The parent keys this component by operation, isolating late results and DOM
// interaction state across sends, rewrites and session switches.
export const StreamOutput = memo(function StreamOutput({raw, card}: {raw: string; card: string}) {
  const root = useRef<HTMLDivElement>(null);
  const latest = useRef({raw, card});
  if (latest.current.raw !== raw || latest.current.card !== card) latest.current = {raw, card};
  const submit = useRef<(() => void) | null>(null);
  const [warning, setWarning] = useState('');
  useEffect(() => {
    let worker: Worker | null = null, timer: ReturnType<typeof setTimeout> | undefined;
    let active = true, busy = false, sent: typeof latest.current | null = null;
    const fail = () => {
      if (!active) return;
      clearTimeout(timer); worker?.terminate(); worker = null; busy = false;
      setWarning('实时排版暂不可用，完成后显示完整回复。');
    };
    const send = () => {
      if (!active || busy || sent === latest.current) return;
      try {
        if (!worker) {
          worker = new Worker(new URL('./stream-output.worker.ts', import.meta.url), {type: 'module'});
          worker.onerror = fail;
          worker.onmessage = (event: MessageEvent<{html?: string; error?: boolean}>) => {
            if (!active || event.currentTarget !== worker) return;
            clearTimeout(timer); busy = false;
            if (typeof event.data.html === 'string') {
              if (root.current) patchStreamHtml(root.current, event.data.html);
              setWarning('');
            } else setWarning('正在等待可排版的片段，完成后显示完整回复。');
            if (sent !== latest.current) send();
          };
        }
        busy = true; sent = latest.current;
        worker.postMessage(sent);
        timer = setTimeout(fail, 4000);
      } catch { fail(); }
    };
    submit.current = send;
    send();
    return () => { active = false; submit.current = null; clearTimeout(timer); worker?.terminate(); };
  }, []);
  useEffect(() => { submit.current?.(); }, [raw, card]);
  return <>
    <div ref={root} className={/<[a-z][a-z0-9-]*(?:\s|>)/i.test(raw) ? 'model-output' : 'model-output platform-prose'}/>
    {warning && <p role="status">{warning}</p>}
  </>;
});
