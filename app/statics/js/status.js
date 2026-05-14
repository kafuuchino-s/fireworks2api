function safeText(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  return escapeHtml(value);
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function fmtCompactNumber(value) {
  const number = numberValue(value, 0);
  return new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    notation: number >= 100000 ? 'compact' : 'standard',
    maximumFractionDigits: number >= 100000 ? 1 : 0,
  }).format(number);
}

function fmtCurrency(value, currency = 'USD') {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const abs = Math.abs(number);
  const digits = abs > 0 && abs < 0.01 ? 4 : 2;
  return new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    style: 'currency',
    currency: currency || 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(number);
}

function fmtRate(value, currency = 'USD') {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const money = new Intl.NumberFormat(getLanguage() === 'en' ? 'en-US' : 'zh-CN', {
    style: 'currency',
    currency: currency || 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(number * 1000);
  return t('status.value.per1kTokens', { value: money });
}

function fmtBool(value, mode = 'yesNo') {
  if (typeof value === 'string' && ['true', 'false'].includes(value.toLowerCase())) value = value.toLowerCase() === 'true';
  if (typeof value !== 'boolean') return value === null || value === undefined || value === '' ? '—' : String(value);
  if (mode === 'supported') return value ? t('common.supported') : t('common.notSupported');
  if (mode === 'enabled') return value ? t('common.enabled') : t('common.disabled');
  if (mode === 'configured') return value ? t('status.value.configured') : t('status.value.notConfigured');
  if (mode === 'protected') return value ? t('status.value.protected') : t('status.value.attention');
  return value ? t('common.yes') : t('common.no');
}

function serviceStatusLabel(value) {
  const status = String(value || 'unknown').toLowerCase();
  const key = `status.value.service.${status}`;
  const translated = t(key);
  return translated === key ? (value || t('common.unknown')) : translated;
}

function serviceStatusClass(value) {
  const status = String(value || 'unknown').toLowerCase();
  if (['healthy', 'ok', 'ready'].includes(status)) return 'badge-green';
  if (['no_keys', 'not_configured', 'cooldown_only', 'disabled', 'degraded', 'warn', 'warning'].includes(status)) return 'badge-warn';
  if (['fail', 'failed', 'error', 'unhealthy'].includes(status)) return 'badge-red';
  return '';
}

function statusBadge(value, label = null) {
  return `<span class="badge ${serviceStatusClass(value)}">${escapeHtml(label || serviceStatusLabel(value))}</span>`;
}

function boolBadge(value, mode = 'yesNo') {
  const positive = value === true;
  const cls = positive ? 'badge-green' : (value === false ? 'badge-warn' : '');
  return `<span class="badge ${cls}">${escapeHtml(fmtBool(value, mode))}</span>`;
}

function textBadge(value, cls = '') {
  return `<span class="badge ${cls}">${safeText(value)}</span>`;
}

function getKeyCounts(overview = {}, posture = {}) {
  overview = overview || {};
  posture = posture || {};
  const counts = overview.key_counts || overview.keys || {};
  const total = counts.total ?? counts.all ?? overview.key_total ?? overview.key_count ?? posture.key_count ?? 0;
  const healthy = counts.healthy ?? overview.healthy_key_count ?? overview.healthy_keys ?? 0;
  const cooldown = counts.cooldown ?? overview.cooldown_key_count ?? overview.cooldown_keys ?? 0;
  const disabled = counts.disabled ?? overview.disabled_key_count ?? overview.disabled_keys ?? 0;
  return { total, healthy, cooldown, disabled };
}

function getRequests(overview = {}) {
  overview = overview || {};
  const requests = overview.recent_request_count ?? overview.request_count ?? overview.requests_count ?? overview.requests ?? 0;
  const errors = overview.recent_error_count ?? overview.error_count ?? overview.errors ?? 0;
  return { requests, errors };
}

function getCostTotals(cost = {}) {
  cost = cost || {};
  const totals = cost.totals || cost.summary || cost;
  return {
    requestCount: totals.request_count ?? cost.request_count ?? 0,
    inputTokens: totals.input_tokens ?? totals.input ?? cost.input_tokens ?? 0,
    outputTokens: totals.output_tokens ?? totals.output ?? cost.output_tokens ?? 0,
    cachedTokens: totals.cached_tokens ?? totals.cached ?? cost.cached_tokens ?? 0,
    estimatedCost: totals.estimated_cost?.total ?? totals.estimated_cost?.amount ?? totals.estimated_cost ?? cost.estimated_cost,
    estimatedSavings: totals.estimated_savings ?? totals.estimated_savings_amount ?? cost.estimated_savings,
  };
}

function getCostCurrency(cost = {}) {
  cost = cost || {};
  return cost.rates?.currency || cost.currency || 'USD';
}

function getAccountItems(accounts = null) {
  if (Array.isArray(accounts)) return accounts;
  if (Array.isArray(accounts?.items)) return accounts.items;
  if (Array.isArray(accounts?.data)) return accounts.data;
  return [];
}

function getFireworksStatus(accounts = null) {
  if (accounts === null || accounts === undefined) return 'unknown';
  if (accounts?.status) return accounts.status;
  if (accounts?.status_code) return Number(accounts.status_code) >= 200 && Number(accounts.status_code) < 300 ? 'healthy' : 'degraded';
  if (accounts?.supported === false) return 'not_configured';
  if (getAccountItems(accounts).length) return 'healthy';
  return 'unknown';
}

function fireworksStatusLabel(accounts = null) {
  const status = getFireworksStatus(accounts);
  if (status === 'not_configured') return t('status.value.notConfigured');
  if (status === 'healthy') return t('status.value.connected');
  if (status === 'degraded') return t('status.value.degraded');
  return serviceStatusLabel(status);
}

function redactSource(source) {
  if (!source) return '—';
  const value = String(source);
  if (value.startsWith('stored:')) return t('status.value.storedKey', { name: value.slice(7) || '—' });
  if (value === 'env:fireworks_api_keys') return t('status.value.envKeys');
  if (value === 'env:fireworks_api_keys_json') return t('status.value.envJsonKeys');
  if (value === 'not_configured') return t('status.value.notConfigured');
  return value.replace(/fw_[A-Za-z0-9_-]+/g, 'fw_••••');
}

function reasonLabel(value) {
  if (!value) return '—';
  const key = `status.value.reason.${value}`;
  const translated = t(key);
  return translated === key ? value : translated;
}

function summaryCard({ tone = '', eyebrow, value, title, sub, foot }) {
  return `<article class="status-card ${tone ? `status-card-${tone}` : ''}">
    <div class="status-card-top">
      <span class="status-card-eyebrow">${escapeHtml(eyebrow)}</span>
      ${foot ? `<span class="status-card-foot">${foot}</span>` : ''}
    </div>
    <div class="status-card-value">${value}</div>
    <div class="status-card-title">${escapeHtml(title)}</div>
    <div class="status-card-sub">${escapeHtml(sub)}</div>
  </article>`;
}

function panel(title, sub, body, extraClass = '') {
  return `<section class="panel status-detail-panel ${extraClass}"><div class="panel-hd"><div><div class="panel-title">${escapeHtml(title)}</div><div class="panel-sub">${escapeHtml(sub)}</div></div></div>${body}</section>`;
}

function detailList(items) {
  return `<div class="status-detail-list">${items.map(item => {
    const value = item.html ?? textBadge(item.value);
    return `<div class="status-detail-item">
      <div class="status-detail-copy">
        <span class="status-detail-label">${escapeHtml(item.label)}</span>
        ${item.hint ? `<span class="status-detail-hint">${escapeHtml(item.hint)}</span>` : ''}
      </div>
      <div class="status-detail-value">${value}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderSummaryCards({ overview = {}, posture = {}, cost = {}, accounts = null }) {
  overview = overview || {};
  posture = posture || {};
  cost = cost || {};
  const keys = getKeyCounts(overview, posture);
  const requestStats = getRequests(overview);
  const costTotals = getCostTotals(cost);
  const currency = getCostCurrency(cost);
  const status = overview.service_status || 'unknown';
  const cacheHitRatio = overview.cache_hit_ratio ?? (numberValue(costTotals.inputTokens) ? numberValue(costTotals.cachedTokens) / numberValue(costTotals.inputTokens) : 0);
  const errorRate = numberValue(requestStats.requests) ? numberValue(requestStats.errors) / numberValue(requestStats.requests) : 0;

  return [
    summaryCard({
      tone: serviceStatusClass(status).replace('badge-', '') || 'neutral',
      eyebrow: t('status.card.service.eyebrow'),
      value: statusBadge(status),
      title: serviceStatusLabel(status),
      sub: t('status.card.service.sub', { healthy: fmtNumber(keys.healthy), total: fmtNumber(keys.total) }),
      foot: `<span class="status-dot ${serviceStatusClass(status).replace('badge-', '')}"></span>`,
    }),
    summaryCard({
      tone: numberValue(keys.healthy) > 0 ? 'green' : (numberValue(keys.total) > 0 ? 'warn' : 'neutral'),
      eyebrow: t('status.card.keys.eyebrow'),
      value: fmtNumber(keys.healthy),
      title: t('status.card.keys.title'),
      sub: t('status.card.keys.sub', { total: fmtNumber(keys.total), cooldown: fmtNumber(keys.cooldown), disabled: fmtNumber(keys.disabled) }),
    }),
    summaryCard({
      tone: numberValue(requestStats.errors) > 0 ? 'warn' : 'green',
      eyebrow: t('status.card.requests.eyebrow'),
      value: fmtCompactNumber(requestStats.requests),
      title: t('status.card.requests.title'),
      sub: t('status.card.requests.sub', { errors: fmtNumber(requestStats.errors), rate: fmtPercent(errorRate) }),
    }),
    summaryCard({
      tone: numberValue(costTotals.cachedTokens) > 0 ? 'green' : 'neutral',
      eyebrow: t('status.card.cost.eyebrow'),
      value: costTotals.estimatedCost !== undefined && costTotals.estimatedCost !== null
        ? fmtCurrency(costTotals.estimatedCost, currency)
        : fmtCompactNumber(costTotals.cachedTokens),
      title: costTotals.estimatedCost !== undefined && costTotals.estimatedCost !== null ? t('status.card.cost.title') : t('status.card.cached.title'),
      sub: t('status.card.cost.sub', { cached: fmtNumber(costTotals.cachedTokens), ratio: fmtPercent(cacheHitRatio) }),
    }),
  ].join('');
}

function renderServiceHealth(overview = {}, posture = {}) {
  overview = overview || {};
  posture = posture || {};
  const keys = getKeyCounts(overview, posture);
  const requestStats = getRequests(overview);
  const errorRate = numberValue(requestStats.requests) ? numberValue(requestStats.errors) / numberValue(requestStats.requests) : 0;
  return panel(
    t('status.service.title'),
    t('status.service.sub'),
    detailList([
      { label: t('status.field.serviceStatus'), hint: t('status.hint.serviceStatus'), html: statusBadge(overview.service_status) },
      { label: t('status.field.healthyKeys'), hint: t('status.hint.healthyKeys'), value: t('status.value.keysHealthy', { healthy: fmtNumber(keys.healthy), total: fmtNumber(keys.total) }) },
      { label: t('status.field.cooldownDisabledKeys'), value: t('status.value.cooldownDisabled', { cooldown: fmtNumber(keys.cooldown), disabled: fmtNumber(keys.disabled) }) },
      { label: t('status.field.requestCount'), hint: t('status.hint.requestCount'), value: fmtNumber(requestStats.requests) },
      { label: t('status.field.errorCount'), value: t('status.value.errorsWithRate', { errors: fmtNumber(requestStats.errors), rate: fmtPercent(errorRate) }) },
      { label: t('status.field.avgLatency'), value: overview.avg_latency_ms == null ? '—' : t('status.value.ms', { value: fmtNumber(overview.avg_latency_ms) }) },
    ])
  );
}

function renderCost(cost = {}, overview = {}) {
  cost = cost || {};
  overview = overview || {};
  const totals = getCostTotals(cost);
  const currency = getCostCurrency(cost);
  const rates = cost.rates || {};
  const cacheHitRatio = overview.cache_hit_ratio ?? (numberValue(totals.inputTokens) ? numberValue(totals.cachedTokens) / numberValue(totals.inputTokens) : 0);
  const rows = [
    { label: t('status.field.estimatedCost'), hint: t('status.hint.estimatedCost'), html: `<span class="badge badge-dark">${escapeHtml(fmtCurrency(totals.estimatedCost, currency))}</span>` },
    { label: t('status.field.requestCount'), value: fmtNumber(totals.requestCount || overview.request_count || 0) },
    { label: t('status.field.inputTokens'), value: fmtNumber(totals.inputTokens) },
    { label: t('status.field.outputTokens'), value: fmtNumber(totals.outputTokens) },
    { label: t('status.field.cachedTokens'), hint: t('status.hint.cachedTokens'), value: t('status.value.cachedWithRatio', { tokens: fmtNumber(totals.cachedTokens), ratio: fmtPercent(cacheHitRatio) }) },
  ];
  if (totals.estimatedSavings !== undefined && totals.estimatedSavings !== null) {
    rows.push({ label: t('status.field.estimatedSavings'), value: fmtCurrency(totals.estimatedSavings, currency) });
  }
  return panel(t('status.cost.title'), t('status.cost.sub'), detailList(rows));
}

function renderFireworks(accounts = null) {
  const list = getAccountItems(accounts);
  const supported = accounts?.management_api_supported ?? accounts?.supported;
  const source = accounts?.source ?? accounts?.management_api_source;
  const statusCode = accounts?.status_code;
  const accountNames = list.slice(0, 3).map(item => item.displayName || item.display_name || item.name || item.id).filter(Boolean);
  const accountSummary = accountNames.length
    ? `${accountNames.join(', ')}${list.length > accountNames.length ? ` +${list.length - accountNames.length}` : ''}`
    : '—';
  return panel(
    t('status.fireworks.title'),
    t('status.fireworks.sub'),
    detailList([
      { label: t('status.field.accountsStatus'), hint: t('status.hint.accountsStatus'), html: statusBadge(getFireworksStatus(accounts), fireworksStatusLabel(accounts)) },
      { label: t('status.field.managementApiSupported'), html: boolBadge(supported, 'supported') },
    ])
  );
}

function renderStatusError(results) {
  const labels = [t('status.apiSource.overview'), t('status.apiSource.cost'), t('status.apiSource.accounts')];
  const failed = results
    .map((result, index) => ({ result, label: labels[index] || t('common.unknown') }))
    .filter(item => item.result.status === 'rejected');
  if (!failed.length) return '';
  return panel(
    t('status.loadIssues.title'),
    t('status.loadIssues.sub'),
    detailList(failed.map(item => ({
      label: item.label,
      value: item.result.reason?.message || t('common.loadingFailed'),
    }))),
    'status-panel-wide'
  );
}

let statusLoadInFlight = false;

async function loadStatusPage() {
  if (statusLoadInFlight) return;
  statusLoadInFlight = true;
  const cards = document.getElementById('status-cards');
  const panels = document.getElementById('status-panels');
  const updated = document.getElementById('status-last-updated');
  cards.innerHTML = `<div class="status-loading-card">${escapeHtml(t('common.loading'))}</div>`;
  panels.innerHTML = '';
  if (updated) updated.textContent = t('common.loading');

  try {
    const results = await Promise.allSettled([
      adminFetch('/admin/overview'),
      adminFetch('/admin/usage/cost-estimate'),
      adminFetch('/admin/fireworks/accounts'),
    ]);
    const [overviewRes, costRes, accountsRes] = results;
    const overview = overviewRes.status === 'fulfilled' ? overviewRes.value : {};
    const cost = costRes.status === 'fulfilled' ? (costRes.value || {}) : {};
    const accounts = accountsRes.status === 'fulfilled' ? accountsRes.value : null;

    cards.innerHTML = renderSummaryCards({ overview, posture: {}, cost, accounts });
    panels.innerHTML = [
      renderStatusError(results),
      renderServiceHealth(overview, {}),
      renderCost(cost, overview),
      renderFireworks(accounts),
    ].filter(Boolean).join('');

    if (updated) updated.textContent = t('status.loadedAt', { time: new Date().toLocaleString() });
  } finally {
    statusLoadInFlight = false;
  }
}

function initStatusPage() {
  applyI18n(document);
  document.getElementById('refresh-btn').addEventListener('click', () => loadStatusPage().catch(err => showToast(err.message || t('common.loadingFailed'), 'error')));
  loadStatusPage().catch(err => showToast(err.message || t('common.loadingFailed'), 'error'));
}

window.initStatusPage = initStatusPage;
