/** Launch from the canonical loopback site so the workbench can keep its origin checks. */
export function localAuthoringHref(port: string | undefined, page: Pick<Location, 'protocol' | 'hostname' | 'port'>): string | undefined {
  if (page.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(page.hostname)) return;
  if (!port || !/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535) return;
  if (Number(port) === Number(page.port || 80)) return;
  if (page.hostname === 'localhost') {
    const appPort = page.port ? `:${page.port}` : '';
    return `http://127.0.0.1${appPort}/rpg/authoring`;
  }
  return `http://127.0.0.1:${port}/`;
}
