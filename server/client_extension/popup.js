const connection = document.querySelector('#connection');
const hostId = document.querySelector('#host-id');
const tabState = document.querySelector('#tab-state');
const pairing = document.querySelector('#pairing');
const code = document.querySelector('#pairing-code');
const message = document.querySelector('#message');

async function send(type, payload = {}) {
  return chrome.runtime.sendMessage({ type, ...payload });
}

async function refresh() {
  const state = await send('popup_state');
  connection.textContent = state.connected ? '已连接' : (state.hostId ? '等待重连' : '未连接');
  hostId.textContent = state.hostId || '未配对';
  pairing.hidden = Boolean(state.hostId);
  tabState.textContent = state.boundTab?.bound
    ? `${state.boundTab.title || '已绑定'} · ${state.boundTab.origin}`
    : '未绑定';
  document.querySelector('#bind').disabled = !state.hostId;
  document.querySelector('#unbind').disabled = !state.boundTab?.bound;
}

document.querySelector('#pair').addEventListener('click', async () => {
  message.textContent = '';
  const value = code.value.trim();
  if (!/^\d{8}$/.test(value)) {
    message.textContent = '请输入 8 位配对码。';
    return;
  }
  const result = await send('pair', { pairingCode: value });
  if (!result?.ok) message.textContent = result?.error || '配对失败。';
  await refresh();
});

document.querySelector('#bind').addEventListener('click', async () => {
  message.textContent = '';
  const result = await send('bind_active_tab');
  if (!result?.ok) message.textContent = result?.error || '绑定失败。';
  await refresh();
});

document.querySelector('#unbind').addEventListener('click', async () => {
  await send('unbind_tab');
  await refresh();
});

chrome.runtime.onMessage.addListener((event) => {
  if (event?.type === 'state_changed') refresh();
});

refresh();
