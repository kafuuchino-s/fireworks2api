function cacheQueryParams() {
  const params = new URLSearchParams();
  const limit = document.getElementById('limit').value.trim();
  const modelAlias = document.getElementById('model_alias').value.trim();
  const keyFingerprint = document.getElementById('key_fingerprint').value.trim();
  const errorType = document.getElementById('error_type').value.trim();
  const statusCode = document.getElementById('status_code').value.trim();
  if (limit) params.set('limit', limit);
  if (modelAlias) params.set('model_alias', modelAlias);
  if (keyFingerprint) params.set('key_fingerprint', keyFingerprint);
  if (errorType) params.set('error_type', errorType);
  if (statusCode) params.set('status_code', statusCode);
  return params;
}

function statCard(label, value, sub = '') {
  return `<div class="stat-cell"><div><div class="stat-label">${label}</div><div class="stat-num">${value}</div></div><div class="stat-sub">${sub}</div></div>`;
}

function fmtToken(n) { return fmtNumber(n || 0); }

function renderOverview(overview = {}) {
  const healthy = overview.healthy_key_count ?? overview.healthy_keys ?? 0;
  document.getElementById('stats').innerHTML = [
    statCard(t('cache.stats.requestCount.label'), fmtNumber(overview.request_count), t('cache.stats.requestCount.sub')),
    statCard(t('cache.stats.errorCount.label'), fmtNumber(overview.error_count), t('cache.stats.errorCount.sub')),
    statCard(t('cache.stats.inputTokens.label'), fmtNumber(overview.input_tokens), t('cache.stats.inputTokens.sub')),
    statCard(t('cache.stats.outputTokens.label'), fmtNumber(overview.output_tokens), t('cache.stats.outputTokens.sub')),
    statCard(t('cache.stats.cachedTokens.label'), fmtNumber(overview.cached_tokens), t('cache.stats.cachedTokens.sub')),
    statCard(t('cache.stats.hitRatio.label'), fmtPercent(overview.cache_hit_ratio), t('cache.stats.hitRatio.sub')),
    statCard(t('cache.stats.healthyKeys.label'), fmtNumber(healthy), t('cache.stats.healthyKeys.sub')),
  ].join('');
}

const fmtMaybeNumber = v => (v === null || v === undefined || v === '' ? '—' : fmtNumber(v));

function analysisTableBody(columns, rows, emptyText) {
  const body = rows.length
    ? `<div class="table-card analysis-table-card"><table class="analysis-table"><thead><tr>${columns.map(col => `<th>${escapeHtml(col)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
    : `<div class="empty">${escapeHtml(emptyText)}</div>`;
  return body;
}

function chip(value, cls = '') { return `<span class="badge ${cls}">${escapeHtml(value ?? '—')}</span>`; }

function analysisLinkish(value) { return `<span class="mono">${escapeHtml(value ?? '—')}</span>`; }

function cacheRouteStatus(value) {
  if (value === 'stable') return t('cache.analysis.status.stable');
  if (value === 'dispersed') return t('cache.analysis.status.dispersed');
  return value || '—';
}

function keyStatus(value) {
  if (value === 'active') return t('cache.keyStatus.active');
  if (value === 'cooldown') return t('cache.keyStatus.cooldown');
  if (value === 'disabled') return t('cache.keyStatus.disabled');
  if (value === 'unknown') return t('cache.keyStatus.unknown');
  return value || '—';
}

function analysisOptions(selected) {
  return ['model', 'key', 'sticky'].map(value => `<option value="${value}" ${selected === value ? 'selected' : ''}>${escapeHtml(t(`cache.analysis.option.${value}`))}</option>`).join('');
}

function analysisView(analysis, selected) {
  const models = Array.isArray(analysis.by_model_list) ? analysis.by_model_list : [];
  const keys = Array.isArray(analysis.by_key_list) ? analysis.by_key_list : [];
  const sticky = Array.isArray(analysis.sticky) ? analysis.sticky : [];
  if (selected === 'key') {
    return {
      title: t('cache.analysis.byKey'),
      columns: [t('cache.analysis.key'), t('cache.analysis.status'), t('cache.analysis.requestCount'), t('cache.analysis.cachedTokens'), t('cache.analysis.cacheHitRate')],
      rows: keys.map(row => [
        analysisLinkish(row.key_label || row.masked_key || row.key_name || '—'),
        chip(keyStatus(row.status), row.status === 'disabled' ? 'badge-red' : (row.status === 'cooldown' ? 'badge-warn' : 'badge-green')),
        chip(fmtMaybeNumber(row.request_count)),
        chip(fmtMaybeNumber(row.cached_tokens)),
        chip(fmtPercent(row.token_cache_hit_rate)),
      ]),
    };
  }
  if (selected === 'sticky') {
    return {
      title: t('cache.analysis.sticky'),
      columns: [t('cache.analysis.stableKey'), t('cache.analysis.modelAlias'), t('cache.analysis.keyCount'), t('cache.analysis.cacheHitRate'), t('cache.analysis.status')],
      rows: sticky.map(row => [
        analysisLinkish(row.stable_key_hash || '—'),
        analysisLinkish(row.model_alias || '—'),
        chip(fmtMaybeNumber(row.key_count)),
        chip(fmtPercent(row.token_cache_hit_rate)),
        chip(cacheRouteStatus(row.status), row.status === 'dispersed' ? 'badge-warn' : 'badge-green'),
      ]),
    };
  }
  return {
    title: t('cache.analysis.byModel'),
    columns: [t('cache.analysis.model'), t('cache.analysis.requestCount'), t('cache.analysis.cachedTokens'), t('cache.analysis.cacheHitRate'), t('cache.analysis.avgLatency')],
    rows: models.map(row => [
      analysisLinkish(row.model_alias || row.name || row.model || '—'),
      chip(fmtMaybeNumber(row.request_count)),
      chip(fmtMaybeNumber(row.cached_tokens)),
      chip(fmtPercent(row.token_cache_hit_rate)),
      chip(row.avg_latency_ms == null ? '—' : `${fmtNumber(row.avg_latency_ms)} ms`),
    ]),
  };
}

function renderAnalysis(analysis = null, selected = null) {
  const host = document.getElementById('analysis-panels');
  if (!host) return;
  if (!analysis) {
    host.innerHTML = `<section class="panel"><div class="panel-hd"><div><div class="panel-title">${t('cache.analysis.title')}</div><div class="panel-sub">${t('cache.analysis.unavailable')}</div></div></div></section>`;
    return;
  }
  window.__cacheAnalysis = analysis;
  const dimension = selected || host.dataset.dimension || 'model';
  host.dataset.dimension = dimension;
  const view = analysisView(analysis, dimension);
  host.innerHTML = `<section class="panel analysis-panel-wide">
    <div class="panel-hd">
      <div><div class="panel-title">${escapeHtml(t('cache.analysis.title'))}</div><div class="panel-sub">${escapeHtml(view.title)}</div></div>
      <label class="analysis-dimension"><span>${escapeHtml(t('cache.analysis.dimension'))}</span><select class="input input-small" id="analysis-dimension-select">${analysisOptions(dimension)}</select></label>
    </div>
    ${analysisTableBody(view.columns, view.rows, t('cache.analysis.empty'))}
  </section>`;
}

function statusBadge(status) {
  const code = Number(status);
  const cls = code >= 400 ? 'badge-red' : 'badge-green';
  return `<span class="badge ${cls}">${escapeHtml(status ?? '—')}</span>`;
}

function renderRows(rows = []) {
  const tbody = document.getElementById('request-rows');
  document.getElementById('rows-badge').textContent = t('cache.rowsBadge', { count: rows.length });
  if (!rows.length) { tbody.innerHTML = `<tr><td class="empty" colspan="10">${t('cache.noRows')}</td></tr>`; return; }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="time-cell">${escapeHtml(fmtDate(r.timestamp))}</td>
      <td class="mono">${escapeHtml(r.endpoint || '—')}</td>
      <td>${escapeHtml(r.model_alias || '—')}<div class="muted mono">${escapeHtml(r.upstream_model || '')}</div></td>
      <td class="mono">${escapeHtml(r.key_label || r.masked_key || r.key_name || '—')}</td>
      <td><div class="token-group"><span class="token-pill">${t('cache.tokens.input')} ${fmtToken(r.input_tokens)}</span><span class="token-pill">${t('cache.tokens.output')} ${fmtToken(r.output_tokens)}</span><span class="token-pill">${t('cache.tokens.cached')} ${fmtToken(r.cached_tokens)}</span></div></td>
      <td>${fmtPercent(r.cache_hit_ratio)}</td>
      <td>${escapeHtml(r.latency_ms ?? '—')} ms</td>
      <td>${statusBadge(r.status_code)}</td>
      <td class="error-text">${escapeHtml(r.error_type || '—')}</td>
      <td class="mono">${escapeHtml(r.upstream_request_id || '—')}</td>
    </tr>`).join('');
}

let cacheRefreshTimer = null;
let cacheLoadInFlight = false;

function getAutoRefreshToggle() {
  return document.getElementById('auto-refresh');
}

function stopCacheAutoRefresh() {
  if (cacheRefreshTimer) {
    clearInterval(cacheRefreshTimer);
    cacheRefreshTimer = null;
  }
}

function startCacheAutoRefresh() {
  stopCacheAutoRefresh();
  cacheRefreshTimer = setInterval(() => {
    if (cacheLoadInFlight) return;
    loadCacheData();
  }, 10000);
}

function syncCacheAutoRefresh() {
  const toggle = getAutoRefreshToggle();
  if (!toggle) return;
  if (toggle.checked) startCacheAutoRefresh();
  else stopCacheAutoRefresh();
}

async function loadCacheData() {
  if (cacheLoadInFlight) return;
  cacheLoadInFlight = true;
  const params = cacheQueryParams();
  const tableSub = document.getElementById('table-sub');
  tableSub.textContent = t('cache.loading');
  try {
    const [overview, requests, analysisResp] = await Promise.all([
      adminFetch('/admin/overview'),
      adminFetch(`/admin/requests?${params.toString()}`),
      adminFetch('/admin/cache/analysis').catch(err => err.status === 404 ? null : Promise.reject(err)),
    ]);
    renderOverview(overview || {});
    renderRows(Array.isArray(requests) ? requests : (requests?.items || requests?.data || []));
    renderAnalysis(analysisResp || null);
    tableSub.textContent = t('cache.loadedAt', { time: new Date().toLocaleString() });
  } catch (err) {
    showToast(err.message || t('cache.loadFailed'), 'error');
    tableSub.textContent = t('cache.loadFailed');
    renderRows([]);
    renderAnalysis(null);
  } finally {
    cacheLoadInFlight = false;
  }
}

function initCachePage() {
  applyI18n(document);
  document.getElementById('refresh-btn').addEventListener('click', loadCacheData);
  document.getElementById('refresh-logs-btn').addEventListener('click', loadCacheData);
  document.getElementById('apply-btn').addEventListener('click', loadCacheData);
  document.getElementById('auto-refresh').addEventListener('change', syncCacheAutoRefresh);
  document.getElementById('analysis-panels').addEventListener('change', e => {
    if (e.target?.id === 'analysis-dimension-select') renderAnalysis(window.__cacheAnalysis, e.target.value);
  });
  document.getElementById('reset-btn').addEventListener('click', () => {
    ['limit', 'model_alias', 'key_fingerprint', 'error_type', 'status_code'].forEach(id => document.getElementById(id).value = id === 'limit' ? 50 : '');
    loadCacheData();
  });
  loadCacheData();
}

window.initCachePage = initCachePage;
