let state = { overview: {}, keys: [], quotaSummaries: {}, editing: null };

const $ = id => document.getElementById(id);

function esc(v) { return String(v ?? '').replace(/[&<>'"]/g, s => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[s])); }
function pct(v) { const n = Number(v); return Number.isFinite(n) ? `${(n * 100).toFixed(n >= 0.1 ? 0 : 1)}%` : '—'; }
function num(v) { const n = Number(v); return Number.isFinite(n) ? n.toLocaleString() : '0'; }
function compactTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function actionLabel(act, key) {
  if (act === 'edit') return t('common.edit');
  if (act === 'toggle') return key.enabled ? t('common.disable') : t('common.enable');
  if (act === 'cooldown') return t('account.action.clearCooldown');
  if (act === 'delete') return t('common.delete');
  return '';
}

function keyNameFromButton(btn) {
  const explicit = btn?.dataset?.name || '';
  if (explicit) return explicit;
  return btn?.closest('tr')?.querySelector('td')?.textContent?.trim() || '';
}

function openModal(editing = null) {
  state.editing = editing;
  $('key-modal-title').textContent = editing ? t('account.modal.edit') : t('account.modal.add');
  $('key-name-label').classList.toggle('hidden', !editing);
  $('key-name').value = editing?.name || '';
  $('api-key').value = '';
  $('key-enabled').checked = editing ? !!editing.enabled : true;
  $('key-modal-backdrop').classList.add('open');
}
function closeModal() { $('key-modal-backdrop').classList.remove('open'); state.editing = null; }

function statusBadge(key) {
  if (!key.enabled) return `<span class="badge badge-red">${t('account.status.disabled')}</span>`;
  if (key.cooldown_active || key.in_cooldown) return `<span class="badge badge-warn">${t('account.status.cooldown')}</span>`;
  return `<span class="badge badge-green">${t('account.status.healthy')}</span>`;
}

function quotaStateClass(kind) {
  if (kind === 'danger') return 'quota-meter-danger';
  if (kind === 'warn') return 'quota-meter-warn';
  if (kind === 'ok') return 'quota-meter-ok';
  return 'quota-meter-unavailable';
}

function formatCurrency(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return `$${n.toFixed(2)}`;
}

function quotaSummaryText(q) {
  const bits = [];
  if (q.monthly_used != null || q.monthly_budget != null) {
    const used = formatCurrency(q.monthly_used);
    const budget = formatCurrency(q.monthly_budget);
    if (used || budget) bits.push(`${t('account.quota.usedShort') || t('account.quota.used')} ${used || '—'} / ${budget || '—'}`);
  }
  if (!bits.length && q.monthly_remaining != null) bits.push(`${t('account.quota.remaining')} ${formatCurrency(q.monthly_remaining) || q.monthly_remaining}`);
  if (!bits.length && (q.serverless_rpm_usage != null || q.serverless_rpm_limit != null)) bits.push(`${t('account.quota.rpm')} ${q.serverless_rpm_usage != null ? `${q.serverless_rpm_usage} / ` : ''}${q.serverless_rpm_limit ?? '—'}`);
  return bits.filter(Boolean).join(' · ');
}

function quotaStatus(q) {
  return String(q?.status || q?.quota_status || '').toLowerCase();
}

function quotaIsBlocking(q) {
  const status = quotaStatus(q);
  return ['quota_exhausted', 'billing_required', 'suspended', 'auth_error', 'disabled', 'unusable'].includes(status);
}

function quotaCell(key) {
  const q = state.quotaSummaries?.[key.name];
  if (!q) {
    return { html: `<div class="quota-meter quota-meter-unavailable"><div class="quota-meter-sub">${esc(t('account.quota.unavailable'))}</div></div>`, kind: 'unavailable', stale: false, refreshError: false };
  }
  const blocking = quotaIsBlocking(q);
  const available = !blocking && !(q.available === false || q.supported === false || q.status === 'unavailable');
  const stale = !!q.stale;
  const refreshError = q.refresh_status === 'error' || !!q.last_refresh_error_type;
  const used = Number(q.monthly_used);
  const limit = Number(q.monthly_budget);
  const hasPercent = Number.isFinite(used) && Number.isFinite(limit) && limit > 0;
  const remaining = q.monthly_remaining != null ? q.monthly_remaining : (hasPercent ? Math.max(limit - used, 0) : null);
  let kind = 'unavailable';
  let percent = null;
  if (available && !stale && !refreshError && hasPercent) {
    percent = Math.max(0, Math.min(100, (used / limit) * 100));
    kind = percent >= 90 ? 'danger' : percent >= 70 ? 'warn' : 'ok';
  } else if (available && !stale && !refreshError) {
    kind = 'ok';
  }
  const summary = quotaSummaryText(q) || t('account.quota.availableFallback');
  const meta = [];
  if (blocking) meta.push(t(`account.quota.status.${quotaStatus(q)}`) || t('account.sla.quotaBlocked'));
  if (stale) meta.push(t('account.quota.stale'));
  if (refreshError) meta.push(t('account.quota.refreshError'));
  if (q.last_refreshed_at) meta.push(compactTime(q.last_refreshed_at));
  const bar = hasPercent ? `<div class="quota-meter-track"><div class="quota-meter-fill" style="width:${percent == null ? 0 : percent.toFixed(0)}%"></div></div>` : '';
  const pctText = percent == null ? '' : `${t('account.quota.usedShort') || t('account.quota.used')} ${Math.round(percent)}%`;
  const remainingText = remaining != null ? `${t('account.quota.remaining')} ${formatCurrency(remaining) || remaining}` : '';
  const statusText = [pctText, remainingText].filter(Boolean).join(' · ');
  const classes = ['quota-meter', quotaStateClass(blocking ? 'danger' : (stale || refreshError ? 'unavailable' : kind))];
  if (!available) classes.push('quota-meter-unavailable');
  if (stale || refreshError) classes.push('quota-meter-stale');
  return {
    html: `<div class="${classes.join(' ')}">${bar}${statusText ? `<div class="quota-meter-top"><span class="quota-meter-label">${esc(statusText)}</span></div>` : ''}${summary && summary !== statusText ? `<div class="quota-meter-sub">${esc(summary)}</div>` : ''}${meta.length ? `<div class="quota-meter-note">${esc(meta.join(' · '))}</div>` : ''}</div>`,
    kind: blocking ? 'danger' : (stale || refreshError ? 'stale' : kind),
    stale,
    refreshError,
    blocking,
  };
}

function normalizeQuotaEntry(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const summary = raw.quota_summary && typeof raw.quota_summary === 'object'
    ? raw.quota_summary
    : (raw.summary && typeof raw.summary === 'object' ? raw.summary : raw);
  const itemCount = summary.count ?? raw.quota_items?.length ?? raw.items?.length;
  return {
    key_name: raw.key_name ?? raw.name ?? raw.keyName ?? '',
    available: raw.available ?? summary.available ?? (raw.quota_status === 'ok'),
    supported: raw.quota_supported ?? raw.supported ?? summary.supported,
    status: raw.status ?? summary.status ?? raw.quota_status ?? summary.quota_status,
    account_label: raw.account_label ?? summary.account_label,
    account_id: raw.account_id ?? summary.account_id,
    monthly_budget: raw.monthly_budget ?? summary.monthly_budget ?? summary.monthly_budget_usd ?? summary.budget_usd ?? summary.limit,
    monthly_used: raw.monthly_used ?? summary.monthly_used ?? summary.monthly_spend_usd ?? summary.used ?? summary.usage,
    monthly_remaining: raw.monthly_remaining ?? summary.monthly_remaining ?? summary.remaining,
    serverless_rpm_limit: raw.serverless_rpm_limit ?? summary.serverless_rpm_limit,
    serverless_rpm_usage: raw.serverless_rpm_usage ?? summary.serverless_rpm_usage,
    source: raw.source ?? summary.source,
    stale: !!(raw.stale ?? summary.stale),
    last_refreshed_at: raw.last_refreshed_at ?? summary.last_refreshed_at,
    stale_after: raw.stale_after ?? summary.stale_after,
    refresh_status: raw.refresh_status ?? summary.refresh_status,
    last_refresh_error_type: raw.last_refresh_error_type ?? summary.last_refresh_error_type,
    quota_status: raw.quota_status ?? summary.quota_status,
    items: itemCount,
  };
}

function extractQuotaMap(payload) {
  const items = Array.isArray(payload) ? payload : (payload?.items || payload?.keys || payload?.summaries || []);
  const map = {};
  for (const item of items) {
    const q = normalizeQuotaEntry(item);
    if (q?.key_name) map[q.key_name] = q;
  }
  return map;
}

function quotaText(key) {
  const q = state.quotaSummaries?.[key.name];
  if (!q) return { text: t('account.quota.unavailable'), kind: 'unavailable' };
  if (q.available === false || q.supported === false || q.status === 'unavailable') {
    return { text: t('account.quota.unavailable'), kind: 'unavailable' };
  }
  const main = [];
  const secondary = [];
  const freshness = [];
  if (q.status) main.push(t(`account.quota.status.${String(q.status).toLowerCase()}`) || String(q.status));
  if (q.monthly_budget != null || q.monthly_used != null) {
    const used = q.monthly_used ?? 0;
    const budget = q.monthly_budget ?? 0;
    main.push(`${t('account.quota.usedShort') || t('account.quota.used')} $${used} / $${budget}`);
  }
  if (q.monthly_remaining != null) secondary.push(`${t('account.quota.remaining')} $${q.monthly_remaining}`);
  if (q.serverless_rpm_limit != null) secondary.push(`RPM ${q.serverless_rpm_usage != null ? `${q.serverless_rpm_usage} / ` : ''}${q.serverless_rpm_limit}`);
  if (q.stale) freshness.push(t('account.quota.stale'));
  if (q.refresh_status === 'error') freshness.push(t('account.quota.refreshError'));
  const refreshedAt = compactTime(q.last_refreshed_at);
  if (refreshedAt) freshness.push(refreshedAt);
  if (!main.length && !secondary.length) {
    if (q.items != null) return { text: `${q.items} ${t('account.quota.items')}`, kind: 'available' };
    return { text: t('account.quota.availableFallback'), kind: 'available' };
  }
  return { text: [main.join(' · '), secondary.join(' · '), freshness.join(' · ')].filter(Boolean).join('\n'), kind: q.stale ? 'stale' : 'available' };
}

function render() {
  const o = state.overview || {};
  $('stat-total').textContent = num(o.key_total ?? o.total ?? state.keys.length);
  $('stat-healthy').textContent = num(o.healthy_key_count ?? o.healthy ?? state.keys.filter(k => k.enabled && !k.cooldown_active).length);
  $('stat-cooldown').textContent = num(o.cooldown_key_count ?? o.cooldown ?? state.keys.filter(k => k.cooldown_active).length);
  $('stat-disabled').textContent = num(o.disabled_key_count ?? o.disabled ?? state.keys.filter(k => !k.enabled).length);

  const tbody = $('keys-tbody');
  if (!state.keys.length) {
    $('empty-state').classList.remove('hidden');
    $('table-wrap').classList.add('hidden');
    tbody.innerHTML = '';
    return;
  }
  $('empty-state').classList.add('hidden');
  $('table-wrap').classList.remove('hidden');
  tbody.innerHTML = state.keys.map(key => {
    const quota = quotaCell(key);
    const q = state.quotaSummaries?.[key.name] || {};
    const accountId = (q.account_id || q.account_label || '—').replace(/^accounts\//, '');
    const masked = key.masked_key || key.masked || '—';
    const sla = !key.enabled ? t('account.sla.disabled') : (key.cooldown_active || key.in_cooldown ? t('account.sla.cooldown') : (quota.blocking ? t('account.sla.quotaBlocked') : (quota.stale ? t('account.sla.quotaStale') : (quota.refreshError ? t('account.sla.checkFailed') : (key.validation_error || key.invalid || key.validation_failed ? t('account.sla.invalid') : t('account.sla.healthy'))))));
    const slaClass = !key.enabled ? 'sla-pill-muted' : (key.cooldown_active || key.in_cooldown ? 'sla-pill-warn' : (quota.blocking ? 'sla-pill-red' : (quota.stale || quota.refreshError ? 'sla-pill-warn' : (key.validation_error || key.invalid || key.validation_failed ? 'sla-pill-red' : 'sla-pill-green'))));
    return `
    <tr>
      <td class="mono"><button class="copy-text" data-copy="${esc(masked)}">${esc(masked)}</button></td>
      <td>${esc(accountId || '—')}</td>
      <td>${statusBadge(key)}</td>
      <td>${quota.html}</td>
      <td><span class="sla-pill ${slaClass}"><span class="sla-dot"></span>${esc(sla)}</span></td>
      <td>${num(key.recent_request_count)}</td>
      <td>${pct(key.cache_hit_ratio)}</td>
      <td>
        <div class="row-actions">
          <button class="btn btn-small" data-act="edit" data-name="${esc(key.name)}">${actionLabel('edit', key)}</button>
          <button class="btn btn-small" data-act="toggle" data-name="${esc(key.name)}">${actionLabel('toggle', key)}</button>
          <button class="btn btn-small btn-danger" data-act="delete" data-name="${esc(key.name)}">${actionLabel('delete', key)}</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function syncOverviewFromKeys() {
  const total = state.keys.length;
  const healthy = state.keys.filter(k => k.enabled && !k.cooldown_active && !k.in_cooldown).length;
  const cooldown = state.keys.filter(k => k.cooldown_active || k.in_cooldown).length;
  const disabled = state.keys.filter(k => !k.enabled).length;
  state.overview = {
    ...(state.overview || {}),
    key_total: total,
    total,
    healthy_key_count: healthy,
    healthy,
    cooldown_key_count: cooldown,
    cooldown,
    disabled_key_count: disabled,
    disabled,
  };
}

function removeDeletedKeysFromView(items = []) {
  const deletedNames = new Set(
    items
      .filter(item => item?.status === 'deleted' && item.name)
      .map(item => item.name)
  );
  if (!deletedNames.size) return;
  state.keys = state.keys.filter(key => !deletedNames.has(key.name));
  for (const name of deletedNames) delete state.quotaSummaries?.[name];
  syncOverviewFromKeys();
  render();
}

async function loadData(options = {}) {
  const forceQuota = !!options.forceQuota;
  try {
    state.overview = await adminFetch('/admin/overview');
    const res = await adminFetch('/admin/keys');
    state.keys = Array.isArray(res) ? res : (res.items || res.keys || []);
    render();
    try {
      const quotaRes = await adminFetch(`/admin/fireworks/keys/quota-summaries?refresh=${forceQuota ? 'force' : 'auto'}`);
      state.quotaSummaries = extractQuotaMap(quotaRes);
    } catch (quotaErr) {
      state.quotaSummaries = {};
      if (quotaErr?.status !== 404) console.warn('quota summaries unavailable', quotaErr);
    }
    render();
    if (forceQuota) showToast(t('account.quota.refreshSuccess'), 'success');
  } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
}

async function cleanupInvalidKeys() {
  if (!confirm(t('account.cleanInvalidConfirm'))) return;
  try {
    const res = await adminFetch('/admin/keys/cleanup-invalid', { method: 'POST' });
    const deleted = Number(res?.deleted ?? 0);
    const checked = Number(res?.checked ?? 0);
    const kept = Number(res?.kept ?? 0);
    showToast(deleted > 0
      ? t('account.cleanInvalidDeleted', { deleted, checked, kept })
      : t('account.cleanInvalidNone', { checked, kept }), 'success');
    removeDeletedKeysFromView(res?.items || []);
  } catch (e) {
    showToast(e.message || t('account.cleanInvalidFailed'), 'error');
  }
}

async function submitKey(event) {
  event.preventDefault();
  const keyText = $('api-key').value.trim();
  try {
    if (state.editing) {
      const payload = { name: $('key-name').value.trim(), enabled: $('key-enabled').checked };
      if (keyText) payload.api_key = keyText;
      if (!payload.name) return showToast(t('account.saveHintEdit'), 'error');
      await adminFetch(`/admin/keys/${encodeURIComponent(state.editing.name)}`, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      if (!keyText) return showToast(t('account.saveHint'), 'error');
      const payload = { api_keys: keyText.split(/\r?\n/).map(v => v.trim()).filter(Boolean), enabled: $('key-enabled').checked, validate_with_fireworks: true };
      await adminFetch('/admin/keys/bulk', { method: 'POST', body: JSON.stringify(payload) });
    }
    showToast(t('common.success'), 'success'); closeModal(); await loadData();
  } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
}

async function doAction(act, name) {
  try {
    if (act === 'edit') {
      const key = state.keys.find(k => k.name === name); if (key) openModal(key); return;
    }
    if (act === 'toggle') await adminFetch(`/admin/keys/${encodeURIComponent(name)}/${state.keys.find(k => k.name === name)?.enabled ? 'disable' : 'enable'}`, { method: 'POST' });
    if (act === 'cooldown') await adminFetch(`/admin/keys/${encodeURIComponent(name)}/clear-cooldown`, { method: 'POST' });
    if (act === 'delete') { if (!name) return showToast(t('common.loadingFailed'), 'error'); if (!confirm(t('account.deleteConfirm', { name }))) return; await adminFetch(`/admin/keys/${encodeURIComponent(name)}`, { method: 'DELETE' }); }
    showToast(t('common.success'), 'success'); await loadData();
  } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
}

(async () => {
  await requireAdminToken();
  renderAdminHeader('/admin/account');
  applyI18n(document);
  $('btn-add').addEventListener('click', () => openModal());
  $('btn-refresh').addEventListener('click', () => loadData({ forceQuota: true }));
  $('btn-clean-invalid').addEventListener('click', cleanupInvalidKeys);
  $('key-modal-close').addEventListener('click', closeModal);
  $('key-cancel').addEventListener('click', closeModal);
  $('key-form').addEventListener('submit', submitKey);
  $('key-modal-backdrop').addEventListener('click', e => { if (e.target.id === 'key-modal-backdrop') closeModal(); });
  $('keys-tbody').addEventListener('click', e => {
    const copyBtn = e.target.closest('button[data-copy]');
    if (copyBtn) { navigator.clipboard?.writeText(copyBtn.dataset.copy || '').catch(() => {}); return; }
    const btn = e.target.closest('button[data-act]'); if (btn) doAction(btn.dataset.act, keyNameFromButton(btn));
  });
  await loadData();
})();
