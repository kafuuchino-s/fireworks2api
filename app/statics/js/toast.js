function ensureToastContainer() {
  let node = document.querySelector('.toast-container');
  if (!node) {
    node = document.createElement('div');
    node.className = 'toast-container';
    document.body.appendChild(node);
  }
  return node;
}

function showToast(message, type = 'info', timeout = 2600) {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = String(message ?? '');
  ensureToastContainer().appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

window.showToast = showToast;
