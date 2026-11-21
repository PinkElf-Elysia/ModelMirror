/* The host validates the sending window, origin and card-specific message type. */
document.getElementById('enterChat').addEventListener('click', () => {
  const text = document.getElementById('previewBox').textContent;
  if (text.length > 20000) {
    document.getElementById('configMessage').textContent = '开局超过 20000 字，请缩短后再进入聊天，或先复制保存。';
    return;
  }
  window.parent.postMessage({type: 'modern-life-opening', text}, new URL(location.href).origin);
});

const removeDialog = document.getElementById('removeNpcDialog');
for (const action of removeDialog.querySelectorAll('button')) {
  action.addEventListener('click', () => removeDialog.close(action.value));
}

function confirmNpcRemoval(message) {
  const dialog = removeDialog;
  document.getElementById('removeNpcLabel').textContent = message;
  dialog.returnValue = '';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'delete'), {once: true});
    dialog.showModal();
  });
}
