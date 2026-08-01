const HTTP_BASE = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/api/runtime/client-tools/connect';
const PROTOCOL = 'modelmirror-client-tools-v1';
const RECONNECT_ALARM = 'modelmirror-client-host-reconnect';
let socket = null;
let heartbeatTimer = null;
let reconnectAttempt = 0;
let connected = false;

const getLocal = (keys) => chrome.storage.local.get(keys);
const setLocal = (value) => chrome.storage.local.set(value);

async function capabilities() {
  const response = await fetch(`${HTTP_BASE}/api/runtime/client-tools/capabilities`, { cache: 'no-store' });
  if (!response.ok) throw new Error('无法读取 ModelMirror Client Tool 能力。');
  return response.json();
}

async function connectionFrame() {
  const local = await getLocal(['hostId', 'hostToken', 'pendingPairingCode', 'boundTab']);
  const catalog = await capabilities();
  const tools = catalog.tools.map((item) => ({ name: item.name }));
  const hashes = Object.fromEntries(catalog.tools.map((item) => [item.name, item.schema_hash]));
  const common = {
    protocol: PROTOCOL,
    version: chrome.runtime.getManifest().version,
    capabilities: tools,
    schema_hashes: hashes,
    bound_tab: local.boundTab || {}
  };
  if (local.pendingPairingCode) {
    return { ...common, type: 'pair', pairing_code: local.pendingPairingCode };
  }
  if (local.hostId && local.hostToken) {
    return { ...common, type: 'authenticate', host_id: local.hostId, host_token: local.hostToken };
  }
  return null;
}

async function connect() {
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  const first = await connectionFrame().catch(() => null);
  if (!first) return;
  socket = new WebSocket(WS_URL);
  socket.onopen = () => socket.send(JSON.stringify(first));
  socket.onmessage = async (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'welcome') {
      const update = { hostId: message.host_id, pendingPairingCode: null };
      if (message.host_token) update.hostToken = message.host_token;
      await setLocal(update);
      connected = true;
      reconnectAttempt = 0;
      scheduleHeartbeat();
      broadcastState();
      return;
    }
    if (message.type === 'tool_request') await handleToolRequest(message);
  };
  socket.onclose = () => disconnectAndRetry();
  socket.onerror = () => socket?.close();
}

function disconnectAndRetry() {
  connected = false;
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  socket = null;
  broadcastState();
  reconnectAttempt = Math.min(reconnectAttempt + 1, 8);
  const delayMinutes = Math.max(0.5, Math.min(5, (2 ** reconnectAttempt) / 60));
  chrome.alarms.create(RECONNECT_ALARM, { delayInMinutes: delayMinutes });
}

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function scheduleHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(async () => {
    const { boundTab = {} } = await getLocal(['boundTab']);
    send({ type: 'heartbeat', bound_tab: boundTab });
  }, 20000);
}

async function broadcastState() {
  try { await chrome.runtime.sendMessage({ type: 'state_changed' }); } catch { /* popup closed */ }
}

async function handleToolRequest(request) {
  const cacheKey = `operation:${request.operation_id}`;
  const cached = (await getLocal([cacheKey]))[cacheKey];
  if (cached) {
    send({ ...cached, request_id: request.request_id, operation_id: request.operation_id, tool_call_id: request.tool_call_id });
    return;
  }
  send({ type: 'tool_accepted', request_id: request.request_id });
  try {
    const result = await executeTool(request);
    const response = {
      type: 'tool_result',
      operation_id: request.operation_id,
      tool_call_id: request.tool_call_id,
      result: typeof result.output === 'string' ? result.output : JSON.stringify(result.output),
      metadata: result.metadata || {}
    };
    await rememberOperation(cacheKey, response);
    send({ ...response, request_id: request.request_id });
  } catch (error) {
    const response = {
      type: 'tool_error',
      operation_id: request.operation_id,
      tool_call_id: request.tool_call_id,
      error: String(error?.message || error).slice(0, 1000)
    };
    if (!request.mutating) await rememberOperation(cacheKey, response);
    send({ ...response, request_id: request.request_id });
  }
}

async function rememberOperation(key, response) {
  const { operationKeys = [] } = await getLocal(['operationKeys']);
  const next = [...operationKeys.filter((item) => item !== key), key].slice(-100);
  const update = { operationKeys: next, [key]: response };
  const removed = operationKeys.filter((item) => !next.includes(item));
  await setLocal(update);
  if (removed.length) await chrome.storage.local.remove(removed);
}

async function boundTabOrThrow() {
  const { boundTab } = await getLocal(['boundTab']);
  if (!boundTab?.bound || !Number.isInteger(boundTab.tabId)) throw new Error('当前没有用户授权的绑定标签页。');
  const tab = await chrome.tabs.get(boundTab.tabId);
  const url = new URL(tab.url || 'about:blank');
  if (url.origin !== boundTab.origin) {
    await setLocal({ boundTab: {} });
    broadcastState();
    throw new Error('标签页已导航到新的站点，需要重新点击扩展授权。');
  }
  return { tab, boundTab };
}

async function executeTool(request) {
  const { tab, boundTab } = await boundTabOrThrow();
  if (request.tool_name === 'host_page_screenshot') {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
    const artifact = await uploadScreenshot(request, dataUrl);
    return { output: { artifact_id: artifact.artifact_id, filename: artifact.filename }, metadata: { artifact_id: artifact.artifact_id, size_bytes: artifact.size_bytes } };
  }
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, frameIds: [0] },
    func: runDomTool,
    args: [request.tool_name, request.arguments || {}, boundTab.origin]
  });
  const value = results[0]?.result;
  if (!value?.ok) throw new Error(value?.error || '客户端页面操作失败。');
  return { output: value.output, metadata: value.metadata || {} };
}

async function uploadScreenshot(request, dataUrl) {
  const { hostId, hostToken } = await getLocal(['hostId', 'hostToken']);
  const blob = await (await fetch(dataUrl)).blob();
  if (blob.size > 5 * 1024 * 1024) throw new Error('截图超过 5MB 限制。');
  const body = new FormData();
  body.append('file', blob, 'client-screenshot.png');
  const response = await fetch(`${HTTP_BASE}/api/runtime/client-tool-requests/${request.request_id}/artifact`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${hostToken}`, 'X-ModelMirror-Client-Host-Id': hostId },
    body
  });
  if (!response.ok) throw new Error('截图产物上传失败。');
  return response.json();
}

function runDomTool(toolName, args, authorizedOrigin) {
  const MAX_TEXT = 24000;
  const MAX_ELEMENTS = 500;
  const stateKey = '__modelmirrorClientSnapshot';
  const roots = () => {
    const values = [document];
    const queue = [document.documentElement];
    while (queue.length) {
      const node = queue.shift();
      if (node?.shadowRoot) { values.push(node.shadowRoot); queue.push(...node.shadowRoot.querySelectorAll('*')); }
      if (node?.querySelectorAll) queue.push(...node.querySelectorAll(':scope > *'));
    }
    return values;
  };
  const all = (selector) => roots().flatMap((root) => Array.from(root.querySelectorAll(selector)));
  const byRef = (ref) => all('[data-modelmirror-client-ref]').find((el) => el.getAttribute('data-modelmirror-client-ref') === ref);
  const role = (el) => el.getAttribute('role') || ({ A: 'link', BUTTON: 'button', INPUT: 'textbox', SELECT: 'combobox', TEXTAREA: 'textbox' }[el.tagName] || el.tagName.toLowerCase());
  const name = (el) => (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.innerText || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim().slice(0, 300);
  const sensitive = (el) => {
    const signature = [el.type, el.name, el.id, el.autocomplete, el.getAttribute('aria-label')].join(' ').toLowerCase();
    return /password|passcode|otp|one-time|captcha|verification|cvv|cvc|credit|card.number|auth|secret|token/.test(signature);
  };
  const allowedKey = (key) => /^(Enter|Escape|Tab|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Home|End|PageUp|PageDown|Backspace|Delete|Space)$/.test(key);
  try {
    if (location.origin !== authorizedOrigin) throw new Error('标签页站点授权已失效。');
    if (toolName === 'host_page_read') return { ok: true, output: (document.body?.innerText || '').slice(0, MAX_TEXT), metadata: { title: document.title, url: location.href, truncated: (document.body?.innerText || '').length > MAX_TEXT } };
    if (toolName === 'host_page_snapshot') {
      all('[data-modelmirror-client-ref]').forEach((el) => el.removeAttribute('data-modelmirror-client-ref'));
      const revision = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
      const elements = all('a[href],button,input,select,textarea,[role],[tabindex]').filter((el) => !el.hidden && el.getAttribute('aria-hidden') !== 'true').slice(0, MAX_ELEMENTS);
      const items = elements.map((el, index) => {
        const ref = `mm:${revision}:${index}`;
        el.setAttribute('data-modelmirror-client-ref', ref);
        return { ref, role: role(el), name: name(el), disabled: Boolean(el.disabled), sensitive: sensitive(el) };
      });
      window[stateKey] = { revision, origin: location.origin };
      return { ok: true, output: { title: document.title, url: location.href, revision, elements: items }, metadata: { element_count: items.length, truncated: elements.length >= MAX_ELEMENTS } };
    }
    if (toolName === 'host_page_navigate') {
      const target = new URL(String(args.url || ''), location.href);
      if (!['http:', 'https:'].includes(target.protocol) || target.origin !== authorizedOrigin) throw new Error('客户端导航仅允许当前已授权 origin。');
      location.assign(target.href);
      return { ok: true, output: { navigating: true, url: target.href } };
    }
    if (toolName === 'host_page_scroll') { window.scrollBy({ top: Math.max(-5000, Math.min(5000, Number(args.delta_y || 600))), behavior: 'smooth' }); return { ok: true, output: { scroll_y: window.scrollY } }; }
    if (toolName === 'host_page_wait_for') return new Promise((resolve) => setTimeout(() => resolve({ ok: true, output: { waited_ms: Math.max(100, Math.min(10000, Number(args.milliseconds || 500))) } }), Math.max(100, Math.min(10000, Number(args.milliseconds || 500)))));
    const el = byRef(String(args.ref || ''));
    if (!el) throw new Error('元素引用已过期，请重新获取 snapshot。');
    const refRevision = String(args.ref || '').split(':')[1];
    if (window[stateKey]?.revision !== refRevision || window[stateKey]?.origin !== location.origin) throw new Error('元素引用已过期，请重新获取 snapshot。');
    if (toolName === 'host_page_click') el.click();
    else if (toolName === 'host_page_hover') el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    else if (toolName === 'host_page_fill') {
      if (sensitive(el)) throw new Error('禁止自动填写密码、支付、验证码或身份验证字段。');
      el.focus(); el.value = String(args.value || '').slice(0, 10000); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (toolName === 'host_page_select') {
      if (sensitive(el)) throw new Error('禁止自动填写敏感字段。');
      el.value = String(args.value || ''); el.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (toolName === 'host_page_press') {
      const key = String(args.key || ''); if (!allowedKey(key)) throw new Error('按键不在允许列表中。');
      el.focus(); el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })); el.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }));
    } else throw new Error('不支持的客户端工具。');
    return { ok: true, output: { ok: true, tool: toolName, ref: args.ref }, metadata: { title: document.title, url: location.href } };
  } catch (error) { return { ok: false, error: String(error?.message || error) }; }
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  (async () => {
    if (message.type === 'popup_state') {
      const local = await getLocal(['hostId', 'boundTab']);
      return { connected, hostId: local.hostId, boundTab: local.boundTab || {} };
    }
    if (message.type === 'pair') {
      await setLocal({ pendingPairingCode: message.pairingCode });
      if (socket) socket.close();
      await connect();
      return { ok: true };
    }
    if (message.type === 'bind_active_tab') {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id || !tab.url) throw new Error('无法读取当前标签页。');
      const url = new URL(tab.url);
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('只能绑定 HTTP/HTTPS 页面。');
      await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [0] }, func: () => true });
      const boundTab = { bound: true, tabId: tab.id, origin: url.origin, title: (tab.title || '').slice(0, 200), url: tab.url };
      await setLocal({ boundTab });
      send({ type: 'host_state', bound_tab: boundTab });
      broadcastState();
      return { ok: true };
    }
    if (message.type === 'unbind_tab') {
      await setLocal({ boundTab: {} });
      send({ type: 'host_state', bound_tab: {} });
      broadcastState();
      return { ok: true };
    }
    return { ok: false, error: '未知操作。' };
  })().then(respond).catch((error) => respond({ ok: false, error: String(error?.message || error) }));
  return true;
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (!changeInfo.url) return;
  const { boundTab } = await getLocal(['boundTab']);
  if (!boundTab?.bound || boundTab.tabId !== tabId) return;
  let origin = '';
  try { origin = new URL(tab.url || changeInfo.url).origin; } catch { /* invalid URL */ }
  if (origin !== boundTab.origin) {
    await setLocal({ boundTab: {} });
    send({ type: 'host_state', bound_tab: {} });
    broadcastState();
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { boundTab } = await getLocal(['boundTab']);
  if (boundTab?.tabId === tabId) {
    await setLocal({ boundTab: {} });
    send({ type: 'host_state', bound_tab: {} });
  }
});

chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === RECONNECT_ALARM) connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 1 }));
connect();
