(async function () {
  await requireAdminToken();
  renderAdminHeader('/admin/model');
  applyI18n(document);

  const tbody = document.getElementById('modelsTbody');
  const importPanel = document.getElementById('importPanel');
  const modal = document.getElementById('modelModal');
  const form = document.getElementById('modelForm');
  const title = document.getElementById('modelModalTitle');
  const fields = ['modelId','alias','upstream_model','enabled'].reduce((m,id)=>(m[id]=document.getElementById(id),m),{});

  let models = [];
  let catalog = [];
  let editingAlias = null;

  const boolBadge = v => `<span class="badge badge-capability ${v ? 'badge-capability-true' : 'badge-capability-false'}">${v ? t('common.yes') : t('common.no')}</span>`;
  const capabilityBadge = (label, value, options = {}) => {
    const cls = value === true ? 'badge-capability-true' : (value === false ? 'badge-capability-false' : 'badge-capability-unknown');
    const text = value === true ? t('common.supported') : (value === false ? t('common.notSupported') : t('common.unknown'));
    return `<span class="badge badge-capability ${cls}" title="${escapeHtml(options.title || '')}">${escapeHtml(label)}: ${escapeHtml(options.format ? options.format(value) : text)}</span>`;
  };
  const formatContextTokens = n => {
    if (n == null) return t('common.unknown');
    const value = Number(n);
    if (!Number.isFinite(value)) return t('common.unknown');
    if (value >= 1000) {
      const k = value / 1000;
      return `${Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1).replace(/\.0$/, '')}k`;
    }
    return String(value);
  };
  const unknownValue = () => t('common.unknown');

  const copyTextCell = value => `<button class="copy-text mono" type="button" data-copy="${escapeHtml(value || '')}" title="${escapeHtml(t('common.copy'))}">${escapeHtml(value || '')}</button>`;

  function normalizeList(data) { return Array.isArray(data) ? data : (data?.items || data?.data || data?.models || []); }
  function existingAliasSet() { return new Set(models.map(m => String(m.alias || ''))); }
  function modelMetadata(item) { return { kind: item.kind || item.model_type || item.type || item.model_kind || '', pricing: item.pricing || item.price || item.cost || item.prices || null }; }
  function enrichModel(model) { const catalogItem = catalog.find(item => String(item.upstream_model || '') === String(model.upstream_model || '')); return catalogItem ? { ...catalogItem, ...model } : model; }

  function normalizeModelType(kind) {
    const value = String(kind || '').trim().toLowerCase();
    if (value === 'text' || value === 'image') return value;
    return 'unknown';
  }

  function pricingDetails(pricing) {
    if (!pricing) return null;
    if (typeof pricing === 'string') return pricing.trim() ? pricing.trim() : null;
    if (typeof pricing === 'number') return Number.isFinite(pricing) ? String(pricing) : null;
    if (typeof pricing === 'object') {
      const tierKeys = ['standard', 'priority', 'fast'];
      const tierParts = tierKeys
        .filter(key => pricing[key] && typeof pricing[key] === 'object')
        .map(key => pricingDetails({ tier: key, unit: pricing[key].unit || pricing.unit || 'usd_per_1m_tokens', ...pricing[key] }))
        .filter(Boolean);
      if (tierParts.length) return tierParts.join(' · ');
      const unit = String(pricing.unit || pricing.units || pricing.measure || '').toLowerCase();
      if (unit === 'usd_per_1m_tokens') {
        const lang = String(document.documentElement.lang || '').toLowerCase();
        const isZh = lang.startsWith('zh');
        const tierRaw = String(pricing.tier || pricing.plan || pricing.level || '').trim().toLowerCase();
        const tierLabel = tierRaw ? (tierRaw === 'standard' ? t('model.pricing.standard') : tierRaw === 'fast' ? t('model.pricing.fast') : tierRaw === 'priority' ? t('model.pricing.priority') : tierRaw.charAt(0).toUpperCase() + tierRaw.slice(1)) : '';
        const input = pricing.input ?? pricing.prompt ?? pricing.input_price ?? pricing.prompt_price;
        const cache = pricing.cache ?? pricing.cached ?? pricing.cache_price ?? pricing.prompt_cache ?? pricing.cached_input;
        const output = pricing.output ?? pricing.completion ?? pricing.output_price;
        const money = v => Number.isFinite(Number(v)) ? `$${Number(v).toFixed(2)}` : String(v);
        const parts = [];
        if (tierLabel) parts.push(tierLabel);
        if (input != null) parts.push(isZh ? `${t('model.pricing.input')} ${money(input)}` : `${t('model.pricing.input')} ${money(input)}`);
        if (cache != null) parts.push(isZh ? `${t('model.pricing.cache')} ${money(cache)}` : `${t('model.pricing.cache')} ${money(cache)}`);
        if (output != null) parts.push(isZh ? `${t('model.pricing.output')} ${money(output)}` : `${t('model.pricing.output')} ${money(output)}`);
        if (parts.length) return isZh ? `${parts.join(' · ')} ${t('model.pricing.per1m')}` : `${parts.join(' / ')} per 1M`;
      }
      const parts = [];
      for (const [k, v] of Object.entries(pricing)) {
        if (v == null || v === '' || k === 'unit' || k === 'units' || k === 'measure') continue;
        parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
      }
      return parts.length ? parts.join(' · ') : null;
    }
    return null;
  }

  function pricingLines(pricing) {
    if (!pricing) return [];
    if (typeof pricing === 'object' && !Array.isArray(pricing)) {
      const tierKeys = ['standard', 'priority', 'fast'];
      const tierParts = tierKeys
        .filter(key => pricing[key] && typeof pricing[key] === 'object')
        .map(key => pricingDetails({ tier: key, unit: pricing[key].unit || pricing.unit || 'usd_per_1m_tokens', ...pricing[key] }))
        .filter(Boolean);
      if (tierParts.length) return tierParts;
    }
    const text = pricingDetails(pricing);
    return text ? [text] : [];
  }

  function pricingHtml(pricing, className = 'pricing-line') {
    const lines = pricingLines(pricing);
    return lines.map(line => `<span class="${className}">${escapeHtml(line)}</span>`).join('');
  }

  function openModal(model = null, options = {}) {
    const isEdit = !!model && options.mode !== 'add';
    title.textContent = isEdit ? t('config.modal.edit') : t('config.modal.add');
    form.reset();
    fields.modelId.value = isEdit ? (model?.id ?? model?.alias ?? '') : '';
    fields.alias.value = model?.alias ?? '';
    fields.upstream_model.value = model?.upstream_model ?? '';
    fields.enabled.checked = model?.enabled ?? true;
    modal.classList.add('open');
  }
  function closeModal(){ modal.classList.remove('open'); }

  function modelKey(model) { return String(model.alias ?? ''); }

  function pricingLabel(pricing) {
    const text = pricingDetails(pricing);
    return text || '—';
  }

  function modelPricingNode(item) {
    const pricingNode = pricingHtml(modelMetadata(item).pricing, 'pricing-line');
    return pricingNode
      ? `<div class="model-meta-row model-price-row"><span class="model-meta-kicker">${escapeHtml(t('model.pricing'))}</span><span class="model-meta-value model-pricing-lines">${pricingNode}</span></div>`
      : `<div class="model-meta-row model-price-row"><span class="model-meta-kicker">${escapeHtml(t('model.pricing'))}</span><span class="model-meta-value model-meta-muted">${escapeHtml(t('model.priceUnknown'))}</span></div>`;
  }

  function modelCapabilityNode(item) {
    const caps = functionalityBadges(item) || `<span class="model-meta-muted">${escapeHtml(t('common.unknown'))}</span>`;
    return `<div class="model-meta-row model-cap-row"><span class="model-meta-kicker">${escapeHtml(t('model.capabilities'))}</span><span class="model-meta-value">${caps}</span></div>`;
  }

  function modelMetadataBlock(model) {
    return `<div class="model-meta-stack">${modelPricingNode(model)}${modelCapabilityNode(model)}</div>`;
  }

  function modelIdentityCell(model, isEditing) {
    const aliasValue = escapeHtml(model.alias || '');
    const upstreamValue = escapeHtml(model.upstream_model || '');
    if (isEditing) {
      return `<div class="model-identity"><div class="model-alias-line"><input class="input input-small" data-inline-field="alias" value="${aliasValue}" required></div><div class="model-upstream-line"><input class="input input-small" data-inline-field="upstream_model" value="${upstreamValue}" required></div></div>`;
    }
    return `<div class="model-identity"><div class="model-alias-line"><span class="model-alias-copy"><button class="copy-text mono" type="button" data-copy="${aliasValue}" title="${escapeHtml(t('common.copy'))}">${aliasValue}</button></span></div><div class="model-upstream-line"><span class="model-upstream-label">${escapeHtml(t('model.upstreamModel'))}</span><span class="model-upstream-copy"><button class="copy-text mono" type="button" data-copy="${upstreamValue}" title="${escapeHtml(t('common.copy'))}">${upstreamValue}</button></span></div></div>`;
  }

  function metadataCell(model) {
    const meta = modelMetadata(model);
    const typeText = normalizeModelType(meta.kind);
    const pricingText = pricingDetails(meta.pricing);
    const pricingNode = pricingText
      ? `<span class="model-meta-pricing">${escapeHtml(pricingText)}</span>`
      : `<span class="model-meta-pricing model-meta-pricing-muted">${escapeHtml(t('model.priceUnknown'))}</span>`;
    return `<div class="model-meta"><div class="model-meta-line"><span class="badge ${typeText === 'text' ? 'badge-green' : (typeText === 'image' ? 'badge-warn' : '')}">${escapeHtml(typeText)}</span>${pricingNode}</div><div class="model-meta-line model-meta-caps">${functionalityBadges(model)}</div></div>`;
  }

  function row(model) {
    const key = escapeHtml(modelKey(model));
    const isEditing = editingAlias === modelKey(model);
    const identityCell = modelIdentityCell(model, isEditing);
    const metadataCellHtml = modelMetadataBlock(model);
    const enabledCell = `<label class="switch" title="${escapeHtml(t('config.enabled'))}">
      <input type="checkbox" data-inline-enabled="${key}" ${model.enabled ? 'checked' : ''}>
      <span class="switch-track" aria-hidden="true"><span class="switch-thumb"></span></span>
      <span class="switch-label">${model.enabled ? t('common.yes') : t('common.no')}</span>
    </label>`;
    return `<tr class="model-map-row" data-model-key="${key}">
      <td class="model-map-primary">${identityCell}</td>
      <td class="model-map-meta">${metadataCellHtml}</td>
      <td class="model-map-state">${enabledCell}</td>
      <td class="model-map-actions">
        <div class="row-actions model-row-actions">
          <button class="btn btn-small" data-edit="${key}">${isEditing ? t('common.save') : t('config.edit')}</button>
          <button class="btn btn-small btn-danger" data-del="${key}">${t('config.delete')}</button>
        </div>
      </td>
    </tr>`;
  }

  async function loadModels() {
    try {
      const data = await adminFetch('/admin/models');
      models = normalizeList(data).map(enrichModel);
      tbody.innerHTML = models.length ? models.map(row).join('') : `<tr><td colspan="4" class="empty">${t('config.empty')}</td></tr>`;
    } catch (e) { showToast(e.message || t('config.loadFailed'), 'error'); tbody.innerHTML = `<tr><td colspan="4" class="empty">${t('config.loadFailed')}</td></tr>`; }
  }

  function functionalityBadges(item) {
    const f = item.supported_functionality || {};
    const rawKind = String(item.kind || item.model_type || item.type || item.model_kind || '');
    const kind = normalizeModelType(rawKind);
    const isVisionKind = kind === 'image' || rawKind.toLowerCase().includes('vision');
    const badges = [];
    const firstKnownBool = values => {
      if (values.some(value => value === true)) return true;
      if (values.some(value => value === false)) return false;
      return null;
    };
    const pushBoolBadge = (label, value, options = {}) => {
      if (value !== true && value !== false) return;
      if (options.trueOnly && value !== true) return;
      badges.push(capabilityBadge(label, value, options));
    };

    pushBoolBadge(t('config.func.serverless'), firstKnownBool([f.serverless, f.supports_serverless, f.supportsServerless]), { trueOnly: true });

    const contextLength = Number(f.context_length);
    if (Number.isFinite(contextLength) && contextLength > 0) {
      badges.push(capabilityBadge(t('config.func.context'), contextLength, { format: value => `${formatContextTokens(value)} ${t('config.func.tokens')}` }));
    }

    pushBoolBadge(t('config.func.functionCalling'), firstKnownBool([f.function_calling, f.tools, f.supports_tools, f.supportsTools]), { trueOnly: true });

    const imageInput = firstKnownBool([f.image_input, f.supports_image_input, f.supportsImageInput]);
    pushBoolBadge(t('config.func.imageInput'), imageInput, { trueOnly: !isVisionKind });

    pushBoolBadge(t('config.func.fineTuning'), f.fine_tuning, { trueOnly: true });
    pushBoolBadge(t('config.func.embeddings'), f.embeddings, { trueOnly: true });
    pushBoolBadge(t('config.func.rerankers'), f.rerankers, { trueOnly: true });
    return badges.length ? badges.join(' ') : '';
  }

  function renderImportPanel() {
    if (!importPanel) return;
    importPanel.innerHTML = `
      <div class="panel-hd">
        <div>
          <div class="panel-title">${t('config.import.title')}</div>
          <div class="panel-sub">${t('config.import.sub')}</div>
        </div>
        <div class="action-box page-action-box">
          <button class="btn btn-primary action-btn action-btn-primary" id="discoverModelsBtn" type="button">
            <span class="action-icon" aria-hidden="true">↺</span>
            <span>${t('config.import.official')}</span>
          </button>
          <button class="btn btn-ghost action-btn" id="discoverAdvancedBtn" type="button">${t('config.import.advanced')}</button>
        </div>
      </div>
      <div id="catalogHost" class="analysis-list"><div class="empty">${t('config.import.empty')}</div></div>
      <div id="advancedCatalogHost" class="analysis-list" hidden><div class="empty">${t('config.import.advancedHint')}</div></div>`;
    document.getElementById('discoverModelsBtn').addEventListener('click', () => discoverModels('official'));
    document.getElementById('discoverAdvancedBtn').addEventListener('click', () => {
      const host = document.getElementById('advancedCatalogHost');
      if (!host) return;
      const open = host.hidden;
      host.hidden = !open;
      if (open) {
        void discoverModels('inference');
        const accountId = document.getElementById('accountId')?.value?.trim();
        if (accountId) void discoverModels('account');
      }
    });
    document.getElementById('manualAddModelBtnTop')?.addEventListener('click', () => openModal());
    renderCatalog();
  }

  function renderCatalog() {
    const host = document.getElementById('catalogHost');
    const advancedHost = document.getElementById('advancedCatalogHost');
    if (!host) return;
    const officialItems = catalog.filter(item => (item.source || 'official') === 'official');
    const advancedItems = catalog.filter(item => (item.source || 'official') !== 'official');
    if (!officialItems.length) {
      host.innerHTML = `<div class="empty">${t('config.import.empty')}</div>`;
    } else {
      host.innerHTML = officialItems.map((item, idx) => {
        const suggested = item.suggested_alias || (item.aliases || [])[0] || '';
        const aliasList = item.aliases || (suggested ? [suggested] : []);
        const aliases = existingAliasSet();
        const already = item.already_mapped || (aliasList.length > 0 && aliasList.every(a => aliases.has(String(a))));
        const canAdd = suggested && !already;
        const canManualAdd = !suggested && !already && item.upstream_model;
        const priceNode = pricingHtml(item.pricing || item.price || item.cost || item.prices, 'pricing-line');
        return `<div class="catalog-card"><div class="catalog-card-main"><div class="catalog-card-head"><div class="catalog-card-title-block"><div class="catalog-alias">${escapeHtml(aliasList.join(', ') || '—')}</div><div class="catalog-upstream">${escapeHtml(item.upstream_model || '')}</div></div><div class="catalog-card-badges"><span class="badge badge-dark">${escapeHtml(item.kind || 'unknown')}</span><span class="badge ${already ? 'badge-green' : 'badge'}">${already ? t('config.import.added') : (item.recommended ? t('config.import.recommended') : t('config.import.optional'))}</span></div></div><div class="catalog-card-body"><div class="catalog-meta-group"><span class="catalog-meta-label">${escapeHtml(t('config.import.aliases'))}</span><div class="catalog-alias-list">${escapeHtml(aliasList.join(', ') || '—')}</div></div>${priceNode ? `<div class="catalog-meta-group"><span class="catalog-meta-label">${escapeHtml(t('model.pricing'))}</span><div class="catalog-price">${priceNode}</div></div>` : ''}<div class="catalog-caps">${functionalityBadges(item)}</div></div></div><div class="catalog-card-side"><div class="catalog-card-status">${already ? t('config.import.added') : (item.recommended ? t('config.import.recommended') : t('config.import.optional'))}</div><div class="catalog-card-actions">${canAdd ? `<button class="btn btn-small btn-primary" data-import-source="official" data-import-idx="${idx}">${t('config.import.add')}</button>` : `<button class="btn btn-small ${canManualAdd ? '' : 'btn-primary'}" ${canManualAdd ? `data-manual-import-source="official" data-manual-import-idx="${idx}"` : 'disabled'}>${already ? t('config.import.added') : t('config.import.manualAlias')}</button>`}</div></div></div>`;
      }).join('');
    }
    if (advancedHost) {
      advancedHost.innerHTML = !advancedItems.length ? `<div class="empty">${t('config.import.advancedHint')}</div>` : advancedItems.map((item, idx) => { const priceNode = pricingHtml(item.pricing || item.price || item.cost || item.prices, 'pricing-line'); return `<div class="catalog-card catalog-card-advanced"><div class="catalog-card-main"><div class="catalog-card-head"><div class="catalog-card-title-block"><div class="catalog-alias">${escapeHtml((item.aliases || []).join(', ') || '—')}</div><div class="catalog-upstream">${escapeHtml(item.upstream_model || '')}</div></div><div class="catalog-card-badges"><span class="badge">${escapeHtml(item.source || 'inference')}</span></div></div><div class="catalog-card-body"><div class="catalog-meta-group"><span class="catalog-meta-label">${escapeHtml(t('config.import.aliases'))}</span><div class="catalog-alias-list">${escapeHtml((item.aliases || []).join(', ') || '—')}</div></div>${priceNode ? `<div class="catalog-meta-group"><span class="catalog-meta-label">${escapeHtml(t('model.pricing'))}</span><div class="catalog-price">${priceNode}</div></div>` : ''}<div class="catalog-caps">${functionalityBadges(item)}</div></div></div><div class="catalog-card-side"><div class="catalog-card-status">${escapeHtml(item.source || 'inference')}</div><div class="catalog-card-actions"><button class="btn btn-small" data-manual-import-source="inference" data-manual-import-idx="${idx}">${t('config.import.manualAlias')}</button></div></div></div>`; }).join('');
    }
  }

  async function discoverModels(source = 'official', options = {}) {
    try {
      const accountId = document.getElementById('accountId')?.value?.trim();
      const url = source === 'official' ? '/admin/fireworks/models?source=official' : source === 'account' ? `/admin/fireworks/models?source=account${accountId ? `&account_id=${encodeURIComponent(accountId)}` : ''}` : '/admin/fireworks/models?source=inference';
      const data = await adminFetch(url);
      const items = normalizeList(data).map(item => ({ ...item, source }));
      catalog = catalog.filter(item => (item.source || 'official') !== source).concat(items);
      models = models.map(enrichModel);
      tbody.innerHTML = models.length ? models.map(row).join('') : `<tr><td colspan="4" class="empty">${t('config.empty')}</td></tr>`;
      renderCatalog();
      const advancedHost = document.getElementById('advancedCatalogHost');
      if (advancedHost && source !== 'official') advancedHost.hidden = false;
      if (!options.silent) showToast(t('config.import.loaded'), 'success');
    } catch (e) {
      if (e.status === 404) return showToast(t('config.import.notFound'), 'error');
      showToast(e.message || t('config.import.failed'), 'error');
    }
  }

  async function importCatalogItem(item) {
    const aliases = item.aliases && item.aliases.length ? item.aliases : [item.suggested_alias].filter(Boolean);
    if (!item.upstream_model || !aliases.length) {
      openModal({ upstream_model: item.upstream_model || '', enabled: true }, { mode: 'add' });
      if (!aliases.length) showToast(t('config.import.noAliasGuidance'), 'info');
      return;
    }
    try {
      await adminFetch('/admin/models/import', {
        method: 'POST',
        body: JSON.stringify({ upstream_model: item.upstream_model, aliases, enabled: true }),
      });
      showToast(t('config.import.success'), 'success');
      await loadModels();
      catalog = catalog.map(model => model.upstream_model === item.upstream_model ? { ...model, already_mapped: true, missing_aliases: [] } : model);
      renderCatalog();
    } catch (e) {
      showToast(e.message || t('config.import.failed'), 'error');
    }
  }

  function renderPanels() {
    const host = document.getElementById('configPanels');
    if (!host) return;
    host.innerHTML = [
      ['config.security.title', 'config.security.sub', 'config.security.body'],
      ['config.cost.title', 'config.cost.sub', 'config.cost.body'],
      ['config.quota.title', 'config.quota.sub', 'config.quota.body'],
    ].map(([title, sub, body]) => `<section class="panel"><div class="panel-hd"><div><div class="panel-title">${t(title)}</div><div class="panel-sub">${t(sub)}</div></div></div><div class="panel-note">${t(body)}</div></section>`).join('');
  }

  async function saveModel(payload, isEdit, originalAlias = null) {
    const method = isEdit ? 'PATCH' : 'POST';
    const path = isEdit ? `/admin/models/${encodeURIComponent(originalAlias || payload.alias)}` : '/admin/models';
    await adminFetch(path, { method, body: JSON.stringify(payload) });
    showToast(t('config.saveSuccess'), 'success');
    closeModal();
    await loadModels();
  }

  async function saveInlineModel(originalAlias) {
    const tr = Array.from(tbody.querySelectorAll('tr[data-model-key]')).find(row => row.dataset.modelKey === originalAlias);
    if (!tr) return;
    const alias = tr.querySelector('[data-inline-field="alias"]')?.value.trim();
    const upstream_model = tr.querySelector('[data-inline-field="upstream_model"]')?.value.trim();
    const current = models.find(m => modelKey(m) === originalAlias);
    if (!alias || !upstream_model || !current) return;
    try {
      await adminFetch(`/admin/models/${encodeURIComponent(originalAlias)}`, {
        method: 'PATCH',
        body: JSON.stringify({ alias, upstream_model, enabled: !!current.enabled }),
      });
      showToast(t('config.saveSuccess'), 'success');
      editingAlias = null;
      await loadModels();
    } catch (err) {
      showToast(err.message || t('config.saveFailed'), 'error');
    }
  }

  async function patchEnabled(alias, enabled) {
    const current = models.find(m => modelKey(m) === alias);
    if (!current) return;
    try {
      await adminFetch(`/admin/models/${encodeURIComponent(alias)}`, {
        method: 'PATCH',
        body: JSON.stringify({ alias: current.alias ?? alias, upstream_model: current.upstream_model ?? '', enabled }),
      });
      await loadModels();
    } catch (e) {
      showToast(e.message || t('config.saveFailed'), 'error');
      await loadModels();
    }
  }

  async function deleteModel(id) {
    try { await adminFetch(`/admin/models/${encodeURIComponent(id)}`, { method: 'DELETE' }); showToast(t('config.deleteSuccess'), 'success'); await loadModels(); }
    catch (e) { showToast(e.message || t('config.loadFailed'), 'error'); }
  }

  async function copyText(value) {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const textarea = document.createElement('textarea');
        textarea.value = value;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
      }
      showToast(t('common.copied'), 'success');
    } catch (e) {
      showToast(e.message || t('common.copyFailed'), 'error');
    }
  }

  document.getElementById('closeModalBtn').onclick = closeModal;
  document.getElementById('cancelModalBtn').onclick = closeModal;
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  tbody.addEventListener('click', e => {
    const copy = e.target.closest('[data-copy]');
    if (copy) { copyText(copy.dataset.copy || ''); return; }
    const edit = e.target.closest('[data-edit]'); const del = e.target.closest('[data-del]');
    if (edit) {
      const key = edit.dataset.edit;
      if (editingAlias === key) { saveInlineModel(key); return; }
      editingAlias = key;
      void loadModels();
      return;
    }
    if (del) deleteModel(del.dataset.del);
  });
  tbody.addEventListener('change', e => {
    const enabled = e.target.closest('[data-inline-enabled]');
    if (!enabled) return;
    patchEnabled(enabled.dataset.inlineEnabled, enabled.checked);
  });
  importPanel?.addEventListener('click', e => {
    const btn = e.target.closest('[data-import-idx]');
    if (btn) {
      const source = btn.dataset.importSource || 'official';
      const item = catalog.filter(model => (model.source || 'official') === source)[Number(btn.dataset.importIdx)];
      if (item) importCatalogItem(item);
      return;
    }
    const manualBtn = e.target.closest('[data-manual-import-idx]');
    if (!manualBtn) return;
    const source = manualBtn.dataset.manualImportSource || 'official';
    const item = catalog.filter(model => (model.source || 'official') === source)[Number(manualBtn.dataset.manualImportIdx)];
    if (item?.upstream_model) openModal({ upstream_model: item.upstream_model, enabled: true }, { mode: 'add' });
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const payload = {
      alias: fields.alias.value.trim(),
      upstream_model: fields.upstream_model.value.trim(),
      enabled: fields.enabled?.checked ?? true,
    };
    const isEdit = !!fields.modelId.value;
    try {
      await saveModel(payload, isEdit, fields.modelId.value || null);
    } catch (err) { showToast(err.message || t('config.saveFailed'), 'error'); }
  });

  await loadModels();
  renderImportPanel();
  await discoverModels('official', { silent: true });
  renderPanels();
})();
