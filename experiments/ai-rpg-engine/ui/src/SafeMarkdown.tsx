import { useRef, useState } from 'react';
import Markdown from 'react-markdown';
export function safeExternalUrl(value: string): string {
  if (!/^https?:\/\//iu.test(value) || /[\u0000-\u0020\u007f]/u.test(value)) return '';
  try { const url = new URL(value); return url.username || url.password ? '' : url.href; } catch { return ''; }
}
export function SafeMarkdown({ text }: { text: string }) {
  const [link, setLink] = useState(''), dialog = useRef<HTMLDialogElement>(null);
  function ask(value: string) { setLink(value); dialog.current?.showModal(); }
  return <><Markdown skipHtml allowedElements={['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'blockquote', 'ul', 'ol', 'li', 'pre', 'code', 'hr', 'br', 'a', 'img']} urlTransform={safeExternalUrl} components={{
    h1: 'h3', h2: 'h3', h4: 'h3', h5: 'h3', h6: 'h3',
    a: ({ href, children }) => href ? <button type="button" className="inline-link" onClick={() => ask(href)}>{children} ↗</button> : <span>{children}</span>,
    img: ({ src, alt }) => typeof src === 'string' && src ? <button type="button" className="inline-link" onClick={() => ask(src)}>图片：{alt || '查看链接'} ↗</button> : <span>图片链接不可用</span>,
  }}>{text}</Markdown><dialog ref={dialog} className="link-dialog"><h2>打开外部链接</h2><p className="break-text">{link}</p><div className="dialog-actions"><button type="button" onClick={() => dialog.current?.close()}>返回阅读</button><a className="external-link" href={link || undefined} target="_blank" rel="noopener noreferrer" onClick={() => dialog.current?.close()}>打开链接 ↗</a></div></dialog></>;
}
