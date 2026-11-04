export function previewFetch(fetcher, publish, secret, config) {
  return async (url, options) => {
    const response = await fetcher(url, options);
    if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) return response;
    const decoder = new TextDecoder();
    let pending = '', text = '', model, provider, disabled = false;
    const tap = new TransformStream({
      transform(bytes, controller) {
        controller.enqueue(bytes);
        if (disabled) return;
        try {
          pending = (pending + decoder.decode(bytes, {stream:true})).replaceAll('\r\n','\n');
          const blocks = pending.split('\n\n');
          pending = blocks.pop();
          if (pending.length > 262144) throw Error();
          for (const block of blocks) {
            const data = block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
            if (!data || data==='[DONE]') continue;
            const event = JSON.parse(data);
            model=event.model||model; provider=event.provider||provider;
            const chunk=event.choices?.[0]?.delta?.content??'';
            if(event.error || typeof chunk!=='string' || (model && ![config.model,config.model+'-20260902'].includes(model)) || (provider && provider!==config.provider)) throw Error();
            text+=chunk;
            if(text.length>262144 || text.includes(secret))throw Error();
          }
          if(model && provider)publish(text.slice(0,Math.max(0,text.length-secret.length+1)));
        } catch { disabled=true; publish(''); }
      }
    });
    return new Response(response.body.pipeThrough(tap),{status:response.status,headers:response.headers});
  };
}
