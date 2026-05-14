const ADMIN_TOKEN_KEY = 'fireworks2api_admin_token';

function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || '';
}

function setAdminToken(token) {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
  else localStorage.removeItem(ADMIN_TOKEN_KEY);
}

function adminHeaders(extra = {}) {
  const token = getAdminToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

async function adminFetch(path, options = {}) {
  const headers = adminHeaders(options.body ? { 'Content-Type': 'application/json' } : {});
  const response = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  if (response.status === 401 || response.status === 403) {
    if (!location.pathname.endsWith('/admin/login')) location.href = '/admin/login';
    throw new Error('unauthorized');
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!response.ok) {
    const detail = data?.detail || data?.error?.message || response.statusText;
    throw httpError(response.status, detail, data);
  }
  return data;
}

async function verifyAdminToken(token) {
  const response = await fetch('/admin/overview', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  return response.ok;
}

async function requireAdminToken() {
  const token = getAdminToken();
  if (!token) {
    location.href = '/admin/login';
    throw new Error('missing admin token');
  }
  return token;
}

function adminLogout() {
  setAdminToken('');
  location.href = '/admin/login';
}

window.getAdminToken = getAdminToken;
window.setAdminToken = setAdminToken;
window.adminHeaders = adminHeaders;
window.adminFetch = adminFetch;
window.verifyAdminToken = verifyAdminToken;
window.requireAdminToken = requireAdminToken;
window.adminLogout = adminLogout;
