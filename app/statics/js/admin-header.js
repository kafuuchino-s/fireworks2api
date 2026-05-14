function renderAdminHeader(active) {
  const links = [
    ['/admin/account', 'nav.accounts'],
    ['/admin/model', 'nav.models'],
    ['/admin/config', 'nav.config'],
    ['/admin/cache', 'nav.cache'],
    ['/admin/status', 'nav.status'],
  ];
  document.body.insertAdjacentHTML('afterbegin', `
    <header class="admin-header">
      <div class="admin-header-inner">
        <div class="admin-brand-wrap">
          <a class="admin-brand-link" href="/admin/account" aria-label="fireworks2api admin">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2 4 13h7l-1 9 10-13h-7l1-7h-2Z"/></svg>
            <span class="admin-brand">fireworks2api</span>
          </a>
          <span class="admin-username">Fireworks Admin</span>
        </div>
        <nav class="admin-nav">
          ${links.map(([href, label]) => `<a class="admin-nav-link ${active === href ? 'active' : ''}" href="${href}" data-i18n="${label}">${t(label)}</a>`).join('')}
        </nav>
        <div class="admin-header-right">
          <div class="admin-lang-menu" data-admin-lang-menu>
            <button class="admin-lang-trigger" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Language menu">
              <span class="admin-lang-trigger-label">${getLanguage() === 'en' ? 'EN' : 'CN'}</span>
              <span aria-hidden="true">▾</span>
            </button>
            <div class="admin-lang-popover" role="menu">
              <button class="admin-lang-option ${getLanguage() === 'zh' ? 'active' : ''}" type="button" role="menuitemradio" aria-checked="${getLanguage() === 'zh'}" onclick="setLanguage('zh')">CN</button>
              <button class="admin-lang-option ${getLanguage() === 'en' ? 'active' : ''}" type="button" role="menuitemradio" aria-checked="${getLanguage() === 'en'}" onclick="setLanguage('en')">EN</button>
            </div>
          </div>
          <button class="btn btn-ghost btn-small" onclick="adminLogout()" data-i18n="common.logout">${t('common.logout')}</button>
        </div>
      </div>
    </header>
  `);

  const menu = document.querySelector('[data-admin-lang-menu]');
  if (!menu || menu.dataset.bound === '1') return;
  menu.dataset.bound = '1';
  const trigger = menu.querySelector('.admin-lang-trigger');
  const closeMenu = () => { menu.classList.remove('open'); trigger.setAttribute('aria-expanded', 'false'); };
  const toggleMenu = () => { const open = !menu.classList.contains('open'); menu.classList.toggle('open', open); trigger.setAttribute('aria-expanded', String(open)); };
  trigger.addEventListener('click', e => { e.stopPropagation(); toggleMenu(); });
  document.addEventListener('click', e => { if (!menu.contains(e.target)) closeMenu(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
}

function fmtNumber(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString() : '0';
}

function fmtPercent(value) {
  const number = Number(value || 0) * 100;
  return `${number.toFixed(number >= 10 ? 0 : 1)}%`;
}

function fmtDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
}

function httpError(status, message, data = null) {
  const err = new Error(message || `HTTP ${status}`);
  err.status = status;
  err.data = data;
  return err;
}

window.renderAdminHeader = renderAdminHeader;
window.fmtNumber = fmtNumber;
window.fmtPercent = fmtPercent;
window.fmtDate = fmtDate;
window.escapeHtml = escapeHtml;
window.httpError = httpError;
