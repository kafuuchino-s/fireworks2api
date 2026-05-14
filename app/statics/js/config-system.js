(async function () {
  await requireAdminToken();
  renderAdminHeader('/admin/config');
  applyI18n(document);

  const form = document.getElementById('configForm');
  const fields = {
    adminToken: document.getElementById('adminToken'),
    upstreamBaseUrl: document.getElementById('upstreamBaseUrl'),
    requestTimeoutSeconds: document.getElementById('requestTimeoutSeconds'),
    maxUpstreamAttempts: document.getElementById('maxUpstreamAttempts'),
    allowUnknownModelPassthrough: document.getElementById('allowUnknownModelPassthrough'),
    cooldownRateLimitSeconds: document.getElementById('cooldownRateLimitSeconds'),
    cooldown5xxSeconds: document.getElementById('cooldown5xxSeconds'),
    cooldownNetworkSeconds: document.getElementById('cooldownNetworkSeconds'),
    cooldownLongSeconds: document.getElementById('cooldownLongSeconds'),
    requestLogRetention: document.getElementById('requestLogRetention'),
    transformDebugEnabled: document.getElementById('transformDebugEnabled'),
    transformDebugRetention: document.getElementById('transformDebugRetention'),
    transformDebugLevel: document.getElementById('transformDebugLevel'),
    transformDebugLogsTbody: document.getElementById('transformDebugLogsTbody'),
    refreshTransformDebugLogsBtn: document.getElementById('refreshTransformDebugLogsBtn'),
    clearTransformDebugLogsBtn: document.getElementById('clearTransformDebugLogsBtn'),
    proxyKeysList: document.getElementById('proxyKeysList'),
    proxyKeyInput: document.getElementById('proxyKeyInput'),
    appendProxyKeyBtn: document.getElementById('appendProxyKeyBtn'),
    generateProxyKeyBtn: document.getElementById('generateProxyKeyBtn'),
  };

  const asText = v => (v === undefined || v === null ? '' : String(v));
  const parseList = v => String(v || '').split(/[\n,]/).map(s => s.trim()).filter(Boolean);
  const setCheckbox = (el, v) => { if (el) el.checked = !!v; };

  function fill(cfg = {}) {
    renderProxyKeys(cfg.proxy_api_keys || []);
    fields.upstreamBaseUrl.value = asText(cfg.upstream_base_url);
    fields.requestTimeoutSeconds.value = asText(cfg.request_timeout_seconds);
    fields.maxUpstreamAttempts.value = asText(cfg.max_upstream_attempts);
    fields.requestLogRetention.value = asText(cfg.request_log_retention);
    fields.transformDebugRetention.value = asText(cfg.transform_debug_retention);
    fields.transformDebugLevel.value = asText(cfg.transform_debug_level);
    fields.cooldownRateLimitSeconds.value = asText(cfg.cooldown_rate_limit_seconds);
    fields.cooldown5xxSeconds.value = asText(cfg.cooldown_5xx_seconds ?? cfg.cooldown_5xx_second ?? cfg.cooldown_5xx);
    fields.cooldownNetworkSeconds.value = asText(cfg.cooldown_network_seconds);
    fields.cooldownLongSeconds.value = asText(cfg.cooldown_long_seconds);
    setCheckbox(fields.allowUnknownModelPassthrough, cfg.allow_unknown_model_passthrough);
    setCheckbox(fields.transformDebugEnabled, cfg.transform_debug_enabled);
  }

  function compactJson(value) {
    if (value === undefined || value === null || value === '') return '—';
    if (Array.isArray(value) || typeof value === 'object') {
      try { return JSON.stringify(value); } catch { return String(value); }
    }
    return String(value);
  }

  function renderTransformDebugLogs(rows = []) {
    if (!fields.transformDebugLogsTbody) return;
    if (!Array.isArray(rows) || !rows.length) {
      fields.transformDebugLogsTbody.innerHTML = `<tr><td class="empty" colspan="8">${t('configSystem.logs.empty')}</td></tr>`;
      return;
    }
    fields.transformDebugLogsTbody.innerHTML = rows.map(row => `<tr>
      <td class="time-cell">${escapeHtml(asText(row.time || row.created_at || row.timestamp))}</td>
      <td>${escapeHtml(asText(row.endpoint))}</td>
      <td>${escapeHtml(asText(row.upstream_endpoint || row.upstream))}</td>
      <td>${escapeHtml(asText(row.model_alias || row.model))}</td>
      <td>${escapeHtml(asText(row.response_status_code || row.error_type || row.status || '—'))}</td>
      <td><span class="compact-json">${escapeHtml(compactJson(row.payload_fields || row.fields))}</span></td>
      <td><span class="compact-json">${escapeHtml(compactJson(row.field_changes || row.changes))}</span></td>
      <td><span class="compact-json">${escapeHtml(compactJson(row.warnings))}</span></td>
    </tr>`).join('');
  }

  function renderProxyKeys(keys = []) {
    if (!fields.proxyKeysList) return;
    if (!Array.isArray(keys) || !keys.length) {
      fields.proxyKeysList.innerHTML = `<div class="empty">${t('configSystem.proxy.empty')}</div>`;
      return;
    }
    fields.proxyKeysList.innerHTML = keys.map(key => `<div class="proxy-key-row">
      <button type="button" class="proxy-key-text mono" data-copy-key="${escapeHtml(String(key))}">${escapeHtml(String(key))}</button>
      <button type="button" class="btn btn-small btn-danger" data-delete-proxy-key="${escapeHtml(String(key))}">${t('common.delete')}</button>
    </div>`).join('');
  }

  function buildPayload() {
    const payload = {
      allow_unknown_model_passthrough: fields.allowUnknownModelPassthrough.checked,
      transform_debug_enabled: fields.transformDebugEnabled.checked,
    };
    const addText = (key, value) => { const v = value.trim(); if (v) payload[key] = v; };
    const addNum = (key, value) => { const v = value.trim(); if (v !== '') payload[key] = Number(v); };

    addText('admin_token', fields.adminToken.value);
    addText('upstream_base_url', fields.upstreamBaseUrl.value);
    addNum('request_timeout_seconds', fields.requestTimeoutSeconds.value);
    addNum('max_upstream_attempts', fields.maxUpstreamAttempts.value);
    addNum('cooldown_rate_limit_seconds', fields.cooldownRateLimitSeconds.value);
    addNum('cooldown_5xx_seconds', fields.cooldown5xxSeconds.value);
    addNum('cooldown_network_seconds', fields.cooldownNetworkSeconds.value);
    addNum('cooldown_long_seconds', fields.cooldownLongSeconds.value);
    addNum('request_log_retention', fields.requestLogRetention.value);
    addNum('transform_debug_retention', fields.transformDebugRetention.value);
    addText('transform_debug_level', fields.transformDebugLevel.value);
    return payload;
  }

  async function saveConfig() {
    try {
      const enteredAdminToken = fields.adminToken.value.trim();
      const payload = buildPayload();
      const res = await adminFetch('/admin/config/runtime', { method: 'PATCH', body: JSON.stringify(payload) });
      const newToken = res?.admin_token || res?.new_admin_token || res?.token || res?.updated_admin_token;
      if (enteredAdminToken) setAdminToken(newToken || enteredAdminToken);
      showToast(t('common.success'), 'success');
      fields.adminToken.value = '';
    } catch (e) {
      showToast(e.message || t('common.loadingFailed'), 'error');
    }
  }

  async function refreshConfig() {
    const res = await adminFetch('/admin/config/runtime').catch(() => ({}));
    fill(res?.config || res || {});
  }

  async function refreshTransformDebugLogs() {
    try {
      const res = await adminFetch('/admin/transform-debug?limit=50');
      const rows = Array.isArray(res) ? res : (res?.items || res?.logs || res?.data || []);
      renderTransformDebugLogs(rows);
    } catch (e) {
      renderTransformDebugLogs([]);
      showToast(e.message || t('common.loadingFailed'), 'error');
    }
  }

  async function clearTransformDebugLogs() {
    if (!confirm(t('configSystem.logs.clearConfirm'))) return;
    try {
      await adminFetch('/admin/transform-debug', { method: 'DELETE' });
      await refreshTransformDebugLogs();
      showToast(t('common.success'), 'success');
    } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
  }

  async function generateProxyKey() {
    try {
      await adminFetch('/admin/config/proxy-keys/generate', { method: 'POST' });
      await refreshConfig();
      showToast(t('common.success'), 'success');
    } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
  }

  async function deleteProxyKey(key) {
    try {
      await adminFetch(`/admin/config/proxy-keys/${encodeURIComponent(key)}`, { method: 'DELETE' });
      await refreshConfig();
      showToast(t('common.success'), 'success');
    } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
  }

  async function appendProxyKey() {
    const key = fields.proxyKeyInput.value.trim();
    if (!key) return;
    try {
      const res = await adminFetch('/admin/config/runtime').catch(() => ({}));
      const existing = Array.isArray(res?.config?.proxy_api_keys) ? res.config.proxy_api_keys : [];
      const payload = { proxy_api_keys: [...existing, key] };
      await adminFetch('/admin/config/runtime', { method: 'PATCH', body: JSON.stringify(payload) });
      fields.proxyKeyInput.value = '';
      await refreshConfig();
      showToast(t('common.success'), 'success');
    } catch (e) { showToast(e.message || t('common.loadingFailed'), 'error'); }
  }

  try {
    await refreshConfig();
    await refreshTransformDebugLogs();
  } catch (e) {
    showToast(e.message || t('common.loadingFailed'), 'error');
  }

  document.getElementById('saveConfigTopBtn').addEventListener('click', saveConfig);
  form.addEventListener('submit', e => { e.preventDefault(); saveConfig(); });
  fields.refreshTransformDebugLogsBtn?.addEventListener('click', refreshTransformDebugLogs);
  fields.clearTransformDebugLogsBtn?.addEventListener('click', clearTransformDebugLogs);
  fields.generateProxyKeyBtn.addEventListener('click', generateProxyKey);
  fields.appendProxyKeyBtn.addEventListener('click', appendProxyKey);
  fields.proxyKeysList.addEventListener('click', e => {
    const copyBtn = e.target.closest('[data-copy-key]');
    const delBtn = e.target.closest('[data-delete-proxy-key]');
    if (copyBtn) { navigator.clipboard?.writeText(copyBtn.dataset.copyKey || '').then(() => showToast(t('common.copied'), 'success')).catch(() => showToast(t('common.copyFailed'), 'error')); }
    if (delBtn) deleteProxyKey(delBtn.dataset.deleteProxyKey || '');
  });
})();
