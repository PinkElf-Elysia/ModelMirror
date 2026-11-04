/** The authoring server accepts same-site navigation on its canonical loopback address. */
export function localAuthoringHref(port: string | undefined, page: Pick<Location, 'protocol' | 'hostname' | 'port'>): string | undefined {
  if (page.protocol !== 'http:' || page.hostname !== '127.0.0.1') return;
  if (!port || !/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535) return;
  if (Number(port) === Number(page.port || 80)) return;
  return `http://127.0.0.1:${port}/`;
}
