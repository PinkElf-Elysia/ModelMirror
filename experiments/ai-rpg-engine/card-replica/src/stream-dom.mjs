// Only pass HTML produced by streamPresentation. Never pass provider text here.
// Patch matching nodes so expanding a disclosure or flipping a card survives
// subsequent chunks; unchanged images and focused controls are not recreated.
export function patchStreamHtml(root, html) {
  const template = root.ownerDocument.createElement('template');
  template.innerHTML = html;
  const preserve = (el, name) => (el.localName === 'details' && name === 'open') ||
    (el.localName === 'input' && name === 'checked');
  function patch(parent, incoming) {
    const wanted = Array.from(incoming.childNodes);
    for (let i = 0; i < wanted.length; i++) {
      const next = wanted[i], current = parent.childNodes[i];
      if (!current) { parent.appendChild(next.cloneNode(true)); continue; }
      if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) {
        current.replaceWith(next.cloneNode(true)); continue;
      }
      if (current.nodeType === 3) {
        if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      } else if (current.nodeType === 1) {
        for (const attr of Array.from(current.attributes)) {
          if (!next.hasAttribute(attr.name) && !preserve(current, attr.name)) current.removeAttribute(attr.name);
        }
        for (const attr of Array.from(next.attributes)) {
          if (!preserve(current, attr.name) && current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
        }
        patch(current, next);
      }
    }
    while (parent.childNodes.length > wanted.length) parent.lastChild.remove();
  }
  patch(root, template.content);
}
